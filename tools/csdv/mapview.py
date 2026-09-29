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
from mapgeo import GeometryError

LEVEL_BIN = 16.0
LEVEL_MERGE = 72.0          # floors closer than this belong to one level (steps, ramps)
MIN_LEVEL_AREA = 250_000.0  # units^2 of floor (~500 x 500) to count as a level
KNEE, HEAD = 18.0, 72.0     # wall band above a level's floor
DOOR_HALF = 34.0            # openings narrower than ~2x this split spaces
NOOK_AREA = 160.0 * 160.0
LANDMARK_WORDS = ("exit", "sign", "door", "stair", "elevator", "lift", "window", "ladder", "vent", "shutter",
                  "button", "statue", "laptop", "computer", "screen", "tv", "clock", "painting", "picture",
                  "desk", "counter", "bench", "chair", "books", "cup")
SCENERY_WORDS = ("skybox", "tree", "car", "truck", "bus", "police", "bush", "plant", "breakable", "_piece")  # outside dressing: not drawn
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
        if "handle" in base.lower():  # door handles are separate props on the doors already marked
            continue
        if cls.startswith(("prop_door", "func_door")):
            if "handle" in base.lower():  # the handle is a separate prop on the same door
                continue
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
    # signs/posters read from head-on renders (signs.py scan + name); one landmark per placed copy
    visual = mapgeo.MAPS_DIR / map_name / "landmarks-visual.json"
    if visual.is_file():
        for m in json.loads(visual.read_text(encoding="utf-8")):
            for pos in m.get("instances") or [m["pos"]]:
                marks.append({"kind": "sign", "name": m["name"], "pos": list(pos)})
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


