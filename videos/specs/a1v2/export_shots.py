"""Export every shot of an A1 cut as its own graded file, numbered in story order, for the user's editing.

Each shot gets the same grade as the joined video (post.py look + restored tone curve, no vignette) but NO
letterbox (the user adds bars in CapCut),
then an 8-bit, dithered, high-quality copy that any editor and the Windows player can open.
Run: python videos/specs/a1v2/export_shots.py v6 ["C:/Users/Liam's PC/Videos/CS2 Renders"]
Writes <out>/A1-v6-shots-graded/NN - (#shot) description.mp4 and a shot-list.txt.
"""
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "csdv"))
from post import ffmpeg_path  # noqa: E402

ARGS = sys.argv[1:]
VER = ARGS.pop(0) if ARGS and ARGS[0].startswith("v") else "v6"
ONLY = {int(x) for x in ARGS.pop(ARGS.index("--only") + 1).split(",")} if "--only" in ARGS else None
if "--only" in ARGS:
    ARGS.remove("--only")
OUT = Path(ARGS[0] if ARGS else r"C:\Users\Liam's PC\Videos\CS2 Renders")


def slug(text, n=60):
    text = re.sub(r"[\\/:*?\"<>|']", "", text)          # characters Windows file names can't hold
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n].rstrip(" .,")


def main():
    order = json.loads((REPO / "videos" / "specs" / f"a1-{VER}-order.json").read_text(encoding="utf-8"))
    notes = {}
    for line in (REPO / "videos" / "specs" / "a1v2" / f"report_{VER}.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (\d+) \| (A1|A11|A12) \| ([^|]+)\| [^|]+\| [^|]+\| (.+) \|", line)
        if m:
            notes[int(m.group(1))] = (m.group(2), m.group(3).strip(), m.group(4).strip())
    dest = OUT / f"A1-{VER}-shots-graded"
    dest.mkdir(exist_ok=True)
    tmp = dest / "_tmp10bit.mp4"
    fin = order["finish"]
    ff = ffmpeg_path()
    listing = []
    for k, o in enumerate(order["shots"], 1):
        hits = glob.glob(str(OUT / f"{o['pass'].upper()}-shots" / f"sequence-*-tick-{o['start']}-to-{o['end']}.mp4"))
        if len(hits) != 1:
            sys.exit(f"shot #{o['n']}: found {hits}")
        demo, span, note = notes[o["n"]]
        if ONLY and o["n"] not in ONLY:
            listing.append(f"{k:02d}  #{o['n']:<3} {demo:<4} demo {span:<18} {note}")
            continue
        name = f"{k:02d} - (#{o['n']}) {slug(note)}.mp4"
        args = [sys.executable, str(REPO / "tools" / "csdv" / "post.py"), hits[0], "--look", fin["look"], "-o", str(tmp)]
        # no letterbox: the user adds bars in CapCut (keeps the full frame for reframing and text)
        if fin.get("curves"):
            args += ["--curves", fin["curves"]]
        if fin.get("vignette") is False:
            args.append("--no-vignette")
        subprocess.run(args, check=True, capture_output=True)
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp), "-vf",
                        "scale=flags=lanczos:sws_dither=ed,format=yuv420p", "-c:v", "libx264", "-profile:v", "high",
                        "-preset", "slow", "-crf", "14", "-c:a", "copy", "-movflags", "+faststart", str(dest / name)],
                       check=True)
        listing.append(f"{k:02d}  #{o['n']:<3} {demo:<4} demo {span:<18} {note}")
        print(f"{k:02d}/{len(order['shots'])} {name}", flush=True)
    tmp.unlink(missing_ok=True)
    (dest / "shot-list.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")
    print("done ->", dest)


if __name__ == "__main__":
    main()
