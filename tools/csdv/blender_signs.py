"""Runs inside Blender (background): find sign-like objects in a textured map export and render each one head-on.

    blender -b --factory-startup -P blender_signs.py -- <config.json>

config.json: {"gltf": ..., "out": ..., "keywords": [...], "custom": [material names from the map's own VPK],
              "sign_model_materials": [...], "max_size": 700, "voxel": 24}
Writes <out>/<id>.png (one per candidate) and <out>/index.json. Positions are Source units (Blender metres / 0.0254).
"""
import json
import os
import sys
from collections import deque

import bpy
import numpy as np
from mathutils import Matrix, Vector

M2U = 1 / 0.0254
cfg = json.load(open(sys.argv[sys.argv.index("--") + 1]))
os.makedirs(cfg["out"], exist_ok=True)
keywords = [k.lower() for k in cfg["keywords"]]
custom = {m.lower() for m in cfg["custom"]}
generic = {m.lower() for m in cfg["sign_model_materials"]}
exclude = [w.lower() for w in cfg.get("exclude", [])]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=cfg["gltf"])


def show_base_colour(mat):
    """Workbench 'Texture' colour shows the node tree's *active* image node; make that the base-colour image
    (the glTF importer often leaves another texture, e.g. the normal map, active -> flat grey renders)."""
    if not mat or not mat.use_nodes:
        return
    nodes = mat.node_tree.nodes
    for node in nodes:
        if node.type == "BSDF_PRINCIPLED":
            stack = [link.from_node for link in node.inputs["Base Color"].links]
            while stack:
                cur = stack.pop()
                if cur.type == "TEX_IMAGE":
                    nodes.active = cur
                    return
                stack += [link.from_node for inp in cur.inputs for link in inp.links]
    images = [n for n in nodes if n.type == "TEX_IMAGE" and n.image and "normal" not in n.image.name.lower()]
    if images:
        nodes.active = images[0]


for m in bpy.data.materials:
    show_base_colour(m)


def base_name(mat):
    return mat.name.split(".")[0].lower() if mat else ""


def wanted(name):
    """'strong' = keep every triangle (decals, overlays, posters); 'weak' = keep only small triangles (3D letters
    in plain materials that walls also use)."""
    if any(w in name for w in exclude):
        return None
    if any(k in name for k in keywords) or name in custom:
        return "strong"
    if name in generic:
        return "weak"
    return None


# 1. candidate triangles (world space, Source units) with their material
tri_pts, tri_mat = [], []
for ob in bpy.context.scene.objects:
    if ob.type != "MESH":
        continue
    kinds = {i: (base_name(s.material), wanted(base_name(s.material))) for i, s in enumerate(ob.material_slots)}
    if not any(k for _, k in kinds.values()):
        continue
    me = ob.data
    me.calc_loop_triangles()
    n = len(me.loop_triangles)
    vidx = np.empty(n * 3, np.int32); me.loop_triangles.foreach_get("vertices", vidx)
    midx = np.empty(n, np.int32); me.loop_triangles.foreach_get("material_index", midx)
    co = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    W = np.array(ob.matrix_world)
    co = (co @ W[:3, :3].T + W[:3, 3]) * M2U
    T = co[vidx.reshape(-1, 3)]
    for i, (name, kind) in kinds.items():
        if not kind:
            continue
        sel = midx == i
        t = T[sel]
        if kind == "weak":
            edges = np.linalg.norm(t - np.roll(t, 1, axis=1), axis=2).max(axis=1)
            t = t[edges < 64]
        if len(t):
            tri_pts.append(t)
            tri_mat += [name] * len(t)
if not tri_pts:
    json.dump([], open(os.path.join(cfg["out"], "index.json"), "w"))
    print("CANDIDATES 0")
    sys.exit(0)
tri = np.concatenate(tri_pts)
tri_mat = np.array(tri_mat)
cent = tri.mean(axis=1)