GRID_CELL = 256.0          # grid square size (world units); the same grid on every floor
ROUTE_COLOURS = ("#1f6fd1", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b")


def col_name(i: int) -> str:
    if i < 0:
        return "?"   # west of the mapped area (e.g. outdoor scenery); a negative index never terminates below
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(ord("A") + r) + name
    return name


class Grid:
    """Map-wide lettered grid: columns A, B, C... west to east, rows 1, 2, 3... north to south."""

    def __init__(self, lo, hi, cell: float = GRID_CELL):
        self.cell = cell
        self.x0 = math.floor(lo[0] / cell) * cell
        self.ytop = math.ceil(hi[1] / cell) * cell

    def square(self, x: float, y: float) -> str:
        col, row = int((x - self.x0) // self.cell), int((self.ytop - y) // self.cell) + 1
        return "off-grid" if col < 0 or row < 1 else f"{col_name(col)}{row}"


def _fmt(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _prepare_level(g, z, letter, nz, tri_zmin, tri_zmax, routes, still, res, rng):
    floor_tris = g.tri[(nz > 0.7) & (np.abs(g.tri[:, :, 2].mean(axis=1) - z) < LEVEL_MERGE / 2)]
    if not len(floor_tris):
        return None
    lo = floor_tris[:, :, :2].reshape(-1, 2).min(axis=0) - 96
    hi = floor_tris[:, :, :2].reshape(-1, 2).max(axis=0) + 96
    if routes:  # crop to where the players go on this level
        on = np.concatenate([p[np.abs(p[:, 2] - z) < 60, :2] for _, p in routes.values()])
        if len(on):
            lo = np.maximum(lo, on.min(axis=0) - CROP_MARGIN)
            hi = np.minimum(hi, on.max(axis=0) + CROP_MARGIN)
    if (hi - lo).min() < 64:
        return None
    R = Raster(lo, hi, res)
    floor = R.draw(floor_tris[:, :, :2])
    band = (tri_zmax > z + KNEE) & (tri_zmin < z + HEAD) & (np.abs(nz) < 0.7)
    wall_tris = g.tri[band]
    inside = (wall_tris[:, :, 0].max(axis=1) >= lo[0]) & (wall_tris[:, :, 0].min(axis=1) <= hi[0]) & \
             (wall_tris[:, :, 1].max(axis=1) >= lo[1]) & (wall_tris[:, :, 1].min(axis=1) <= hi[1])
    walls = R.draw(wall_tris[inside][:, :, :2], width=2)
    free = floor & ~ndimage.binary_dilation(walls, iterations=1)
    if free.sum() * res * res < MIN_LEVEL_AREA / 2:
        return None
    labels, dt = segment(free, res)
    keep_px = set()
    for sp in still or []:
        if abs(sp["pos"][2] - z) < 60:
            c, r = R.px(sp["pos"][:2])[0]
            if 0 <= int(r) < R.h and 0 <= int(c) < R.w and labels[int(r), int(c)]:
                keep_px.add(int(labels[int(r), int(c)]))
    labels = merge_small(labels, res, keep_px)
    ids = [i for i in np.unique(labels) if i]
    centres = {i: ndimage.center_of_mass(labels == i) for i in ids}
    order = sorted(ids, key=lambda i: (round(centres[i][1] / (400 / res)), centres[i][0]))
    level = Level(letter, float(z), float(floor.sum() * res * res))
    img = np.ones((R.h, R.w, 3)) * 0.95
    img[floor] = [0.86, 0.86, 0.86]
    id_of_label = np.zeros(labels.max() + 1, dtype=object)
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
        level.spaces.append(Space(sid, letter, float(z), kind, [round(wx), round(wy), round(float(z))],
                                  [round(x0), round(y0), round(x1), round(y1)], round(area), [round(lx), round(ly)]))
        id_of_label[i] = sid
        base = {"room": (0.62, 0.78, 0.95), "corridor": (0.72, 0.9, 0.72), "nook": (1.0, 0.78, 0.5)}[kind]
        img[mask] = np.clip(np.array(base) + rng.uniform(-0.06, 0.06, 3), 0, 1)
    border = np.zeros_like(labels, bool)
    border[:, 1:] |= (labels[:, 1:] != labels[:, :-1]) & (labels[:, 1:] > 0) & (labels[:, :-1] > 0)
    border[1:, :] |= (labels[1:, :] != labels[:-1, :]) & (labels[1:, :] > 0) & (labels[:-1, :] > 0)
    img[ndimage.binary_dilation(border)] = [0.4, 0.4, 0.5]
    img[walls] = [0.08, 0.08, 0.08]
    return {"letter": letter, "z": float(z), "R": R, "lo": lo, "hi": hi, "img": img, "labels": labels,
            "id_of_label": id_of_label, "level": level}


def space_at(levels: list, x: float, y: float, z: float):
    """(floor letter, space id) for a world position, or (None, None)."""
    best = None
    for lv in levels:
        if abs(z - lv["z"]) < 72 and (best is None or abs(z - lv["z"]) < abs(z - best["z"])):
            best = lv
    if best is None:
        return None, None
    c, r = best["R"].px([x, y])[0]
    r, c = int(round(r)), int(round(c))
    if 0 <= r < best["R"].h and 0 <= c < best["R"].w:
        lab = best["labels"][r, c]
        return best["letter"], (best["id_of_label"][lab] or None) if lab else None
    return best["letter"], None


def build_overview(map_name: str, res: float = 4.0, routes: dict | None = None, still: list | None = None,
                   out_dir: Path | None = None, title_note: str = "", zones: list | None = None,
                   tickrate: float = 64.0) -> dict:
    """High-res labelled floor maps for directing (one PNG per floor the players use) + <map>-spaces.json.

    routes: {label: (ticks, positions (N,3))}; still: [{name, start, end, pos}];
    zones: [{"id", "label", "center", "radius"|"polygon"}] drawn as hidden zones."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Polygon

    g = mapgeo.MapGeometry.for_map(map_name)
    out_dir = out_dir or (mapgeo.MAPS_DIR / map_name / "overview")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{map_name}-*.png"):
        old.unlink()
    visited = np.concatenate([p[:, 2] for _, p in (routes or {}).values()]) if routes else None
    zs = find_levels(g, visited)
    if visited is not None:
        zs = [z for z in zs if np.sum(np.abs(visited - z) < 60) > 5 * 64]
    nz, _ = _tri_normals(g)
    marks = landmarks(map_name, g)
    tri_zmin, tri_zmax = g.tri[:, :, 2].min(axis=1), g.tri[:, :, 2].max(axis=1)
    rng = np.random.default_rng(3)

    levels = []
    for z in sorted(zs):
        lv = _prepare_level(g, z, chr(ord("A") + len(levels)), nz, tri_zmin, tri_zmax, routes, still, res, rng)
        if lv:
            levels.append(lv)
    if not levels:
        raise GeometryError("No floor levels with player movement found.")
    grid = Grid(np.min([lv["lo"] for lv in levels], axis=0), np.max([lv["hi"] for lv in levels], axis=0))

    # landmarks: de-duplicated, numbered L1.. across the whole map (west->east, north->south)
    kept = []
    for m in sorted(marks, key=lambda m: (-(m["pos"][1] // 512), m["pos"][0])):
        near = lambda k: np.linalg.norm(np.subtract(k["pos"], m["pos"])) < 48  # noqa: E731
        if not any(near(k) and (k["name"] == m["name"] or k["kind"] == m["kind"] == "door") for k in kept):
            kept.append(m)
    for n, m in enumerate(kept, start=1):
        m["id"] = f"L{n}"
        m["square"] = grid.square(*m["pos"][:2])

    result = {"map": map_name, "resolution": res, "grid": {"cell": grid.cell, "x0": grid.x0, "ytop": grid.ytop},
              "levels": [], "spaces": [], "images": [], "landmarks": [], "zones": zones or []}
    player_names = list((routes or {}).keys())
    for lv in levels:
        R, lo, hi, z, letter = lv["R"], lv["lo"], lv["hi"], lv["z"], lv["letter"]
        width_in = 20.0
        map_h = width_in * 0.76 * R.h / R.w
        fig = plt.figure(figsize=(width_in, max(map_h + 1.2, 9.0)), dpi=200)
        ax = fig.add_axes([0.025, 0.05, 0.66, 0.88])
        side = fig.add_axes([0.705, 0.05, 0.29, 0.88])
        side.axis("off")
        ext = (lo[0], lo[0] + R.w * res, lo[1], lo[1] + R.h * res)
        ax.imshow(lv["img"], extent=ext, interpolation="nearest")
        # grid
        gx = np.arange(grid.x0, ext[1] + grid.cell, grid.cell)
        gy = np.arange(grid.ytop, ext[2] - grid.cell, -grid.cell)
        for x in gx:
            ax.axvline(x, color="#223", lw=0.35, alpha=0.35, zorder=2)
        for y in gy:
            ax.axhline(y, color="#223", lw=0.35, alpha=0.35, zorder=2)
        cx = [x + grid.cell / 2 for x in gx if ext[0] <= x + grid.cell / 2 <= ext[1]]
        cy = [y - grid.cell / 2 for y in gy if ext[2] <= y - grid.cell / 2 <= ext[3]]
        ax.set_xticks(cx, [col_name(int((x - grid.x0) // grid.cell)) for x in cx], fontsize=8)
        ax.set_yticks(cy, [str(int((grid.ytop - y) // grid.cell) + 1) for y in cy], fontsize=8)
        ax.tick_params(top=True, labeltop=True, right=True, labelright=True, length=0)
        ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
        # spaces
        for s in lv["level"].spaces:
            ax.text(*s.label_xy, s.id, ha="center", va="center", fontsize=9 if s.type != "nook" else 7.5, weight="bold",
                    color="#111", zorder=6, bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#667", lw=0.5, alpha=0.85))
        # routes: line, direction arrows every 5 s, time labels every 15 s, start/end
        for (name, (ticks, pos)), colour in zip((routes or {}).items(), ROUTE_COLOURS):
            on = np.abs(pos[:, 2] - z) < 60
            if on.sum() < 32:
                continue
            ax.plot(np.where(on, pos[:, 0], np.nan), np.where(on, pos[:, 1], np.nan), c=colour, lw=1.6, alpha=0.85,
                    zorder=7, label=name)
            step = int(5 * tickrate)
            for k in range(0, len(ticks) - step // 5, step):
                k2 = min(k + int(0.5 * tickrate), len(ticks) - 1)
                if on[k] and on[k2] and np.linalg.norm(pos[k2, :2] - pos[k, :2]) > 24:
                    ax.annotate("", xy=pos[k2, :2], xytext=pos[k, :2], zorder=8,
                                arrowprops=dict(arrowstyle="-|>", color=colour, lw=1.2, mutation_scale=11))
            for k in range(0, len(ticks), int(15 * tickrate)):
                if on[k]:
                    ax.scatter(*pos[k, :2], s=14, c=colour, zorder=9, edgecolors="white", linewidths=0.5)
                    ax.annotate(_fmt(ticks[k] / tickrate), pos[k, :2], xytext=(4, -9), textcoords="offset points",
                                fontsize=6.5, color=colour, weight="bold", zorder=9,
                                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.7))
            for k, word in ((np.flatnonzero(on)[0], "start"), (np.flatnonzero(on)[-1], "end")):
                ax.scatter(*pos[k, :2], s=70, marker="o" if word == "start" else "X", c=colour, edgecolors="black",
                           zorder=10)
        # still spots
        for s in still or []:
            if abs(s["pos"][2] - z) < 60:
                ax.scatter(s["pos"][0], s["pos"][1], marker="*", s=260, c="gold", edgecolors="black", zorder=11)
                ax.annotate(f"{s['name']} still {_fmt(s['start'] / tickrate)}–{_fmt(s['end'] / tickrate)}",
                            (s["pos"][0], s["pos"][1]), xytext=(9, 7), textcoords="offset points", fontsize=7.5,
                            weight="bold", zorder=11, bbox=dict(boxstyle="round,pad=0.15", fc="#fff6c8", ec="#aa8"))
        # landmarks: numbered markers
        # signs hang on this floor's walls (centre 0-150 u above the floor); other marks keep the looser band
        here = [m for m in kept if (-16 <= m["pos"][2] - z <= 150 if m["kind"] == "sign" else abs(m["pos"][2] - z) <= 170)
                and lo[0] <= m["pos"][0] <= hi[0] and lo[1] <= m["pos"][1] <= hi[1]]
        for m in here:
            colour = {"door": "#8b4513", "exit": "#0a8f2a", "button": "#c00000", "sign": "#0b7fa8"}.get(m["kind"], "#6a3d9a")
            marker = {"door": "s", "exit": "^", "sign": "P"}.get(m["kind"], "D")
            ax.scatter(m["pos"][0], m["pos"][1], marker=marker, s=90 if m["kind"] == "exit" else 45, c=colour,
                       edgecolors="white", linewidths=0.8, zorder=12)
            ax.annotate(m["id"], (m["pos"][0], m["pos"][1]), xytext=(5, 5), textcoords="offset points", fontsize=7.5,
                        weight="bold", color=colour, zorder=12)
        # hidden zones
        for zone in zones or []:
            if abs(zone["center"][2] - z) < 60:
                if zone.get("polygon"):
                    ax.add_patch(Polygon(zone["polygon"], closed=True, fc=(0.9, 0.1, 0.1, 0.3), ec="darkred", lw=2,
                                         ls="--", zorder=10))
                else:
                    ax.add_patch(Circle(zone["center"][:2], zone.get("radius", 32) + 24, fc=(0.9, 0.1, 0.1, 0.25),
                                        ec="darkred", lw=2, ls="--", zorder=10))
                ax.annotate(f"{zone['id']}: {zone.get('label', 'hidden zone')}", zone["center"][:2], xytext=(-10, -24),
                            textcoords="offset points", fontsize=8.5, weight="bold", color="darkred", zorder=12,
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="darkred"))
        ax.set_title(f"{map_name} — floor {letter} (z ≈ {z:.0f}){title_note}", fontsize=15, weight="bold", pad=22)
        # side legend
        key = [("KEY", "bold", 11)]
        for name, colour in zip(player_names, ROUTE_COLOURS):
            key.append((f"━ {name[:34]}", colour, 8.5))
        key += [("● start   ✕ end   ➤ direction (every 5 s)", "#333", 8), ("m:ss labels every 15 s", "#333", 8),
                ("★ stood still ≥ 6 s    ▲ EXIT sign", "#333", 8), ("■ door   ◆ object/prop   ✚ sign/poster", "#333", 8),
                ("L# = landmark (listed below)", "#333", 8),
                ("blue room · green corridor · orange nook", "#333", 8),
                (f"grid square = {grid.cell:.0f} u (≈ {grid.cell * 0.0254:.1f} m)", "#333", 8)]
        marks_col = [("LANDMARKS", "bold", 10.5)]
        for m in here:
            label = "EXIT sign" if m["kind"] == "exit" else m["name"].replace("_", " ")
            marks_col.append((f"{m['id']:<5}{label[:28]:<28}{m['square']:>5}", "#222", 7))
        spaces_col = [("SPACES", "bold", 10.5)]
        for s in lv["level"].spaces:
            spaces_col.append((f"{s.id:<5}{s.type:<9}{grid.square(*s.label_xy):>4}", "#222", 7))
        line_h = lambda size: (size + 3.2) / (fig.get_size_inches()[1] * 72 * 0.88)  # noqa: E731

        def draw(column, x, y):
            for text, colour, size in column:
                side.text(x, y, text, fontsize=size, family="monospace", weight="bold" if colour == "bold" else "normal",
                          color="#111" if colour == "bold" else colour, va="top", transform=side.transAxes)
                y -= line_h(size)
            return y

        top = draw(key, 0.0, 1.0) - 0.02
        # landmarks left, spaces right; overflow continues in a narrower third block under the key
        max_rows = int((top - 0.0) / line_h(7)) - 1
        draw(marks_col[: max_rows + 1], 0.0, top)
        draw(spaces_col[: max_rows + 1], 0.60, top)
        if len(marks_col) > max_rows + 1 or len(spaces_col) > max_rows + 1:
            side.text(0.0, 0.0, "(more in <map>-spaces.json)", fontsize=7, color="#a00", transform=side.transAxes)
        path = out_dir / f"{map_name}-{letter}.png"
        fig.savefig(path)
        plt.close(fig)
        result["images"].append(str(path))
        result["levels"].append({"letter": letter, "z": z, "floor_area": round(lv["level"].floor_area)})
        result["spaces"] += [dict(s.__dict__, square=grid.square(*s.label_xy)) for s in lv["level"].spaces]
    result["landmarks"] = [{k: m[k] for k in ("id", "kind", "name", "pos", "square")} for m in kept]
    (out_dir / f"{map_name}-spaces.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    result["_levels"] = levels
    result["_grid"] = grid
    return result


def movement_table(result: dict, tracks: dict, start: int, end: int, every: float = 5.0, tickrate: float = 64.0) -> str:
    """Markdown timeline: every N seconds, where each player is (floor, grid square, space) and what they're doing."""
    levels, grid = result["_levels"], result["_grid"]
    names = [t.name for t in tracks.values()]
    head = "| time | " + " | ".join(names) + " |\n|---|" + "---|" * len(names)
    rows = [head]
    for tick in range(start, end + 1, int(every * tickrate)):
        cells = []
        for t in tracks.values():
            i = min(np.searchsorted(t.tick, tick), len(t.tick) - 1)
            j = min(i + int(tickrate), len(t.tick) - 1)
            p = t.pos[i]
            speed = float(np.linalg.norm(t.pos[j, :2] - p[:2]) / max((t.tick[j] - t.tick[i]) / tickrate, 1e-3))
            state = "still" if speed < 40 else "run" if speed > 180 else "walk"
            if not t.alive[i]:
                state = "dead"
            floor, space = space_at(levels, p[0], p[1], p[2])
            inside = [zone["id"] for zone in result.get("zones", []) if abs(zone["center"][2] - p[2]) < 72
                      and _in_zone(zone, p[:2])]
            cells.append(f"{floor or '?'}·{space or '-'} {grid.square(p[0], p[1])} {state}"
                         + (f" **in {','.join(inside)}**" if inside else ""))
        rows.append(f"| {_fmt(tick / tickrate)} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _in_zone(zone: dict, xy) -> bool:
    if zone.get("polygon"):
        from matplotlib.path import Path as _Path
        return bool(_Path(np.asarray(zone["polygon"])).contains_point(tuple(xy)))
    return float(np.linalg.norm(np.asarray(xy) - np.asarray(zone["center"][:2]))) < float(zone.get("radius", 32))
