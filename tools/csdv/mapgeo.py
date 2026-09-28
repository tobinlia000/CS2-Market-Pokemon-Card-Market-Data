"""Map collision geometry for camera placement: export with Source2Viewer-CLI, load, and ray-test.

The collision mesh (`maps/<map>/world_physics.vmdl_c`) is exported to glb. Its raw vertex positions are already in
world units with z up, the same space as demo positions and HLAE campaths (the node matrix only converts to glTF
meters/y-up, so it is ignored).

Surface groups (node names / extras.InteractAs):
- solid: blocks camera placement AND line of sight (concrete, wood, metal, ...)
- see-through (glass, passbullets/chain-link): blocks camera placement, not line of sight
- ignored: playerclip/npcclip (invisible walls), grenadeclip, sky
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import subprocess
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPS_DIR = REPO_ROOT / "videos" / "maps"
DEFAULT_CLI = Path(os.environ.get("USERPROFILE", "~")) / "Tools" / "Source2Viewer-CLI" / "Source2Viewer-CLI.exe"
IGNORED_INTERACT = {"playerclip", "npcclip", "csgo_grenadeclip", "sky", "trigger", "water"}
SEE_THROUGH_WORDS = ("glass", "passbullets", "chainlink")
CELL = 128.0  # xy grid cell size for the ray acceleration grid


class GeometryError(RuntimeError):
    pass


# --- Export -----------------------------------------------------------------------------------------------------

def cs2_csgo_dir() -> Path | None:
    for base in (Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")):
        candidate = base / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo"
        if candidate.is_dir():
            return candidate
    return None


def map_vpks(map_name: str) -> list[Path]:
    """VPKs to search for a map's files, most specific first.

    Official maps: [game/csgo/maps/<map>.vpk].
    Workshop maps: steamapps/workshop/content/730/<id>/<id>[_dir].vpk is an *addon* that contains a nested
    maps/<map>.vpk (the map itself) plus the map's custom models. The nested VPK is extracted once to
    videos/maps/<map>/<map>.vpk; returns [nested map vpk, addon vpk]. (`-l -f maps/` lists nothing for these
    addons, so the full listing is searched.)"""
    csgo = cs2_csgo_dir()
    if not csgo:
        raise GeometryError("CS2 install not found.")
    official = csgo / "maps" / f"{map_name}.vpk"
    if official.is_file():
        return [official]
    nested = MAPS_DIR / map_name / f"{map_name}.vpk"
    addon_note = MAPS_DIR / map_name / "addon.txt"
    if nested.is_file() and addon_note.is_file():
        return [nested, Path(addon_note.read_text(encoding="utf-8").strip())]
    workshop = csgo.parents[3] / "workshop" / "content" / "730"
    wanted = f"maps/{map_name}.vpk".lower()
    for addon in sorted(workshop.glob("*/*.vpk")):
        if re.search(r"_\d{3}\.vpk$", addon.name):
            continue  # data chunks; the _dir.vpk or single .vpk holds the listing
        listing = _run_cli(["-i", str(addon), "-l"], check=False).lower()
        if wanted + " " in listing or wanted + "\n" in listing:
            nested.parent.mkdir(parents=True, exist_ok=True)
            _run_cli(["-i", str(addon), "-f", f"maps/{map_name}.vpk", "-o", str(nested)])
            if not nested.is_file():
                raise GeometryError(f"Could not extract maps/{map_name}.vpk from {addon}")
            addon_note.write_text(str(addon), encoding="utf-8")
            return [nested, addon]
    raise GeometryError(f"No VPK found for map '{map_name}' (official maps folder or subscribed workshop maps).")


def find_map_vpk(map_name: str) -> Path:
    return map_vpks(map_name)[0]


def _run_cli(args: list[str], check: bool = True) -> str:
    cli = Path(os.environ.get("S2V_CLI", DEFAULT_CLI))
    if not cli.is_file():
        raise GeometryError(f"Source2Viewer-CLI not found at {cli} (set S2V_CLI).")
    result = subprocess.run([str(cli), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise GeometryError(f"Source2Viewer-CLI failed: {result.stdout[-500:]} {result.stderr[-500:]}")
    return result.stdout


def export_map(map_name: str) -> Path:
    """Export the collision mesh once; returns videos/maps/<map>/<map>_physics.glb."""
    out_dir = MAPS_DIR / map_name
    glb = out_dir / f"{map_name}_physics.glb"
    if glb.is_file():
        return glb
    vpk = find_map_vpk(map_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Windows paths only: an MSYS-style /c/... output path is taken literally (it wrote to C:\c\...).
    _run_cli(["-i", str(vpk), "-f", f"maps/{map_name}/world_physics.vmdl_c", "-d", "--gltf_export_format", "glb",
              "-o", str(out_dir)])
    # Layout varies: <map>_physics.glb (de_dust2) or maps/<map>/world_physics_physics.glb (totemlake).
    # Skip models/ and entities/ (per-prop exports) and pick the biggest file.
    found = sorted((p for p in out_dir.rglob("*_physics.glb")
                    if not {"models", "entities"} & set(p.relative_to(out_dir).parts[:-1])),
                   key=lambda p: -p.stat().st_size)
    if not found:
        raise GeometryError(f"Export produced no *_physics.glb in {out_dir}")
    if found[0] != glb:
        found[0].replace(glb)
    return glb


# --- Entities (doors, dynamic/physics props, func_brush): not part of world_physics -------------------------------

# Entity classes whose model is visible geometry the camera must not pass through or look through.
SOLID_ENTITY_PREFIXES = ("prop_", "func_door", "func_brush", "func_wall", "func_breakable", "func_movelinear",
                         "func_rotating", "func_tracktrain", "func_physbox")


def parse_entities(text: str) -> list[dict]:
    """Source2Viewer's decompiled .vents: blocks separated by '====N====', one 'key value' per line."""
    entities = []
    for block in re.split(r"^====\d+====\s*$", text, flags=re.M):
        kv = {}
        for line in block.splitlines():
            match = re.match(r"^(\S+)\s+(.*)$", line.strip())
            if match:
                kv[match.group(1)] = match.group(2).strip().strip('"')
        if kv:
            entities.append(kv)
    return entities


