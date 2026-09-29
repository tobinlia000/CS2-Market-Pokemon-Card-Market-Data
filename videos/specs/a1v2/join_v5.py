"""Join the A1 v5 render passes in story order, grade, and write playback copies.

Needs the four renders (a1-v5, a1-v5-p2, a11-v5, a11-v5-p2) in the output folder, each with its <NAME>-shots folder.
Run: python videos/specs/a1v2/join_v5.py [v5|v6] ["C:/Users/Liam's PC/Videos/CS2 Renders"]
Writes A1-v5.mp4 (joined), A1-v5-final.mp4 (graded 10-bit), A1-v5-final-8bit.mp4, A1-v5-preview-720p.mp4.
"""
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "csdv"))
from post import ffmpeg_path  # noqa: E402

ARGS = sys.argv[1:]
VER = ARGS.pop(0) if ARGS and ARGS[0] in ("v5", "v6") else "v5"
OUT = Path(ARGS[0] if ARGS else r"C:\Users\Liam's PC\Videos\CS2 Renders")


def main():
    order = json.loads((REPO / "videos" / "specs" / f"a1-{VER}-order.json").read_text(encoding="utf-8"))
    files = []
    for o in order["shots"]:
        folder = OUT / f"{o['pass'].upper()}-shots"
        hits = glob.glob(str(folder / f"sequence-*-tick-{o['start']}-to-{o['end']}.mp4"))
        if len(hits) != 1:
            sys.exit(f"shot #{o['n']} ({o['pass']} ticks {o['start']}-{o['end']}): found {hits}")
        files.append(hits[0])
    ff = ffmpeg_path()
    lst = OUT / f"A1-{VER}-concat.txt"
    # relative paths: the user folder has an apostrophe, which the concat list can't quote reliably
    lst.write_text("".join(f"file '{os.path.relpath(f, OUT).replace(os.sep, '/')}'\n" for f in files), encoding="utf-8")
    joined = OUT / f"A1-{VER}-joined.mp4"  # not A1-V6.mp4: that is the pass file (case-insensitive)
    subprocess.run([ff, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", joined.name],
                   cwd=OUT, check=True)
    lst.unlink()
    print("joined", len(files), "shots ->", joined)
    fin = order["finish"]
    final = OUT / f"A1-{VER}-final.mp4"
    args = [sys.executable, str(REPO / "tools" / "csdv" / "post.py"), str(joined), "--look", fin["look"], "-o", str(final)]
    if fin.get("letterbox"):
        args.append("--letterbox")
    if fin.get("curves"):
        args += ["--curves", fin["curves"]]
    if fin.get("vignette") is False:
        args.append("--no-vignette")
    subprocess.run(args, check=True)
    print("graded ->", final)
    c8 = OUT / f"A1-{VER}-final-8bit.mp4"
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(final), "-vf",
                    "scale=flags=lanczos:sws_dither=ed,format=yuv420p", "-c:v", "libx264", "-profile:v", "high",
                    "-preset", "slow", "-crf", "16", "-c:a", "copy", "-movflags", "+faststart", str(c8)], check=True)
    prev = OUT / f"A1-{VER}-preview-720p.mp4"
    subprocess.run([ff, "-v", "error", "-y", "-i", str(c8), "-vf", "scale=1280:-2:flags=lanczos", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "22", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(prev)],
                   check=True)
    print("8-bit ->", c8, "\npreview ->", prev)


if __name__ == "__main__":
    main()
