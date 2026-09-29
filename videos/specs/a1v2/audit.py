"""Lore audit of the A1 cut: LL's front must never read (only back of head / hidden face / silhouette)."""
import json, math, sys
import numpy as np
import build as B
import cine

spec = json.load(open(B.REPO / "videos" / "specs" / "a1-v2.json", encoding="utf-8"))
SIL = 0.08   # LL counts as a silhouette below this fraction of the frame height
out = []
for c in spec["clips"]:
    a, b = c["start"], c["end"]
    ticks = np.arange(a, b + 1, 4)
    if "camera" in c:
        k = c["camera"]["keys"]
        kt = np.array([x["t"] for x in k]) * B.TR + a
        cam = np.array([np.interp(ticks, kt, [x["pos"][i] for x in k]) for i in range(3)]).T
        pitch = np.interp(ticks, kt, [x["pitch"] for x in k]); yaw = np.interp(ticks, kt, [x["yaw"] for x in k])
        fov = np.interp(ticks, kt, [x["fov"] for x in k])
    else:  # native POV
        cam = B.eye(B.TC, ticks); _, yaw, pitch, _ = B.sample(B.TC, ticks); fov = np.full(len(ticks), 90.0)
    lb = B.body(B.TL, ticks); _, lyaw, _, _ = B.sample(B.TL, ticks)
    bad, sil, back = [], 0, 0
    for i, t in enumerate(ticks):
        inf = cine._in_frame(cam[i], float(pitch[i]), float(yaw[i]), float(fov[i]), lb[i])
        vis = [j for j in range(4) if inf[j] and B.G.clear(cam[i], lb[i][j])]
        if not vis:
            continue
        dist = np.linalg.norm(lb[i][3] - cam[i])
        vfov = 2 * math.atan(math.tan(math.radians(fov[i]) / 2) / 0.75 * 9 / 16)
        frac = 72 / (2 * dist * math.tan(vfov / 2))
        front = abs(B.adiff(B.bearing(lb[i][3], cam[i]), lyaw[i])) < 100
        if not front:
            back += 1
        elif frac < SIL:
            sil += 1
        else:
            bad.append((t, frac))
    if bad:
        ts = [x[0] for x in bad]
        out.append((c["why"][:48], f"{B.tk and ''}{min(ts)/64:.1f}-{max(ts)/64:.1f}s demo", len(bad), max(x[1] for x in bad)))
    print(f"{c['why'][:50]:50} back {back:3} silhouette {sil:3} FRONT {len(bad):3}" + (f"  max size {max(x[1] for x in bad):.0%}" if bad else ""))
print("\nVIOLATIONS:")
for o in out:
    print(" ", o)
