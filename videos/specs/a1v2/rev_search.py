"""Shared checks for the v5 camera searches (A1): strict LL face check, smiley-in-frame, quick evaluate."""
import numpy as np

import build as B
from build import sample

SMILEY = np.array([-2636.0, 1076.0, -5840.0])  # smiley spray at LL's corridor mouth (keep out of frame)


def strict(s, step=2):
    """Samples where LL is visible and the camera is less than 120 deg behind his facing (0 = face-safe)."""
    ticks = s["ticks"]
    lb = B.body(B.TL, ticks)
    _, lyaw, _, _ = sample(B.TL, ticks)
    bad = 0
    for i in range(0, len(ticks), step):
        inf = B.cine._in_frame(s["cam"][i], float(s["pitch"][i]), float(s["yaw"][i]), float(s["fov"][i]), lb[i],
                               margin=B.LL_MARGIN)
        if not any(inf[j] and B.G.clear(s["cam"][i], lb[i][j]) for j in range(4)):
            continue
        if abs(B.adiff(B.bearing(lb[i][3], s["cam"][i]), lyaw[i])) < 120:
            bad += 1
    return bad


def smiley_in(s):
    for i in (0, len(s["ticks"]) // 2, -1):
        inf = B.cine._in_frame(s["cam"][i], float(s["pitch"][i]), float(s["yaw"][i]), float(s["fov"][i]),
                               SMILEY[None], margin=1.1)
        if inf[0] and B.G.clear(s["cam"][i], SMILEY + [0, 6, 0]):
            return True
    return False


def ev(s):
    return [round(x, 2) for x in B.evaluate(s["ticks"], s["cam"], s["pitch"], s["yaw"], s["fov"], step=2)]