# 2. group: triangles are linked through the voxels their vertices, edge midpoints and centre fall in (a big
#    picture can be two triangles 128 u across), and neighbouring occupied voxels are connected (26-neighbourhood)
vox = cfg.get("voxel", 24)
edge = np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2).max(axis=1)
cells = {}
for i in range(len(tri)):
    n = int(np.ceil(edge[i] / (vox * 0.9))) + 1        # barycentric grid finer than a voxel
    a, b = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
    keep = a + b <= n
    u, v = a[keep] / n, b[keep] / n
    pts = tri[i, 0] + u[:, None] * (tri[i, 1] - tri[i, 0]) + v[:, None] * (tri[i, 2] - tri[i, 0])
    for k in {tuple(c) for c in np.floor(pts / vox).astype(int)}:
        cells.setdefault(k, []).append(i)
label, groups = {}, []
for start in cells:
    if start in label:
        continue
    label[start] = len(groups); members = set(); queue = deque([start])
    while queue:
        c = queue.popleft(); members.update(cells[c])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    nb = (c[0] + dx, c[1] + dy, c[2] + dz)
                    if nb in cells and nb not in label:
                        label[nb] = label[start]; queue.append(nb)
    groups.append(np.array(sorted(members)))

# 3. render setup: flat, texture colours, orthographic
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "TEXTURE"
scene.display.shading.background_type = "VIEWPORT"
scene.display.shading.background_color = (0.12, 0.12, 0.12)
scene.render.resolution_x, scene.render.resolution_y = 900, 450
cam_data = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam); scene.camera = cam
cam_data.type = "ORTHO"

index, seen = [], {}
for g in groups:
    t = tri[g]
    V = t.reshape(-1, 3)
    lo, hi = V.min(axis=0), V.max(axis=0)
    size = hi - lo
    if size.max() > cfg.get("max_size", 700) or size.max() < 6:
        continue
    cen = (lo + hi) / 2
    # identical copies (same materials, same shape) are rendered once and listed as instances
    mats, counts = np.unique(tri_mat[g], return_counts=True)
    top = tuple(m for m, _ in sorted(zip(mats, counts), key=lambda p: -p[1])[:4])
    sig = (top, len(g), tuple(sorted(np.round(size / 4).astype(int))))
    if sig in seen:
        seen[sig]["instances"].append([round(float(x)) for x in cen])
        continue
    w, vecs = np.linalg.eigh(np.cov((V - V.mean(axis=0)).T) + np.eye(3) * 1e-6)
    nrm = vecs[:, 0]
    # front = side the faces point to (area-weighted); ambiguous for solid 3D letters -> both sides rendered
    fn = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]).sum(axis=0)
    facing = float(np.dot(fn, nrm) / (np.linalg.norm(fn) + 1e-9))
    sides = [np.sign(facing)] if abs(facing) > 0.3 else [1.0, -1.0]
    extent = np.ptp(V @ vecs[:, 1:], axis=0)
    depth = np.ptp(V @ nrm)
    shots = []
    for s in sides:
        d = nrm * s
        dist_u = 400.0
        pos = (cen + d * dist_u) / M2U
        fwd = Vector(-d)
        up = Vector((0, 0, 1)) if abs(fwd.z) < 0.8 else Vector((0, 1, 0))
        right = fwd.cross(up).normalized(); up2 = right.cross(fwd).normalized()
        cam.matrix_world = Matrix.Translation(Vector(pos)) @ Matrix((right, up2, -fwd)).transposed().to_4x4()
        cam_data.ortho_scale = max(extent.max() * 1.3, 24) / M2U
        cam_data.clip_start = (dist_u - depth / 2 - 6) / M2U
        cam_data.clip_end = (dist_u + depth / 2 + 10) / M2U   # a little of the wall behind for context
        name = f"{len(index):04d}{'ab'[len(shots)] if len(sides) > 1 else ''}"
        scene.render.filepath = os.path.join(cfg["out"], name + ".png")
        bpy.ops.render.render(write_still=True)
        shots.append(name + ".png")
    entry = {"id": len(index), "pos": [round(float(x)) for x in cen], "size": [round(float(x)) for x in size],
             "normal": [round(float(x), 2) for x in nrm * sides[0]], "tris": int(len(g)), "images": shots,
             "materials": list(top), "instances": [[round(float(x)) for x in cen]]}
    index.append(entry)
    seen[sig] = entry
json.dump(index, open(os.path.join(cfg["out"], "index.json"), "w"), indent=1)
print("CANDIDATES", len(index))