def _vec(value: str | None, default=(0.0, 0.0, 0.0)) -> np.ndarray:
    if not value:
        return np.array(default, dtype=np.float64)
    numbers = re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", value)
    return np.array([float(n) for n in numbers[:3]], dtype=np.float64) if len(numbers) >= 3 else np.array(default)


def angle_matrix(pitch: float, yaw: float, roll: float) -> np.ndarray:
    """Source engine AngleMatrix: columns are the entity's forward, left and up axes in world space."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    return np.array([
        [cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy],
        [cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy],
        [-sp, sr * cp, cr * cp],
    ])


def solid_entities(entities: list[dict]) -> list[dict]:
    keep = []
    for e in entities:
        cls = e.get("classname", "")
        model = e.get("model", "")
        if not cls.startswith(SOLID_ENTITY_PREFIXES) or ".vmdl" not in model:
            continue
        if e.get("rendermode") == "10" or e.get("startdisabled", "false").lower() in ("1", "true"):
            continue  # invisible or disabled at spawn
        keep.append(e)
    return keep


def _model_path(model: str) -> str:
    """'resource_name:"models/x/y.vmdl' -> 'models/x/y.vmdl_c'"""
    path = model.split(":", 1)[-1].strip().strip('"')
    return path + ("_c" if path.endswith(".vmdl") else "")


def export_entities(map_name: str) -> list[dict]:
    """Decompile the map's entity lumps (maps/<map>/entities/*.vents_c) into a list of dicts (cached as text)."""
    out_dir = MAPS_DIR / map_name / "entities"
    cached = sorted(out_dir.glob("*.txt"))
    if not cached:
        vpk = find_map_vpk(map_name)
        listing = _run_cli(["-i", str(vpk), "-l", "-f", f"maps/{map_name}/entities/"], check=False)
        lumps = re.findall(r"^(maps/\S+?\.vents_c)", listing, flags=re.M)
        out_dir.mkdir(parents=True, exist_ok=True)
        for lump in lumps:
            target = out_dir / (Path(lump).stem + ".txt")
            _run_cli(["-i", str(vpk), "-f", lump, "-d", "-o", str(target)])
        cached = sorted(out_dir.glob("*.txt"))
    entities = []
    for path in cached:
        entities += parse_entities(path.read_text(encoding="utf-8", errors="replace"))
    return entities


def export_model(map_name: str, model: str) -> Path | None:
    """Export one entity model to glb (physics hull if the model has one). Searched in the map VPK, then in the
    game's pak01 (stock props). Cached in videos/maps/<map>/models/."""
    rel = _model_path(model)
    out_dir = MAPS_DIR / map_name / "models" / re.sub(r"[^A-Za-z0-9._-]", "_", rel)
    found = sorted(out_dir.glob("*.glb")) if out_dir.is_dir() else []
    if not found and not (out_dir / "missing").exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        csgo = cs2_csgo_dir()
        for vpk in (*map_vpks(map_name), csgo / "pak01_dir.vpk" if csgo else None):
            if vpk is None or not Path(vpk).is_file():
                continue
            _run_cli(["-i", str(vpk), "-f", rel, "-d", "--gltf_export_format", "glb", "-o", str(out_dir)], check=False)
            found = sorted(out_dir.rglob("*.glb"))
            if found:
                break
        if not found:
            (out_dir / "missing").write_text(rel)
    physics = [p for p in found if p.stem.endswith("_physics")]
    return (physics or found or [None])[0]


