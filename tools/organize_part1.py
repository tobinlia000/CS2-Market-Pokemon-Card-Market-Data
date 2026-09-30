"""Tidy CS2 Renders: move everything from Backrooms Part 1 into one folder, sorted by where it appears in the script.

Moves only (nothing is deleted). Every move is logged to <root>/moves.csv (from,to) so it can be undone.
Run: python tools/organize_part1.py [--dry-run]
"""
import csv
import re
import shutil
import sys
from pathlib import Path

R = Path(r"C:\Users\Liam's PC\Videos\CS2 Renders")
ROOT = R / "Backrooms Part 1"
S1 = ROOT / "1 - Backrooms (script scenes 2-6)"
EDIT = S1 / "A1 edit shots"
CUSTOM = S1 / "Custom shots"
SCENES = {"A2": ROOT / "2 - Poolrooms (script scene 9)", "C1": ROOT / "3 - School (script scene 11)",
          "D1": ROOT / "4 - Insertion2 flash (script scene 13)", "G1": ROOT / "5 - Jungle temple (script scene 17)"}
PREV = ROOT / "6 - Previews and full cuts"
OLD = PREV / "Old versions"
WORK = ROOT / "7 - Work files (safe to delete)"

DRY = "--dry-run" in sys.argv
moves = []


def mv(src: Path, dst: Path):
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst = dst.with_name(dst.stem + " (2)" + dst.suffix)
    moves.append((str(src), str(dst)))
    if not DRY:
        shutil.move(str(src), str(dst))


def main():
    # 1. the A1 edit shots (graded, story order)
    g = R / "A1-v6-shots-graded"
    if g.exists():
        for f in sorted(g.iterdir()):
            mv(f, EDIT / f.name)
    # 2. custom shot requests: 8-bit finals are the ones to use; the rest is work
    cs = R / "Custom Shots"
    if cs.exists():
        for f in sorted(cs.iterdir()):
            if f.name.endswith("-final-8bit.mp4"):
                mv(f, CUSTOM / f.name.replace("-final-8bit", ""))
            else:
                mv(f, WORK / "custom shots" / f.name)
    # 3. B-roll per scene
    br = R / "Part 1 B-roll"
    for demo, dest in SCENES.items():
        d = br / demo
        if d.exists():
            for f in sorted(d.iterdir()):
                name = {"index.md": f"{demo} index (every shot with its demo time).md",
                        "contact-sheet.jpg": f"{demo} contact sheet (one frame per shot).jpg"}.get(f.name, f.name)
                mv(f, dest / name)
    # 4. previews and full cuts: current = v6; older = Old versions; masters/joins = work
    for f in sorted(R.glob("A1-v*.mp4")) + sorted(R.glob("A1-v*-*.mp4")):
        if not f.exists():
            continue
        m = re.match(r"A1-(v[\d.]+[a-z]?)(?:-(.*))?\.mp4$", f.name)
        if not m:
            continue
        ver, kind = m.group(1), m.group(2) or "joined"
        if "plate32" in f.name:
            mv(f, WORK / "A1" / f.name)   # the v4 hallway plate: that shot isn't in v6
            continue
        nice = {"final-8bit": "full cut (plays in Windows)", "preview-720p": "preview 720p",
                "final": "full cut (10-bit master)"}.get(kind)
        if ver == "v6" and nice:
            mv(f, PREV / f"A1 {ver} - {nice}.mp4")
        elif nice in ("full cut (plays in Windows)", "preview 720p"):
            mv(f, OLD / f"A1 {ver} - {nice}.mp4")
        else:
            mv(f, WORK / "A1" / f.name)
    # 5. everything else from this video = work files
    pats = ["A1-*", "A11-*", "A12-*", "BROLL-*", "a1-*"]
    for p in pats:
        for f in sorted(R.glob(p)):
            if f.exists() and f != ROOT:
                mv(f, WORK / ("B-roll passes" if f.name.upper().startswith("BROLL") else "A1") / f.name)
    # remove now-empty source folders (only if empty)
    if not DRY:
        for d in (g, cs, br / "A2", br / "C1", br / "D1", br / "G1", br):
            try:
                d.rmdir()
            except OSError:
                pass
        with open(ROOT / "moves.csv", "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(moves)
    for a, b in moves:
        line = f"{Path(a).name}  ->  {Path(b).relative_to(ROOT)}"
        print(line.encode("ascii", "replace").decode())
    print(len(moves), "moves", "(dry run)" if DRY else "")


if __name__ == "__main__":
    main()
