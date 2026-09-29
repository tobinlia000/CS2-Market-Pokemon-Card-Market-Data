"""Visual landmarks: find signs, posters, logos and other readable surfaces on a map, render each one head-on,
and let a human (or Claude) name them. Named signs then appear on the floor maps as landmarks.

    python tools/csdv/signs.py scan <map>                   # export textured world, render candidates, make sheets
    python tools/csdv/signs.py name <map> 12="Car Toys sign" 31="bathroom sign" ...
    python tools/csdv/signs.py clean <map>                  # delete the textured export (1-2 GB), keep renders

Files: videos/maps/<map>/signs/{index.json, NNNN.png, sheets/sheet-NN.png}; names in
videos/maps/<map>/landmarks-visual.json (read by mapview.landmarks). The textured export lives in
videos/maps/<map>/textured/ until `clean`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import mapgeo
from mapgeo import MAPS_DIR, GeometryError, _run_cli

KEYWORDS = ["sign", "poster", "logo", "text", "letter", "number", "banner", "billboard", "label", "menu", "notice",
            "exit", "arrow", "graffiti", "painting", "picture", "photo", "flag", "neon", "sticker", "calendar",
            "decal", "overlay", "advert", "brand", "store", "shop", "chalkboard", "whiteboard", "blackboard"]
# Never interesting even if a keyword matches (grime decals, tiling surfaces).
EXCLUDE = ["stain", "crack", "dirt", "grime", "puddle", "leak", "blood", "burn", "scorch", "dust", "rust", "moss",
           "detail", "parkingstripe", "tire", "footprint", "splat", "bullet", "hole", "physics", "tools", "nodraw",
           "asphalt", "crosswalk", "roadline", "sanddecal", "flagpole", "jalousie", "jalusie", "mltry", "decay",
           "pigeon", "peel", "mold", "water_", "wet", "branch", "pole", "undergrowth", "bjork", "trunk", "stump",
           "stripe", "brunn", "trim", "garbage", "trash", "path_", "rug_", "container", "fake_sky", "shelves",
           "chiller", "shelving", "urban_props", "interior_props", "koloni_props", "rat", "bacteria", "duck_",
           "glowstick", "hat_", "deadbod", "pinks", "party_goer", "skinstealer"]
SURFACE = ["floor", "wall", "ceil", "carpet", "tile", "concrete", "plaster", "wood", "metal", "brick", "rock",
           "ground", "grass", "water", "glass", "trim", "roof", "stone", "marble", "dirt", "sand", "fabric", "default"]
# Custom (map-shipped) materials that are ordinary props, not readable surfaces.
PROPS = ["door", "window", "lamp", "light", "rim", "box", "cabinet", "chair", "table", "desk", "pipe", "extinguisher",
         "flagpole", "plastic", "chrom", "ceramic", "side", "frame", "bench", "locker", "radiator", "vent", "fence",
         "tree", "leaf", "bush", "curtain", "blind", "handle", "rail", "stair", "asphalt", "road", "blacktop", "blend",
         "cable", "wire", "sink", "toilet", "bed", "sofa", "shelf", "crate", "barrel", "tire", "wheel", "car", "truck",
         "bottle", "cup", "plate", "can_", "trash", "bin_", "glass", "mirror"]
SIGN_MODEL_WORDS = r"sign|logo|letter|text|neon|name|store|shop|banner|board"


def blender_exe() -> Path:
    env = os.environ.get("BLENDER_EXE")
    if env and Path(env).is_file():
        return Path(env)
    found = sorted(Path(r"C:\Program Files\Blender Foundation").glob("Blender */blender.exe"))
    if not found:
        raise GeometryError("Blender not found (set BLENDER_EXE).")
    return found[-1]


def _link_or_copy(src: Path, dst: Path):
    if dst.exists():
        return
    try:
        os.link(src, dst)          # same volume: no extra disk space
    except OSError:
        shutil.copy2(src, dst)


def export_textured(map_name: str) -> Path:
    """World render mesh with materials + textures. Workshop addons are exposed to Source2Viewer as a mod folder
    (pak01_*.vpk hard links) through a generated gameinfo.gi, so custom materials resolve."""
    out = MAPS_DIR / map_name / "textured"
    gltf = out / "w.gltf"
    if gltf.is_file():
        return gltf
    vpks = mapgeo.map_vpks(map_name)
    csgo = mapgeo.cs2_csgo_dir()
    search = []
    if len(vpks) > 1:
        addon = vpks[1]
        mod = out / "addon"
        mod.mkdir(parents=True, exist_ok=True)
        stem = addon.name[: -len("_dir.vpk")] if addon.name.endswith("_dir.vpk") else addon.stem
        for part in addon.parent.glob(f"{stem}_*.vpk"):
            _link_or_copy(part, mod / part.name.replace(stem, "pak01", 1))
        search.append(mod)
    search += [csgo, csgo.parent / "core"]
    gi = out / "gi" / "game" / "mod" / "gameinfo.gi"
    gi.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(f'\t\t\tGame\t"{p.as_posix()}"' for p in search)
    gi.write_text('"GameInfo"\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n' + lines + "\n\t\t}\n\t}\n}\n",
                  encoding="utf-8")
    _run_cli(["-i", str(vpks[0]), "-f", f"maps/{map_name}/world.vwrld_c", "-d", "--game", str(gi),
              "--gltf_export_format", "gltf", "--gltf_export_materials", "-o", str(gltf)], check=False)
    if not gltf.is_file():
        raise GeometryError(f"Textured export failed for {map_name}")
    return gltf


def material_lists(map_name: str) -> tuple[list[str], list[str]]:
    """(custom materials shipped by the workshop addon, materials used by the addon's sign-like models)."""
    vpks = mapgeo.map_vpks(map_name)
    if len(vpks) < 2:
        return [], []
    listing = _run_cli(["-i", str(vpks[1]), "-l"], check=False)
    paths = [line.split(" CRC")[0].strip() for line in listing.splitlines()]
    custom = sorted({Path(p).name[: -len(".vmat_c")] for p in paths if p.endswith(".vmat_c")})
    # keep a prop-sounding material when it also has a sign word (e.g. assault_door_decal01, tabl_exit)
    custom = [m for m in custom if not any(w in m.lower() for w in SURFACE + EXCLUDE)
              and (not any(w in m.lower() for w in PROPS) or any(k in m.lower() for k in KEYWORDS))]
    generic = set()
    for p in paths:
        if p.endswith(".vmdl_c") and re.search(SIGN_MODEL_WORDS, Path(p).stem, re.I):
            refs = _run_cli(["-i", str(vpks[1]), "-f", p, "-b", "RERL"], check=False)
            generic |= {Path(m).stem for m in re.findall(r"([\w/\\.-]+)\.vmat\b", refs)}
    return custom, sorted(generic)


def scan(map_name: str) -> Path:
    out = MAPS_DIR / map_name / "signs"
    gltf = export_textured(map_name)
    custom, generic = material_lists(map_name)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():  # clear contents (the folder itself may be a shell's working directory)
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    cfg = {"gltf": str(gltf), "out": str(out), "keywords": KEYWORDS, "custom": custom,
           "sign_model_materials": generic, "exclude": EXCLUDE, "max_size": 700, "voxel": 24}
    (out / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    script = Path(__file__).with_name("blender_signs.py")
    result = subprocess.run([str(blender_exe()), "-b", "--factory-startup", "-P", str(script), "--",
                             str(out / "config.json")], capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    (out / "blender.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if not (out / "index.json").is_file():
        raise GeometryError(f"Blender run failed; see {out / 'blender.log'}")
    _filter_excluded(out)
    sheets(out)
    return out


def _filter_excluded(out: Path):
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    keep = [e for e in index if not all(any(w in m for w in EXCLUDE) for m in e["materials"])]
    (out / "index.json").write_text(json.dumps(keep, indent=1), encoding="utf-8")


def sheets(out: Path, cols: int = 2, rows: int = 5):
    """Contact sheets: a grid of tiles, one per image (a two-sided candidate gets two tiles, 'a' and 'b'),
    each labelled S<id>[a|b], number of copies, position and main material."""
    from PIL import Image, ImageDraw

    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    sheet_dir = out / "sheets"
    shutil.rmtree(sheet_dir, ignore_errors=True)
    sheet_dir.mkdir()
    tiles = [(e, img) for e in index for img in e["images"]]
    W, H, pad = 560, 280, 20
    per = cols * rows
    for n in range(0, len(tiles), per):
        chunk = tiles[n:n + per]
        sheet = Image.new("RGB", (W * cols, (H + pad) * ((len(chunk) + cols - 1) // cols)), (35, 35, 35))
        d = ImageDraw.Draw(sheet)
        for k, (e, img) in enumerate(chunk):
            x, y = (k % cols) * W, (k // cols) * (H + pad)
            side = img[4:5] if img[4:5] in ("a", "b") else ""
            copies = len(e.get("instances", [])) or 1
            d.text((x + 6, y + 4), f"S{e['id']}{side}  x{copies}  {tuple(e['pos'])}  {e['materials'][0][:26]}",
                   fill=(255, 220, 0))
            im = Image.open(out / img).convert("RGB")
            im.thumbnail((W - 6, H))
            sheet.paste(im, (x + (W - im.width) // 2, y + pad + (H - im.height) // 2))
        sheet.save(sheet_dir / f"sheet-{n // per:02d}.png")
    return sorted(sheet_dir.glob("*.png"))


def name(map_name: str, pairs: list[str]):
    """Record names for candidates: 12="Car Toys sign". An empty name removes the entry."""
    out = MAPS_DIR / map_name / "signs"
    index = {e["id"]: e for e in json.loads((out / "index.json").read_text(encoding="utf-8"))}
    path = MAPS_DIR / map_name / "landmarks-visual.json"
    named = {m["sign"]: m for m in json.loads(path.read_text(encoding="utf-8"))} if path.is_file() else {}
    for pair in pairs:
        key, _, label = pair.partition("=")
        sid = int(key.strip().lstrip("Ss"))
        if not label.strip():
            named.pop(sid, None)
            continue
        e = index[sid]
        named[sid] = {"sign": sid, "kind": "sign", "name": label.strip(), "pos": e["pos"], "normal": e["normal"],
                      "size": e["size"], "instances": e.get("instances") or [e["pos"]]}
    path.write_text(json.dumps(sorted(named.values(), key=lambda m: m["sign"]), indent=1), encoding="utf-8")
    return path


def clean(map_name: str):
    shutil.rmtree(MAPS_DIR / map_name / "textured", ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("map")
    n = sub.add_parser("name"); n.add_argument("map"); n.add_argument("pairs", nargs="+")
    c = sub.add_parser("clean"); c.add_argument("map")
    args = ap.parse_args(argv)
    if args.cmd == "scan":
        out = scan(args.map)
        index = json.loads((out / "index.json").read_text(encoding="utf-8"))
        print(f"{len(index)} candidates; sheets in {out / 'sheets'}")
    elif args.cmd == "name":
        print(name(args.map, args.pairs))
    else:
        clean(args.map)
    return 0


if __name__ == "__main__":
    sys.exit(main())