def entity_triangles(map_name: str):
    """Yield (entity, (triangles in world space, opaque)) for every solid entity with an exportable model."""
    models: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}
    for entity in solid_entities(export_entities(map_name)):
        model = entity["model"]
        if model not in models:
            glb = export_model(map_name, model)
            models[model] = MapGeometry.glb_triangles(glb) if glb else None
        local = models[model]
        if local is None or not len(local[0]):
            continue
        tris, opaque = local
        angles = _vec(entity.get("angles"))
        scale = _vec(entity.get("scales"), (1.0, 1.0, 1.0))
        rotation = angle_matrix(*angles)
        world = (tris * scale) @ rotation.T + _vec(entity.get("origin"))
        yield entity, (world, opaque)



# --- Loading ----------------------------------------------------------------------------------------------------

_COMPONENT = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _read_glb(path: Path):
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise GeometryError(f"{path} is not a glb file")
    json_len = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + json_len])
    offset = 20 + json_len
    # A model with no geometry exports as a glb with no BIN chunk (seen on cs_insertion2).
    bin_len = struct.unpack_from("<I", data, offset)[0] if len(data) >= offset + 8 else 0
    binary = data[offset + 8: offset + 8 + bin_len]

    def accessor(index):
        acc = gltf["accessors"][index]
        view = gltf["bufferViews"][acc["bufferView"]]
        width = _WIDTH[acc["type"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        arr = np.frombuffer(binary, _COMPONENT[acc["componentType"]], acc["count"] * width, start)
        return arr.reshape(acc["count"], width) if width > 1 else arr

    return gltf, accessor


class MapGeometry:
    def __init__(self, triangles: np.ndarray, opaque: np.ndarray, name: str = ""):
        """triangles: (N, 3, 3) world coordinates; opaque: (N,) bool, False for glass/chain-link."""
        self.name = name
        self.entity_count = 0
        self.tri = triangles.astype(np.float64)
        self.opaque = opaque.astype(bool)
        self.v0 = self.tri[:, 0]
        self.e1 = self.tri[:, 1] - self.v0
        self.e2 = self.tri[:, 2] - self.v0
        lo = self.tri.min(axis=1)
        hi = self.tri.max(axis=1)
        self.zlo, self.zhi = lo[:, 2], hi[:, 2]
        self.origin = lo[:, :2].min(axis=0) - CELL
        cmin = np.floor((lo[:, :2] - self.origin) / CELL).astype(np.int64)
        cmax = np.floor((hi[:, :2] - self.origin) / CELL).astype(np.int64)
        self.shape = tuple(cmax.max(axis=0) + 2)
        cells: dict[int, list[int]] = {}
        for i, (a, b) in enumerate(zip(cmin, cmax)):
            for cx in range(a[0], b[0] + 1):
                base = cx * self.shape[1]
                for cy in range(a[1], b[1] + 1):
                    cells.setdefault(base + cy, []).append(i)
        self.cells = {k: np.asarray(v, dtype=np.int64) for k, v in cells.items()}

    @staticmethod
    def glb_triangles(glb: Path) -> tuple[np.ndarray, np.ndarray]:
        """(triangles (N,3,3) in the file's raw Source units, opaque (N,)) with clip/sky groups removed."""
        gltf, accessor = _read_glb(Path(glb))
        tris, opaque = [], []
        for node in gltf.get("nodes", []):
            if "mesh" not in node:
                continue
            name = node.get("name", "").lower()
            interact = {s.lower() for s in (node.get("extras") or {}).get("InteractAs") or []}
            if interact & IGNORED_INTERACT or "clip" in name or name.endswith("_sky"):
                continue
            see_through = any(word in name for word in SEE_THROUGH_WORDS)
            for prim in gltf["meshes"][node["mesh"]]["primitives"]:
                if "indices" not in prim:
                    continue
                verts = accessor(prim["attributes"]["POSITION"]).astype(np.float64)
                idx = accessor(prim["indices"]).astype(np.int64).reshape(-1, 3)
                tris.append(verts[idx])
                opaque.append(np.full(len(idx), not see_through))
        if not tris:
            return np.empty((0, 3, 3)), np.empty(0, bool)
        return np.concatenate(tris), np.concatenate(opaque)

    @classmethod
    def load(cls, glb: Path) -> "MapGeometry":
        tris, opaque = cls.glb_triangles(glb)
        if not len(tris):
            raise GeometryError(f"No collision triangles in {glb}")
        return cls(tris, opaque, Path(glb).stem)

    @classmethod
    def for_map(cls, map_name: str, entities: bool = True) -> "MapGeometry":
        """World collision + solid entities (doors, dynamic/physics props, func_brush). Cached as an .npz."""
        cache = MAPS_DIR / map_name / ("collision_full.npz" if entities else "collision_world.npz")
        if cache.is_file():
            data = np.load(cache)
            geometry = cls(data["tri"], data["opaque"], map_name)
            geometry.entity_count = int(data["entities"])
            return geometry
        tris, opaque = cls.glb_triangles(export_map(map_name))
        parts, flags, count = [tris], [opaque], 0
        if entities:
            for entity, (etris, eopaque) in entity_triangles(map_name):
                parts.append(etris)
                flags.append(eopaque)
                count += 1
        tri, opq = np.concatenate(parts), np.concatenate(flags)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, tri=tri.astype(np.float32), opaque=opq, entities=count)
        geometry = cls(tri, opq, map_name)
        geometry.entity_count = count
        return geometry

    # --- Queries ---------------------------------------------------------------------------------------------

    def _candidates(self, p0: np.ndarray, p1: np.ndarray) -> np.ndarray:
        length = np.linalg.norm(p1[:2] - p0[:2])
        steps = max(2, int(length / (CELL * 0.5)) + 2)
        pts = p0[:2] + (p1[:2] - p0[:2]) * np.linspace(0, 1, steps)[:, None]
        keys = set()
        for x, y in np.floor((pts - self.origin) / CELL).astype(np.int64):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    keys.add((x + dx) * self.shape[1] + y + dy)
        found = [self.cells[k] for k in keys if k in self.cells]
        if not found:
            return np.empty(0, dtype=np.int64)
        idx = np.unique(np.concatenate(found))
        zmin, zmax = min(p0[2], p1[2]), max(p0[2], p1[2])
        return idx[(self.zhi[idx] >= zmin) & (self.zlo[idx] <= zmax)]

    def first_hit(self, p0, p1, see_through_blocks: bool = False) -> float | None:
        """Fraction along p0 -> p1 of the first surface hit, or None if the segment is clear."""
        p0 = np.asarray(p0, dtype=np.float64)
        p1 = np.asarray(p1, dtype=np.float64)
        idx = self._candidates(p0, p1)
        if not see_through_blocks:
            idx = idx[self.opaque[idx]]
        if idx.size == 0:
            return None
        d = p1 - p0
        e1, e2, v0 = self.e1[idx], self.e2[idx], self.v0[idx]
        h = np.cross(d, e2)
        a = np.einsum("ij,ij->i", e1, h)
        ok = np.abs(a) > 1e-9
        f = np.zeros_like(a)
        f[ok] = 1.0 / a[ok]
        s = p0 - v0
        u = f * np.einsum("ij,ij->i", s, h)
        q = np.cross(s, e1)
        v = f * (q @ d)
        t = f * np.einsum("ij,ij->i", e2, q)
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6) & (t <= 1)
        return float(t[hit].min()) if hit.any() else None

    def clear(self, p0, p1) -> bool:
        return self.first_hit(p0, p1) is None

    def floor_z(self, x: float, y: float, z: float, depth: float = 4000.0) -> float | None:
        hit = self.first_hit((x, y, z), (x, y, z - depth), see_through_blocks=True)
        return None if hit is None else z - hit * depth

    def outline(self, bounds=None, max_points: int = 60000) -> np.ndarray:
        """xy centroids of steep (wall-like) triangles for top-down previews."""
        normal = np.cross(self.e1, self.e2)
        steep = np.abs(normal[:, 2]) < 0.3 * np.linalg.norm(normal, axis=1)
        pts = self.tri[steep].mean(axis=1)
        if bounds is not None:
            (x0, y0), (x1, y1) = bounds
            pts = pts[(pts[:, 0] >= x0) & (pts[:, 0] <= x1) & (pts[:, 1] >= y0) & (pts[:, 1] <= y1)]
        if len(pts) > max_points:
            pts = pts[np.random.default_rng(0).choice(len(pts), max_points, replace=False)]
        return pts
