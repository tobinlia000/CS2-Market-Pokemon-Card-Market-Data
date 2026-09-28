"""Labelled overhead map of a CS2 map, one image per floor level, for directing shots.

From the collision mesh (world + props, see mapgeo.py):
- floor levels = heights with lots of walkable floor (triangle normals pointing up);
- per level: walkable floor, and walls = anything between knee and head height above that floor;
- spaces = open floor split at doorways / narrow openings (distance-transform cores, grown back), each labelled
  <level letter><number> and typed room / corridor / nook;
- overlays: doors, buttons, notable props, named static detail (exit signs...) from the world export, player routes
  with time marks, and the spots where players stand still.

Writes videos/maps/<map>/overview/<map>-<letter>.png per level + <map>-spaces.json (ids, type, centre, bbox, area)
so specs and requests can refer to spaces by id ("camera in C4, keep N1 hidden").
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

import mapgeo

LEVEL_BIN = 16.0
LEVEL_MERGE = 72.0          # floors closer than this belong to one level (steps, ramps)
MIN_LEVEL_AREA = 250_000.0  # units^2 of floor (~500 x 500) to count as a level
KNEE, HEAD = 18.0, 72.0     # wall band above a level's floor
DOOR_HALF = 34.0            # openings narrower than ~2x this split spaces
NOOK_AREA = 160.0 * 160.0
LANDMARK_WORDS = ("exit", "sign", "door", "stair", "elevator", "lift", "window", "ladder", "vent", "shutter",
                  "button", "statue", "laptop", "computer", "screen", "tv", "clock", "painting", "picture",
                  "desk", "counter", "bench", "chair", "books", "cup")
SCENERY_WORDS = ("skybox", "tree", "car", "truck", "bus", "police", "bush", "plant")  # outside dressing: not drawn
MIN_SPACE = 100.0 * 100.0   # smaller fragments merge into their neighbour (unless someone stood there)
CROP_MARGIN = 500.0


@dataclass
class Space:
    id: str
    level: str
    z: float
    type: str
    centre: list
    bbox: list
    area: float
    label_xy: list


@dataclass
class Level:
    letter: str
    z: float
    floor_area: float
    spaces: list = field(default_factory=list)


def _tri_normals(g: mapgeo.MapGeometry):
    n = np.cross(g.e1, g.e2)
    area = np.linalg.norm(n, axis=1) / 2
    nz = n[:, 2] / np.maximum(2 * area, 1e-9)
    return nz, area


def find_levels(g: mapgeo.MapGeometry, visited_z: np.ndarray | None = None) -> list[float]:
    nz, area = _tri_normals(g)
    walk = nz > 0.7
    z = g.tri[walk, :, 2].mean(axis=1)
    bins = np.arange(z.min() - LEVEL_BIN, z.max() + 2 * LEVEL_BIN, LEVEL_BIN)
    hist, edges = np.histogram(z, bins=bins, weights=area[walk])
    peaks = [(hist[i], (edges[i] + edges[i + 1]) / 2) for i in range(len(hist))
             if hist[i] >= MIN_LEVEL_AREA and hist[i] >= hist[max(i - 1, 0)] and hist[i] >= hist[min(i + 1, len(hist) - 1)]]
    levels: list[float] = []
    for _, zc in sorted(peaks, reverse=True):
        if all(abs(zc - l) > LEVEL_MERGE for l in levels):
            levels.append(zc)
    if visited_z is not None and len(visited_z):  # every height the players actually stood on gets a level
        for zc in np.unique(np.round(visited_z / LEVEL_BIN) * LEVEL_BIN):
            if all(abs(zc - l) > LEVEL_MERGE for l in levels) and np.sum(np.abs(visited_z - zc) < LEVEL_BIN) > 64:
                levels.append(float(zc))
    return sorted(levels)


class Raster:
    def __init__(self, lo, hi, res):
        self.lo, self.res = np.asarray(lo, float), float(res)
        self.w = int(math.ceil((hi[0] - lo[0]) / res)) + 1
        self.h = int(math.ceil((hi[1] - lo[1]) / res)) + 1

    def px(self, xy):
        xy = np.atleast_2d(xy)
        return np.c_[(xy[:, 0] - self.lo[0]) / self.res, (self.h - 1) - (xy[:, 1] - self.lo[1]) / self.res]

    def world(self, col, row):
        return self.lo[0] + col * self.res, self.lo[1] + (self.h - 1 - row) * self.res

    def draw(self, tris2d, width=0):
        img = Image.new("1", (self.w, self.h), 0)
        d = ImageDraw.Draw(img)
        for t in tris2d:
            pts = [tuple(p) for p in self.px(t)]
            if width:
                d.line(pts + [pts[0]], fill=1, width=width)
            else:
                d.polygon(pts, fill=1, outline=1)
        return np.array(img, bool)


def segment(free: np.ndarray, res: float, min_core_area: float = 64.0 * 64.0):
    dt = ndimage.distance_transform_edt(free) * res
    cores, n = ndimage.label(dt > DOOR_HALF)
    if n == 0:
        return np.zeros_like(free, int), dt
    sizes = ndimage.sum(np.ones_like(dt), cores, index=np.arange(1, n + 1)) * res * res
    keep = np.zeros(n + 1, bool)
    keep[1:] = sizes >= min_core_area
    cores = np.where(keep[cores], cores, 0)
    # grow cores over all free floor (nearest core), so doorways and edges belong to the closest space
    _, (ri, ci) = ndimage.distance_transform_edt(cores == 0, return_indices=True)
    labels = cores[ri, ci] * free
    # small free patches that no core reached closely (nooks/alcoves narrower than a doorway): own labels
    left = free & (ndimage.distance_transform_edt(cores == 0) * res > DOOR_HALF * 2.2)
    extra, m = ndimage.label(left)
    if m:
        labels = np.where(left, extra + labels.max(), labels)
    return labels, dt


def merge_small(labels: np.ndarray, res: float, keep: set) -> np.ndarray:
    """Fold fragments smaller than MIN_SPACE into the neighbouring space they touch most."""
    ids, counts = np.unique(labels[labels > 0], return_counts=True)
    for i, count in sorted(zip(ids, counts), key=lambda x: x[1]):
        if count * res * res >= MIN_SPACE or i in keep:
            continue
        mask = labels == i
        ring = ndimage.binary_dilation(mask, iterations=2) & ~mask & (labels > 0)
        if ring.any():
            neighbours, n = np.unique(labels[ring], return_counts=True)
            labels[mask] = neighbours[np.argmax(n)]
        else:
            labels[mask] = 0
    return labels


def space_type(mask: np.ndarray, res: float) -> str:
    area = mask.sum() * res * res
    rows, cols = np.nonzero(mask)
    if len(rows) < 3:
        return "nook"
    cov = np.cov(np.c_[cols, rows].T)
    ev = np.sort(np.linalg.eigvalsh(cov))
    elong = math.sqrt(ev[1] / max(ev[0], 1e-6))
    if area < NOOK_AREA:
        return "nook"
    if elong > 3.2:
        return "corridor"
    return "room"


def landmarks(map_name: str, g: mapgeo.MapGeometry) -> list[dict]:
    """Named things: doors/buttons/props from the entity lump, plus named static detail from the world export."""
    marks = []
    for e in mapgeo.export_entities(map_name):
        cls = e.get("classname", "")
        if not (cls.startswith(("prop_door", "func_door", "func_button", "prop_dynamic", "prop_physics"))):
            continue
        origin = mapgeo._vec(e.get("origin"))
        base = Path(mapgeo._model_path(e.get("model", ""))).stem if e.get("model") else cls
        if cls.startswith(("prop_door", "func_door")):
            kind = "door"
        elif cls == "func_button":
            kind = "button"
        else:
            words = [w for w in LANDMARK_WORDS if w in base.lower()]
            if not words or any(w in base.lower() for w in SCENERY_WORDS):
                continue
            kind = words[0]
        marks.append({"kind": kind, "name": base, "pos": origin.tolist()})
    for e in mapgeo.export_entities(map_name):
        # Exit signs glow green: a green-dominant light entity sits at each one (totemlake: 6, all at signs;
        # the mesh named "exit_ceiling" is a separate ceiling fixture).
        if e.get("classname", "").startswith("light"):
            col = mapgeo._vec(e.get("color"), (0, 0, 0))
            if col[1] > 60 and col[1] > 1.4 * max(col[0], col[2], 1):
                marks.append({"kind": "exit", "name": "EXIT sign (green light)", "pos": mapgeo._vec(e.get("origin")).tolist()})
    world = mapgeo.MAPS_DIR / map_name / "render" / "world.glb"
    if world.is_file():
        marks += _world_landmarks(world)
    return marks


def _world_landmarks(glb: Path) -> list[dict]:
    """Named world-node meshes (e.g. n0_lr0_agg_merge_exit_ceiling_0) placed via their node matrices, split into
    instances by clustering (an aggregate mesh can hold several copies)."""
    from scipy.cluster.hierarchy import fcluster, linkage

    gltf, acc = mapgeo._read_glb(glb)
    out = []
    for node in gltf["nodes"]:
        name = node.get("name", "")
        low = name.lower()
        words = [w for w in LANDMARK_WORDS if re.search(rf"(^|_){w}", low)]
        if "mesh" not in node or not words or "decal" in low or any(w in low for w in SCENERY_WORDS):
            continue
        M = np.array(node["matrix"]).reshape(4, 4).T if "matrix" in node else np.eye(4)
        V = np.concatenate([acc(p["attributes"]["POSITION"]) for p in gltf["meshes"][node["mesh"]]["primitives"]])
        V = V[:: max(1, len(V) // 2000)].astype(float)
        W = (M @ np.c_[V, np.ones(len(V))].T).T[:, :3]
        S = np.c_[W[:, 0], -W[:, 2], W[:, 1]] / 0.0254       # glTF meters, y-up -> Source units, z-up
        groups = fcluster(linkage(S, "single"), 96, "distance") if len(S) > 1 else np.array([1])
        if "exit" in low:  # named exit meshes are fixtures; the lit signs come from green lights above
            words = ["sign"]
        short = re.sub(r"^n\d+_lr\d+_(agg_merge_)?|_\d+$|_color$", "", Path(name).stem.split(".")[0])
        for gid in np.unique(groups):
            q = S[groups == gid]
            out.append({"kind": words[0], "name": short, "pos": q.mean(axis=0).tolist(), "size": np.ptp(q, axis=0).tolist()})
    return out


def build_overview(map_name: str, res: float = 8.0, routes: dict | None = None, still: list | None = None,
                   out_dir: Path | None = None, title_note: str = "", zones: list | None = None) -> dict:
    """zones: [{"id": "N1", "label": "CT hiding corner", "center": [x, y, z], "radius": r}] drawn as hidden zones."""
    """routes: {player name: (ticks, positions (N,3))}; still: [{name, start, end, pos}] (from positions.still_spots)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    g = mapgeo.MapGeometry.for_map(map_name)
    out_dir = out_dir or (mapgeo.MAPS_DIR / map_name / "overview")
    out_dir.mkdir(parents=True, exist_ok=True)
    visited = np.concatenate([p[:, 2] for _, p in (routes or {}).values()]) if routes else None
    zs = find_levels(g, visited)
    if visited is not None:   # only levels the players actually use
        zs = [z for z in zs if np.sum(np.abs(visited - z) < 60) > 5 * 64]
    nz, _ = _tri_normals(g)
    marks = landmarks(map_name, g)
    tri_zmin, tri_zmax = g.tri[:, :, 2].min(axis=1), g.tri[:, :, 2].max(axis=1)
    result = {"map": map_name, "resolution": res, "levels": [], "spaces": [], "images": []}
    rng = np.random.default_rng(3)

    letter_index = 0
    for z in zs:
        letter = chr(ord("A") + letter_index)
        floor_tris = g.tri[(nz > 0.7) & (np.abs(g.tri[:, :, 2].mean(axis=1) - z) < LEVEL_MERGE / 2)]
        if not len(floor_tris):
            continue
        lo = floor_tris[:, :, :2].reshape(-1, 2).min(axis=0) - 96
        hi = floor_tris[:, :, :2].reshape(-1, 2).max(axis=0) + 96
        if routes:  # crop to where the players go on this level
            on = np.concatenate([p[np.abs(p[:, 2] - z) < 60, :2] for _, p in routes.values()])
            if len(on):
                lo = np.maximum(lo, on.min(axis=0) - CROP_MARGIN)
                hi = np.minimum(hi, on.max(axis=0) + CROP_MARGIN)
        if (hi - lo).min() < 64:  # the players' positions at this height are off this level's floor
            continue
        R = Raster(lo, hi, res)
        floor = R.draw(floor_tris[:, :, :2])
        band = (tri_zmax > z + KNEE) & (tri_zmin < z + HEAD) & (np.abs(nz) < 0.7)
        wall_tris = g.tri[band]
        inside = (wall_tris[:, :, 0].max(axis=1) >= lo[0]) & (wall_tris[:, :, 0].min(axis=1) <= hi[0]) & \
                 (wall_tris[:, :, 1].max(axis=1) >= lo[1]) & (wall_tris[:, :, 1].min(axis=1) <= hi[1])
        walls = R.draw(wall_tris[inside][:, :, :2], width=2)
        free = floor & ~ndimage.binary_dilation(walls, iterations=1)
        if free.sum() * res * res < MIN_LEVEL_AREA / 2:  # a sliver (a ledge, a step): not a level worth a map
            continue
        letter_index += 1
        labels, dt = segment(free, res)
        keep_px = set()
        for sp in still or []:
            if abs(sp["pos"][2] - z) < 60:
                c, r = R.px(sp["pos"][:2])[0]
                if 0 <= int(r) < R.h and 0 <= int(c) < R.w and labels[int(r), int(c)]:
                    keep_px.add(int(labels[int(r), int(c)]))
        labels = merge_small(labels, res, keep_px)
        ids = [i for i in np.unique(labels) if i]
        # stable, readable numbering: west->east, then north->south
        centres = {i: ndimage.center_of_mass(labels == i) for i in ids}
        order = sorted(ids, key=lambda i: (round(centres[i][1] / (400 / res)), centres[i][0]))
        level = Level(letter, float(z), float(floor.sum() * res * res))
        colours = np.zeros((labels.max() + 1, 3))
        img = np.ones((R.h, R.w, 3)) * 0.93
        img[floor] = [0.85, 0.85, 0.85]
        for n, i in enumerate(order, start=1):
            mask = labels == i
            area = float(mask.sum() * res * res)
            if area < 32 * 32:
                continue
            kind = space_type(mask, res)
            sid = f"{letter}{n}"
            peak = np.unravel_index(np.argmax(np.where(mask, dt, -1)), dt.shape)
            lx, ly = R.world(peak[1], peak[0])
            rows, cols = np.nonzero(mask)
            (x0, y1), (x1, y0) = R.world(cols.min(), rows.min()), R.world(cols.max(), rows.max())
            cy, cx = centres[i]
            wx, wy = R.world(cx, cy)
            space = Space(sid, letter, float(z), kind, [round(wx), round(wy), round(float(z))],
                          [round(x0), round(y0), round(x1), round(y1)], round(area), [round(lx), round(ly)])
            level.spaces.append(space)
            base = {"room": (0.55, 0.75, 0.95), "corridor": (0.7, 0.9, 0.7), "nook": (1.0, 0.75, 0.45)}[kind]
            colours[i] = np.clip(np.array(base) + rng.uniform(-0.08, 0.08, 3), 0, 1)
            img[mask] = colours[i]
        # borders between spaces (so you can see where one ends and the next begins)
        border = np.zeros_like(labels, bool)
        border[:, 1:] |= (labels[:, 1:] != labels[:, :-1]) & (labels[:, 1:] > 0) & (labels[:, :-1] > 0)
        border[1:, :] |= (labels[1:, :] != labels[:-1, :]) & (labels[1:, :] > 0) & (labels[:-1, :] > 0)
        img[ndimage.binary_dilation(border)] = [0.35, 0.35, 0.45]
        img[walls] = [0.1, 0.1, 0.1]

        fig_w = 14
        fig, ax = plt.subplots(figsize=(fig_w, fig_w * R.h / R.w + 0.8), dpi=110)
        ax.imshow(img, extent=(lo[0], lo[0] + R.w * res, lo[1], lo[1] + R.h * res), interpolation="nearest")
        for s in level.spaces:
            size = 9 if s.type != "nook" else 8
            ax.text(*s.label_xy, s.id, ha="center", va="center", fontsize=size, weight="bold",
                    color="black", bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75))
        for m in marks:
            if abs(m["pos"][2] - z) > 170 or not (lo[0] <= m["pos"][0] <= hi[0] and lo[1] <= m["pos"][1] <= hi[1]):
                continue
            style = {"door": ("s", "saddlebrown"), "exit": ("^", "green"), "button": ("o", "red")}.get(m["kind"], ("D", "purple"))
            big = m["kind"] == "exit"
            ax.scatter(m["pos"][0], m["pos"][1], marker=style[0], c=style[1], s=160 if big else 30, zorder=8 if big else 5,
                       edgecolors="black" if big else "white")
            label = f"EXIT (z {m['pos'][2]:.0f})" if big else m["name"].replace("_", " ")[:22]
            ax.annotate(label, (m["pos"][0], m["pos"][1]), xytext=(8, -12) if big else (4, 4), textcoords="offset points",
                        fontsize=9 if big else 6, color=style[1], weight="bold" if big else "normal", zorder=9,
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="green") if big else None)
        for (name, (ticks, pos)), colour in zip((routes or {}).items(), ("tab:blue", "tab:red", "tab:green", "tab:orange")):
            on = np.abs(pos[:, 2] - z) < 60
            if on.sum() < 32:
                continue
            seg = np.where(on, pos[:, 0], np.nan), np.where(on, pos[:, 1], np.nan)
            ax.plot(*seg, c=colour, lw=1.0, alpha=0.8, label=name)
            for t in range(0, int(ticks[-1]), 30 * 64):
                k = np.searchsorted(ticks, t)
                if k < len(ticks) and on[k]:
                    ax.annotate(f"{t // 64 // 60}:{t // 64 % 60:02d}", (pos[k, 0], pos[k, 1]), fontsize=6, color=colour,
                                xytext=(2, -8), textcoords="offset points")
                    ax.scatter(pos[k, 0], pos[k, 1], s=8, c=colour, zorder=6)
        for s in still or []:
            if abs(s["pos"][2] - z) < 60:
                ax.scatter(s["pos"][0], s["pos"][1], marker="*", s=160, c="gold", edgecolors="black", zorder=7)
                a, b = s["start"] // 64, s["end"] // 64
                ax.annotate(f"{s['name']} still {a // 60}:{a % 60:02d}-{b // 60}:{b % 60:02d}", (s["pos"][0], s["pos"][1]),
                            xytext=(6, 6), textcoords="offset points", fontsize=7, weight="bold")
        for zone in zones or []:
            if abs(zone["center"][2] - z) < 60:
                from matplotlib.patches import Circle, Polygon
                if zone.get("polygon"):
                    ax.add_patch(Polygon(zone["polygon"], closed=True, fc=(0.9, 0.1, 0.1, 0.3), ec="darkred", lw=2,
                                         ls="--", zorder=7))
                else:
                    ax.add_patch(Circle(zone["center"][:2], zone.get("radius", 32) + 24, fill=True,
                                        fc=(0.9, 0.1, 0.1, 0.25), ec="darkred", lw=2, ls="--", zorder=7))
                ax.annotate(f"{zone['id']}: {zone.get('label', 'hidden zone')}", zone["center"][:2], xytext=(-10, -22),
                            textcoords="offset points", fontsize=9, weight="bold", color="darkred", zorder=9,
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="darkred"))
        ax.set_title(f"{map_name}  level {letter}  (floor z ≈ {z:.0f})  blue = room, green = corridor, orange = nook"
                     f"{title_note}", fontsize=10)
        ax.set_xlabel("x"); ax.set_ylabel("y")
        from matplotlib.ticker import MultipleLocator
        ax.xaxis.set_minor_locator(MultipleLocator(250)); ax.yaxis.set_minor_locator(MultipleLocator(250))
        ax.grid(which="both", color="white", lw=0.4, alpha=0.6)
        if routes and ax.get_legend_handles_labels()[0]:
            ax.legend(loc="upper right", fontsize=7)
        fig.tight_layout()
        path = out_dir / f"{map_name}-{letter}.png"
        fig.savefig(path)
        plt.close(fig)
        result["images"].append(str(path))
        result["levels"].append({"letter": letter, "z": level.z, "floor_area": round(level.floor_area)})
        result["spaces"] += [s.__dict__ for s in level.spaces]
    result["zones"] = zones or []
    result["landmarks"] = [m for m in marks if m["kind"] in ("exit", "door", "button", "stair", "elevator", "sign")]
    (out_dir / f"{map_name}-spaces.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
