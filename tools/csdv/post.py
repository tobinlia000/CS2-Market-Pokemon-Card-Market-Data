"""Post-processing looks for rendered clips (FFmpeg; nothing touches the game).

  python tools/csdv/post.py <input.mp4> --look downscale|cinematic|camcorder [--size 2560x1440]
                            [--letterbox] [--stamp "SEP 27 1996"] [-o out.mp4]

downscale  high-quality Lanczos downscale (supersampling: render at 4K, deliver 1440p).
cinematic  gentle contrast, slightly muted colour, soft vignette, fine grain; --letterbox adds 2.39:1 bars.
camcorder  90s tape look for the Backrooms mood: 4:3, low resolution, colour fringing, noise, faint scanlines,
           slight tape wobble, warm tint, "PLAY" + date/time stamp. Delivered as 1920x1440 (4:3).
Audio is copied unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FONT = "C\\:/Windows/Fonts/consola.ttf"   # drawtext needs an explicit font on Windows (no fontconfig)
SYMBOLS = "C\\:/Windows/Fonts/seguisym.ttf"  # Consolas has no ▶ glyph


def ffmpeg_path() -> str:
    settings = Path(os.environ.get("USERPROFILE", "~")) / ".csdm" / "settings.json"
    if settings.is_file():
        ff = json.loads(settings.read_text(encoding="utf-8")).get("video", {}).get("ffmpegSettings", {})
        if ff.get("customLocationEnabled") and Path(ff.get("customExecutableLocation", "")).is_file():
            return ff["customExecutableLocation"]
    found = shutil.which("ffmpeg")
    if not found:
        sys.exit("FFmpeg not found (set it in CS Demo Manager > Settings > Video).")
    return found


def filters(look: str, size: tuple[int, int], letterbox: bool, stamp: str, curves: str = "", vignette: bool = True) -> str:
    w, h = size
    if look == "downscale":
        return f"scale={w}:{h}:flags=lanczos"
    if look == "cinematic":
        # 16-bit processing + deband: dark gradients recorded in 8-bit show contour "banding" (the user's note on
        # the first sample); smoothing them in 16-bit and exporting 10-bit keeps the gradient smooth.
        chain = ["format=gbrp16le",
                 f"scale={w}:{h}:flags=lanczos",
                 "deband=1thr=0.015:2thr=0.015:3thr=0.015:range=24:blur=1"]
        if curves:
            # restores a map's own tone curve when its post-process is switched off (e.g. to drop its vignette)
            chain.append(f"curves=all='{curves}'")
        chain += [
                 "eq=contrast=1.07:saturation=0.9:gamma=0.98",
                 "colorbalance=rs=0.015:bs=-0.015:rh=0.02:bh=-0.02",   # a touch warm in the highlights
                 "noise=alls=5:allf=t+u"]                             # fine, moving grain
        if vignette:
            chain.insert(-1, "vignette=angle=PI/5:mode=backward")
        if letterbox:
            bar = round(h * (1 - (w / 2.39) / h) / 2)
            chain += [f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill",
                      f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill"]
        return ",".join(chain)
    if look == "camcorder":
        return ",".join([
            "crop=ih*4/3:ih",                                      # 4:3 like a camcorder
            "crop=iw-24:ih-18:12+3*sin(t*9):9+2*sin(t*5.3)",       # slight tape wobble
            "scale=640:480:flags=bilinear",                          # tape-era resolution
            "gblur=sigma=0.7",
            "rgbashift=rh=2:bh=-2",                                  # colour fringing
            "eq=contrast=1.08:saturation=1.12:gamma=1.03",
            "colorbalance=rs=0.05:gs=0.03:bs=-0.06",                # warm, slightly yellow (Backrooms)
            "noise=alls=14:allf=t",
            "drawgrid=w=iw:h=3:t=1:c=black@0.18",                    # faint scanlines
            "scale=1920:1440:flags=bilinear",
            f"drawtext=fontfile='{FONT}':text='PLAY':x=90:y=80:fontsize=54:fontcolor=white@0.9",
            f"drawtext=fontfile='{SYMBOLS}':text='▶':x=250:y=80:fontsize=54:fontcolor=white@0.9",
            f"drawtext=fontfile='{FONT}':text='{stamp}':x=90:y=h-150:fontsize=50:fontcolor=white@0.9",
            f"drawtext=fontfile='{FONT}':text='%{{pts\\:gmtime\\:0\\:%H\\\\\\:%M\\\\\\:%S}}':x=w-420:y=h-150:"
            "fontsize=50:fontcolor=white@0.9",
            "vignette=angle=PI/4.5",
        ])
    raise SystemExit(f"Unknown look '{look}'.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--look", required=True, choices=["downscale", "cinematic", "camcorder"])
    ap.add_argument("--size", default="2560x1440")
    ap.add_argument("--letterbox", action="store_true")
    ap.add_argument("--no-vignette", action="store_true", help="cinematic only: skip the soft vignette")
    ap.add_argument("--curves", default="", help="cinematic only: ffmpeg curves points, e.g. '0/0 0.09/0.035 1/1'")
    ap.add_argument("--stamp", default="SEP 27 1996")
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--bits", type=int, choices=[8, 10], default=10,
                    help="10 (default): smooth dark gradients, fine for YouTube/editors; 8: widest player support")
    ap.add_argument("-o", "--output")
    args = ap.parse_args(argv)
    src = Path(args.input)
    out = Path(args.output) if args.output else src.with_name(f"{src.stem}-{args.look}{src.suffix}")
    w, h = (int(v) for v in args.size.lower().split("x"))
    pix = "yuv420p10le" if args.bits == 10 else "yuv420p"
    vf = filters(args.look, (w, h), args.letterbox, args.stamp, args.curves, not args.no_vignette) + f",format={pix}"
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-vf", vf,
           "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf), "-pix_fmt", pix, "-c:a", "copy", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        return result.returncode
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
