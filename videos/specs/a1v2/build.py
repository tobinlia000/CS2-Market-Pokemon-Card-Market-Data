"""A1 shot list v2 (planning chat, 2026-09-28) -> videos/specs/a1-v2.json + a check report.

Every camera is computed from the demo's per-tick positions and checked against the map geometry:
  - Caillou (C) visible and in frame for the shot (unless the shot lets him enter/leave on purpose),
  - Lightning Lemur (LL) out of frame where the script hides him, and never seen from the front (face-safe),
  - cameras not inside walls.
Run: python videos/specs/a1v2/build.py   (then csdv.py build videos/specs/a1-v2.json)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "csdv"))
import cine  # noqa: E402
import mapgeo  # noqa: E402
import positions  # noqa: E402

DEMO = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\A1.dem"
MAP = "ze_backrooms_insomnia"
TR = 64
CNAME, LNAME = "Caillou (Canadian now)", "Lightning Lemur"

G = mapgeo.MapGeometry.for_map(MAP)
_tracks = {t.name: t for t in positions.load_tracks(DEMO).values()}
TC, TL = _tracks[CNAME], _tracks[LNAME]


def tk(s: str) -> int:
    m, x = s.split(":")
    return int(round((int(m) * 60 + float(x)) * TR))


def sample(track, ticks):
    i = np.searchsorted(track.tick, ticks).clip(0, len(track.tick) - 1)
    return track.pos[i], track.yaw[i], track.pitch[i], track.duck[i]


def body(track, ticks):
    pos, _, _, duck = sample(track, ticks)
    hs = np.array([6.0, 34.0, 56.0, 70.0])
    return pos[:, None, :] + np.stack([np.zeros((len(ticks), 4)), np.zeros((len(ticks), 4)),
                                       hs[None] * (1 - 0.3 * duck[:, None])], axis=2)


def eye(track, ticks):
    pos, _, _, duck = sample(track, ticks)
    return pos + np.c_[np.zeros(len(pos)), np.zeros(len(pos)), 64 - 18 * duck]


def fdir(yaw):
    return np.c_[np.cos(np.radians(yaw)), np.sin(np.radians(yaw))]


def bearing(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def adiff(a, b):
    return (a - b + 180) % 360 - 180


def cam_ok(p) -> bool:
    fl = G.floor_z(*p, depth=800.0)
    if fl is None or p[2] - fl < 8:
        return False
    up = G.first_hit(p, p + [0, 0, 3000.0], see_through_blocks=True)
    if up is None:
        return False  # open sky above means we're outside the level
    for a in range(0, 360, 45):
        d = np.array([math.cos(math.radians(a)), math.sin(math.radians(a)), 0.0])
        if not G.clear(p, p + d * 14):
            return False
    return True


# --- evaluation ---------------------------------------------------------------------------------------------------

LL_MARGIN = 1.15  # LL checks: count him as in frame even when only an edge of his body is (v3 #8 lesson)


def evaluate(ticks, cam, pitch, yaw, fov, ll_policy="out", step=6):
    """Per sampled tick: C visible+in frame, LL visible+in frame, LL front visible."""
    idx = np.arange(0, len(ticks), step)
    ts = ticks[idx]
    cb, lb = body(TC, ts), body(TL, ts)
    _, lyaw, _, _ = sample(TL, ts)
    cvis, lin, lfront = [], [], []
    for k, i in enumerate(idx):
        c, p_, y_, f_ = cam[i], float(pitch[i]), float(yaw[i]), float(fov[i])
        inf = cine._in_frame(c, p_, y_, f_, cb[k])
        cvis.append(sum(1 for j in range(4) if inf[j] and G.clear(c, cb[k][j])) >= 2)
        linf = cine._in_frame(c, p_, y_, f_, lb[k], margin=LL_MARGIN)
        seen = any(linf[j] and G.clear(c, lb[k][j]) for j in range(4))
        lin.append(seen)
        face = abs(adiff(bearing(lb[k][3], c), lyaw[k])) < 100  # camera in front of LL's face
        lfront.append(seen and face)
    return float(np.mean(cvis)), float(np.mean(lin)), float(np.mean(lfront))


def passes(res, need_c, ll_policy):
    cv, li, lf = res
    if cv < need_c:
        return False
    if ll_policy == "out" and li > 0:
        return False
    if ll_policy == "backonly" and lf > 0:
        return False
    return True


# --- static / zoom solver -------------------------------------------------------------------------------------------

def zoom_track(fov0, zoom, n, ease=True):
    f = np.linspace(0, 1, n)
    if ease:
        f = f * f * (3 - 2 * f)
    scale = 1 + (zoom - 1) * f
    return np.degrees(2 * np.arctan(np.tan(np.radians(fov0) / 2) * scale))


def solve_static(a, b, rel=None, absb=None, dists=(250, 350, 450, 600), heights=(64,), anchor="mid", aim="chest",
                 margin=1.2, fov_range=(25, 90), need_c=0.9, ll="out", zoom=1.0, avoid_yaw=None, fit=True,
                 fixed_fov=None, near=None, aim_point=None, tries=400):
    ticks = np.arange(a, b + 1, 2)
    cpos, cyaw, _, _ = sample(TC, ticks)
    feet = cpos
    if anchor == "start":
        ap = feet[0]
    elif anchor == "end":
        ap = feet[-1]
    else:
        ap = feet[np.argmin(np.linalg.norm(feet - feet.mean(0), axis=1))]
    ref = float(np.degrees(np.arctan2(*np.mean(fdir(cyaw), 0)[::-1])))
    travel = feet[-1, :2] - feet[0, :2]
    trav = math.degrees(math.atan2(travel[1], travel[0])) if np.linalg.norm(travel) > 64 else ref
    names = {"front": 0, "back": 180, "left": 90, "right": -90, "front-left": 45, "front-right": -45,
             "back-left": 135, "back-right": -135}
    if absb is not None:
        want = float(absb)
    elif rel == "ahead":
        want = trav
    elif rel == "behind-travel":
        want = trav + 180
    elif rel is not None:
        want = ref + names[rel]
    else:
        want = ref + 45
    pts = body(TC, ticks[::4]).reshape(-1, 3)
    aimp = np.asarray(aim_point, float) if aim_point is not None else (
        np.r_[ap[:2], ap[2] + 50] if aim == "anchor" else pts.mean(0) + [0, 0, 8])
    cands = []
    for db in range(0, 181, 10):
        for sgn in ((1, -1) if db not in (0, 180) else (1,)):
            for d in dists:
                for h in heights:
                    bb = want + sgn * db
                    base = np.asarray(near, float) if near is not None else ap
                    cam = np.r_[base[:2] + np.array([math.cos(math.radians(bb)), math.sin(math.radians(bb))]) * d,
                                ap[2] + h]
                    pref = -db / 25.0 - abs(math.log(d / dists[0])) * 0.5 - abs(h - heights[0]) / 200
                    cands.append((pref, cam))
    cands.sort(key=lambda c: -c[0])
    n = len(ticks)
    for pref, cam in cands[:tries]:
        if not cam_ok(cam) or not G.clear(cam, aimp):
            continue
        pitch, yaw = (float(v) for v in __import__("campath").look_at(tuple(cam), tuple(aimp)))
        if avoid_yaw is not None and abs(adiff(yaw, avoid_yaw[0])) < avoid_yaw[1]:
            continue
        fov0 = fixed_fov if fixed_fov else cine.fit_fov(cam, pitch, yaw, pts, margin) if fit else fov_range[1]
        if fov0 > fov_range[1]:
            if fit and need_c >= 0.9:
                continue
            fov0 = fov_range[1]
        fov0 = max(fov0, fov_range[0])
        fov = zoom_track(fov0, zoom, n)
        camt = np.tile(cam, (n, 1))
        res = evaluate(ticks, camt, np.full(n, pitch), np.full(n, yaw), fov)
        if passes(res, need_c, ll):
            return dict(ticks=ticks, cam=camt, pitch=np.full(n, pitch), yaw=np.full(n, yaw), fov=fov, res=res)
    return None


# --- moving / special shots -----------------------------------------------------------------------------------------

def smooth(x, s):
    return gaussian_filter1d(x, sigma=s, axis=0, mode="nearest")


def look(cam, target):
    d = target - cam
    yaw = np.degrees(np.unwrap(np.radians(np.degrees(np.arctan2(d[:, 1], d[:, 0])))))
    pitch = -np.degrees(np.arctan2(d[:, 2], np.hypot(d[:, 0], d[:, 1])))
    return pitch, yaw


def shot_pov_keys(a, b, fov0=90, zoom=1.0):
    ticks = np.arange(a, b + 1, 2)
    cam = eye(TC, ticks)
    _, yaw, pitch, _ = sample(TC, ticks)
    fov = zoom_track(fov0, zoom, len(ticks))
    return dict(ticks=ticks, cam=cam, pitch=pitch.astype(float), yaw=np.degrees(np.unwrap(np.radians(yaw))), fov=fov,
                res=evaluate(ticks, cam, pitch, yaw, fov, step=8))


def shot_ots_c(a, b, back=70, side=16, up=8, fov=60, ahead=500):
    ticks = np.arange(a, b + 1, 2)
    e = smooth(eye(TC, ticks), 6)
    _, yaw, _, _ = sample(TC, ticks)
    fd = smooth(fdir(yaw), 10)
    fd = fd / np.linalg.norm(fd, axis=1)[:, None]
    right = np.c_[fd[:, 1], -fd[:, 0]]
    want = np.c_[e[:, :2] - fd * back + right * side, e[:, 2] + up]
    cam, _ = cine._resolve(G, e, want, 32)
    target = np.c_[e[:, :2] + fd * ahead, e[:, 2] - 10]
    pitch, yw = look(cam, 0.75 * target + 0.25 * e)
    fv = np.full(len(ticks), float(fov))
    return dict(ticks=ticks, cam=cam, pitch=pitch, yaw=yw, fov=fv, res=evaluate(ticks, cam, pitch, yw, fv))


def shot_behind_ll(a, b, dist=60, side=10, up=6, fov=None, static=True, weight=0.85, handheld=0.0, follow_facing=False,
                   glide=4):
    """Camera just behind LL's head looking past him at C. static: one fixed spot (median)."""
    ticks = np.arange(a, b + 1, 2)
    le = eye(TL, ticks)
    ce = eye(TC, ticks) - [0, 0, 14]
    _, lyaw, _, _ = sample(TL, ticks)
    if follow_facing:
        dirv = smooth(fdir(lyaw), 6)
    else:
        dirv = ce[:, :2] - le[:, :2]
    dirv = dirv / np.maximum(np.linalg.norm(dirv, axis=1), 1e-6)[:, None]
    right = np.c_[dirv[:, 1], -dirv[:, 0]]
    want = np.c_[le[:, :2] - dirv * dist + right * side, le[:, 2] + up]
    if static:
        m = len(ticks) // 2
        want = np.tile(want[m], (len(ticks), 1))
        cam = want
    else:
        cam, _ = cine._resolve(G, le, smooth(want, glide), 32)
        cam = smooth(cam, glide)
    target = weight * ce + (1 - weight) * le
    if not static:
        target = smooth(target, glide)
    if static:
        target = np.tile(target.mean(0), (len(ticks), 1))
    pitch, yw = look(cam, target)
    if fov is None:
        d = np.linalg.norm(ce - cam, axis=1).mean()
        fov = cine.hfov_for_size(d, "medium") * 1.6
    fv = np.full(len(ticks), float(np.clip(fov, 20, 90)))
    roll = np.zeros(len(ticks))
    if handheld:
        tt = (ticks - ticks[0]) / TR
        cam, pitch, yw, roll = cine.apply_handheld(handheld, tt, TR / 2, cam, pitch, yw, roll)
    out = dict(ticks=ticks, cam=cam, pitch=pitch, yaw=yw, fov=fv, roll=roll,
               res=evaluate(ticks, cam, pitch, yw, fv, ll_policy="backonly"))
    out["camok"] = bool(cam_ok(cam[len(cam) // 2]))
    return out


def shot_creature_lead(a, b, ahead=36, fov=75):
    ticks = np.arange(a, b + 1, 2)
    le = smooth(eye(TL, ticks), 5)
    pos, lyaw, _, _ = sample(TL, ticks)
    vel = np.gradient(smooth(pos, 5), axis=0)
    spd = np.linalg.norm(vel[:, :2], axis=1)
    mv = vel[:, :2] / np.maximum(spd, 1e-6)[:, None]
    w = np.clip(spd / 3.0, 0, 1)[:, None]
    hd = smooth(w * mv + (1 - w) * fdir(lyaw), 8)
    hd = hd / np.linalg.norm(hd, axis=1)[:, None]
    cam = np.c_[le[:, :2] + hd * ahead, le[:, 2]]
    yaw = np.degrees(np.unwrap(np.arctan2(hd[:, 1], hd[:, 0])))
    pitch = np.full(len(ticks), 4.0)
    fv = np.full(len(ticks), float(fov))
    return dict(ticks=ticks, cam=cam, pitch=pitch, yaw=yaw, fov=fv, res=evaluate(ticks, cam, pitch, yaw, fv))


def shot_cine(kind, a, b, params, track=TC):
    r = cine.build(kind, track, a, b, TR, params, G)
    ticks = np.array([a + int(round(k.t * TR)) for k in r.keys])
    cam = np.array([[k.x, k.y, k.z] for k in r.keys])
    pitch = np.array([k.pitch for k in r.keys])
    yaw = np.array([k.yaw for k in r.keys])
    fov = np.array([k.fov for k in r.keys])
    roll = np.array([k.roll for k in r.keys])
    return dict(ticks=ticks, cam=cam, pitch=pitch, yaw=yaw, fov=fov, roll=roll, warn=r.warnings,
                res=evaluate(ticks, cam, pitch, yaw, fov, step=2))


def shot_whip(a, b, cam, from_pt, to_pt, whip=0.3, fov=60):
    ticks = np.arange(a, b + 1, 1)
    n = len(ticks)
    p0, y0 = (float(v) for v in __import__("campath").look_at(tuple(cam), tuple(from_pt)))
    p1, y1 = (float(v) for v in __import__("campath").look_at(tuple(cam), tuple(to_pt)))
    y1 = y0 + adiff(y1, y0)
    tt = (ticks - ticks[0]) / TR
    f = np.clip(tt / whip, 0, 1)
    f = f * f * (3 - 2 * f)
    camt = np.tile(np.asarray(cam, float), (n, 1))
    yaw = y0 + (y1 - y0) * f
    pitch = p0 + (p1 - p0) * f
    fv = np.full(n, float(fov))
    return dict(ticks=ticks, cam=camt, pitch=pitch, yaw=yaw, fov=fv, res=evaluate(ticks, camt, pitch, yaw, fv, step=3))


def shot_pull(a, b, bearing_deg, d0=150, d1=520, h0=60, h1=260, fov=55):
    ticks = np.arange(a, b + 1, 2)
    cpos = smooth(sample(TC, ticks)[0], 6)
    f = np.linspace(0, 1, len(ticks))
    f = f * f * (3 - 2 * f)
    d = d0 + (d1 - d0) * f
    h = h0 + (h1 - h0) * f
    dv = np.array([math.cos(math.radians(bearing_deg)), math.sin(math.radians(bearing_deg))])
    aimp = cpos + [0, 0, 46]
    want = np.c_[cpos[:, :2] + dv * d[:, None], cpos[:, 2] + h]
    cam, pulled = cine._resolve(G, aimp, want, 32)
    pitch, yaw = look(cam, aimp)
    fv = np.full(len(ticks), float(fov))
    return dict(ticks=ticks, cam=cam, pitch=pitch, yaw=yaw, fov=fv, res=evaluate(ticks, cam, pitch, yaw, fv),
                pulled=float(pulled.mean()))


# --- spec output ------------------------------------------------------------------------------------------------------

def keys_of(s, a):
    keys = []
    roll = s.get("roll")
    for i in range(len(s["ticks"])):
        keys.append({"t": round((int(s["ticks"][i]) - a) / TR, 4), "pos": [round(float(v), 1) for v in s["cam"][i]],
                     "pitch": round(float(s["pitch"][i]), 3), "yaw": round(float(s["yaw"][i]), 3),
                     "roll": round(float(roll[i]), 3) if roll is not None else 0.0,
                     "fov": round(float(s["fov"][i]), 3)})
    if len(keys) > 2 and all(k["pos"] == keys[0]["pos"] and k["yaw"] == keys[0]["yaw"] and k["pitch"] == keys[0]["pitch"]
                             for k in keys):
        # static camera: keep a few keys for the zoom only
        n = len(keys)
        keys = [keys[i] for i in sorted(set([0, n // 4, n // 2, 3 * n // 4, n - 1]))]
    return keys


CLIPS, REPORT = [], []


def add(num, a_s, b_s, s, note="", cfg=None, name=None):
    a, b = tk(a_s), tk(b_s)
    if s is None:
        REPORT.append((num, a_s, b_s, "FAILED: no camera met the rules", note))
        return
    if s == "native_pov":
        CLIPS.append({"type": "ticks", "start": a, "end": b, "pov": CNAME, "view": "first", "why": f"#{num} {note}"})
        REPORT.append((num, a_s, b_s, "native POV", note))
        return
    clip = {"type": "ticks", "start": a, "end": b, "pov": CNAME, "why": f"#{num} {note}",
            "camera": {"shot": "keys", "interp": "linear" if len(s["ticks"]) > 6 else "cubic", "keys": keys_of(s, a)}}
    if cfg:
        clip["cfg"] = cfg
    CLIPS.append(clip)
    cv, li, lf = s["res"]
    flags = []
    if cv < 0.85:
        flags.append(f"C visible only {cv:.0%}")
    if li > 0:
        flags.append(f"LL in frame {li:.0%}")
    if lf > 0:
        flags.append(f"LL FRONT {lf:.0%}")
    if s.get("camok") is False:
        flags.append("camera near geometry")
    REPORT.append((num, a_s, b_s, f"C {cv:.0%} | LL {li:.0%} | front {lf:.0%}" + (" | " + "; ".join(flags) if flags else ""),
                   note))
    print(f"#{num:>3} {a_s}-{b_s}  C {cv:.0%}  LL {li:.0%}  front {lf:.0%}  {note}", flush=True)


def look_at(cam, target):
    import campath
    return tuple(float(v) for v in campath.look_at(tuple(cam), tuple(target)))


def fixed(a_s, b_s, cam, target, fov, zoom=1.0):
    t = np.arange(tk(a_s), tk(b_s) + 1, 2)
    n = len(t)
    p, y = look_at(cam, target)
    s = dict(ticks=t, cam=np.tile(np.asarray(cam, float), (n, 1)), pitch=np.full(n, p), yaw=np.full(n, y),
             fov=zoom_track(fov, zoom, n))
    s["res"] = evaluate(t, s["cam"], s["pitch"], s["yaw"], s["fov"])
    return s


def reuse(src, a_s, b_s):
    if src is None:
        return None
    t = np.arange(tk(a_s), tk(b_s) + 1, 2)
    n = len(t)
    m = len(src["cam"]) // 2
    s = dict(ticks=t, cam=np.tile(src["cam"][m], (n, 1)), pitch=np.full(n, float(src["pitch"][m])),
             yaw=np.full(n, float(src["yaw"][m])), fov=np.full(n, float(src["fov"][-1])))
    s["res"] = evaluate(t, s["cam"], s["pitch"], s["yaw"], s["fov"])
    return s


def st(a_s, b_s, **kw):
    return solve_static(tk(a_s), tk(b_s), **kw)


# ======================================================================================================================
# Shots (numbers = the planning chat's A1 v2 list). Times adjusted where the data required it (see notes).
# ======================================================================================================================
def main():
    C_WALL = np.array([-2730.0, 362.0, -5836.0])          # C's eye spot at W28 while staring north (1:24-1:31)
    LL_W25 = np.array([-2686.0, 1124.0, -5846.0])         # LL standing in the lit doorway
    T14 = np.array([-3472.0, 3921.0, -5844.0])            # LL in the dark pillar hall
    SMOKE = np.array([-650.0, 5135.0, -5850.0])           # smoke landing point (AE9)

    def cpos(s):
        return sample(TC, np.array([tk(s)]))[0][0]

    add(1, "0:25.9", "0:33.5", st("0:25.9", "0:33.5", rel="left", anchor="start", dists=(180, 240, 320, 420), margin=1.25),
        "transition, C's left side, locked")
    add(2, "0:33.5", "0:39", "native_pov", "POV, looks around")
    add(3, "0:39", "0:46", st("0:39", "0:46", rel="front", dists=(220, 320, 450), fov_range=(40, 95), margin=1.35),
        "liminal wide")
    add(4, "1:06", "1:16", st("1:06", "1:16", absb=0, dists=(500, 700, 900, 1100), margin=1.8, zoom=0.7,
                               avoid_yaw=(90, 60)), "slow zoom 100->70 from the east, not north")
    add(5, "1:16", "1:22.5", st("1:16", "1:22.5", rel="left", dists=(90, 130, 170), heights=(18, 30), margin=1.1,
                                 fov_range=(30, 85)), "low close along the wall")
    add(6, "1:22.5", "1:26", st("1:22.5", "1:26", absb=0, dists=(200, 280, 360), margin=1.4, avoid_yaw=(90, 50)),
        "side-on, corridor out of frame")
    add(7, "1:26", "1:31.4", shot_pov_keys(tk("1:26"), tk("1:31.4"), fov0=90, zoom=0.97),
        "POV + lens creep: LL tiny in the doorway (intended)")
    add(8, "1:32.3", "1:39", shot_ots_c(tk("1:32.3"), tk("1:39")), "OTS right shoulder, backing away")
    add(9, "1:44", "1:51", fixed("1:44", "1:51", C_WALL, LL_W25, 90.0, zoom=0.85),
        "the abyss: slow zoom on the W25 doorway, C not in frame, LL tiny (intended)")
    add(10, "1:51", "1:58", st("1:51", "1:58", absb=53, dists=(160, 220, 300), margin=1.3), "from the NE, medium")
    add(11, "1:58", "2:06", fixed("1:58", "2:06", [-3112.0, 696.0, -5830.0],
                                  sample(TC, np.arange(tk("1:58"), tk("2:06"), 8))[0].mean(0) + [0, 0, 10], 85.0),
        "high angle (ceiling is only 144 u up, so as high as the room allows)")
    add(12, "2:06", "2:14", st("2:06", "2:14", rel="back-left", dists=(400, 550, 700), margin=3.0, fov_range=(45, 90),
                               avoid_yaw=(40, 55)), "wide, small in the room, not toward W25")
    add(13, "2:14", "2:21", st("2:14", "2:21", rel="front-right", dists=(170, 230, 300), margin=1.3), "front-side medium")
    add(14, "2:21", "2:26", st("2:21", "2:26", rel="ahead", anchor="end", dists=(140, 220, 300), need_c=0.7,
                               fov_range=(40, 85), margin=1.3, avoid_yaw=(90, 35)),
        "arrival, ahead on his path, W25 corridor out of frame")
    s15 = shot_behind_ll(tk("2:26"), tk("2:30.5"), dist=120, side=-18, up=14, weight=0.97, fov=35)
    add(15, "2:26", "2:30.5", s15, "THE REVEAL: behind LL's head, C far")
    add(16, "2:30.5", "2:33.5", st("2:30.5", "2:33.5", absb=90, dists=(80, 100, 120), fit=False, fixed_fov=38, need_c=0.8,
                                   aim_point=sample(TC, np.arange(tk("2:30.5"), tk("2:33.5"), 8))[0].mean(0) + [0, 0, 62]),
        "close on C's face, LL behind the camera")
    add(17, "2:34", "2:38", reuse(s15, "2:34", "2:38"), "hold, same frame as #15, C gone")
    add(18, "2:40", "2:45", shot_cine("ground", tk("2:40"), tk("2:45"), {"facing": "away"}), "ground lock-off, runs away")
    add(19, "2:48", "2:54", shot_cine("tripod", tk("2:48"), tk("2:54"), {"angle": "front-right", "distance": 850}), "tripod pan, sprint north")
    add(20, "2:54", "3:01", st("2:54", "3:01", rel="ahead", anchor="end", dists=(150, 250, 350), need_c=0.55, fit=False,
                               fixed_fov=82), "liminal, runs through")
    add(21, "3:03", "3:10.3", st("3:03", "3:10.3", absb=90, anchor="end", dists=(200, 300, 400), need_c=0.8, margin=1.4),
        "arrival, north of U19 (ends before LL arrives)")
    add(22, "3:11.5", "3:17.3", shot_behind_ll(tk("3:11.5"), tk("3:17.3"), dist=90, side=-25, up=8, weight=0.95, static=False, fov=55, glide=40,
                                               follow_facing=True),
        "IT'S RIGHT BEHIND YOU: LL's back foreground, C sharp")
    add(23, "3:25.5", "3:34", shot_pull(tk("3:25.5"), tk("3:34"), bearing_deg=-150, d0=150, d1=420, h0=50, h1=220, fov=58),
        "pull-out/rise from the SW; framed away from the lit end of the pillar hall where LL stands")
    s24 = st("3:34", "3:43", absb=180, dists=(160, 220, 300), margin=1.3)
    add(24, "3:34", "3:43", s24, "side-on from the west")
    add(25, "3:43", "3:53", st("3:43", "3:53", absb=90, dists=(120, 170, 230), margin=1.3, zoom=0.7, fov_range=(25, 85)),
        "push toward his face 100->70 (north of him)")
    add(26, "3:53", "3:57.4", reuse(s24, "3:53", "3:57.4"), "same framing as #24, hold")
    c = cpos("3:57.4")
    wcam = c + [-70.0, -20.0, 60.0]
    add(27, "3:57.4", "3:58.9", shot_whip(tk("3:57.4"), tk("3:58.9"), wcam, c + [0, 0, 50], T14, whip=0.28),
        "WHIP PAN to the darkness (LL there, invisible)")
    add(28, "3:58.9", "4:01.5", shot_whip(tk("3:58.9"), tk("4:01.5"), wcam, T14, c + [0, 0, 45], whip=0.28),
        "WHIP PAN back; he has bolted (3:59.3)")
    add(29, "4:05", "4:10", shot_cine("ground", tk("4:05"), tk("4:10"), {"facing": "toward"}), "ground lock-off, toward")
    add(30, "4:10", "4:15", st("4:10", "4:15", rel="front-left", dists=(350, 500, 650), fit=False, fixed_fov=80, need_c=0.8),
        "wide, small in the space")
    add(31, "4:15", "4:18", shot_cine("tripod", tk("4:15"), tk("4:18"), {"angle": "front-left"}), "tripod pan")
    add(32, "4:19", "4:23", st("4:19", "4:23", rel="behind-travel", anchor="start", dists=(120, 200), need_c=0.5, fit=False,
                               fixed_fov=78, aim_point=cpos("4:22") + [0, 0, 50]), "liminal, looking west, runs into frame")
    add(33, "4:25", "4:29.5", st("4:25", "4:29.5", rel="left", dists=(200, 280, 360), margin=1.3), "side-on, LL framed out")
    add(34, "4:29.5", "4:31.0", shot_behind_ll(tk("4:29.5"), tk("4:31.0"), dist=90, side=18, up=10, static=False,
                                               weight=0.8, handheld=1.0, fov=70), "SURGE: behind LL, handheld")
    s35 = shot_behind_ll(tk("4:36"), tk("4:38"), dist=90, side=-20, up=6, weight=0.85, fov=60)
    s35 = reuse(s35, "4:36", "4:40.5")
    add(35, "4:36", "4:40.5", s35, "hold behind LL at Q19, C sprints away")
    add(36, "5:41", "5:48", st("5:41", "5:48", rel="ahead", anchor="end", dists=(150, 250, 350), need_c=0.5, fit=False,
                               fixed_fov=80), "liminal, runs into frame")
    add(37, "5:48", "5:55", shot_cine("ground", tk("5:48"), tk("5:55"), {"facing": "away"}), "ground lock-off, away")
    add(38, "6:13", "6:21", st("6:13", "6:21", rel="front-left", dists=(350, 500, 650), fit=False, fixed_fov=82, need_c=0.8),
        "wide on the small pillars")
    add(39, "6:21", "6:26", st("6:21", "6:26", rel="front-left", dists=(200, 280, 360), margin=1.3, need_c=0.85),
        "medium as he turns west")
    add(40, "6:26", "6:40.5", st("6:26", "6:40.5", rel="left", dists=(300, 420, 550), margin=1.5, zoom=0.9,
                                 fov_range=(35, 85)), "side-on along row 25, subtle zoom")
    add(41, "13:01", "13:10", st("13:01", "13:10", absb=0, dists=(500, 650, 800), margin=2.4, fov_range=(30, 85)),
        "wide from the pit side, LL out (north)")
    add(42, "13:10", "13:17.5", st("13:10", "13:17.5", absb=0, dists=(300, 400, 500), margin=1.6, zoom=0.8),
        "slow zoom in, same axis")
    t43 = np.arange(tk("13:17.5"), tk("13:21") + 1, 2)
    add(43, "13:17.5", "13:21", fixed("13:17.5", "13:21", SMOKE, sample(TC, t43)[0].mean(0) + [0, 0, 46], 70.0),
        "at the smoke's landing point facing C; smoke fills frame ~13:19.3")
    add(44, "13:40", "13:49", st("13:40", "13:49", absb=0, dists=(450, 600, 750), margin=1.8, zoom=0.8),
        "slow zoom from the pit side")
    add(45, "13:49.2", "13:51.3", shot_creature_lead(tk("13:49.2"), tk("13:51.3")), "creature lead (LL's real walk)")
    add(46, "13:50.5", "13:54.5", st("13:50.5", "13:54.5", absb=90, dists=(200, 280, 360), margin=1.3),
        "side-on; his real head turn E->NE at 13:51.5-13:53.5")
    add(47, "14:10", "14:16", st("14:10", "14:16", rel="front-left", dists=(180, 250), margin=1.3), "corner, front-side")
    add(48, "14:20", "14:25", st("14:20", "14:25", rel="front-left", dists=(80, 110, 140), margin=1.1, fov_range=(18, 80)),
        "tight; he snaps back east at 14:23.8")
    add(49, "14:34.5", "14:38.2", fixed("14:34.5", "14:38.2", [-1290.0, 5060.0, -5836.0],
                                        sample(TC, np.arange(tk("14:34.5"), tk("14:38.2"), 8))[0].mean(0) + [0, 0, 46], 45.0),
        "from the corner: he backs away toward the pit; cut 14:38.2")
    add(50, "14:36.5", "14:38.2", "native_pov", "POV: he faces NW back at the room while backing up; cut 14:38.2")
    add(51, "14:42.6", "14:46", st("14:42.6", "14:46", absb=0, dists=(300, 400, 500), margin=1.4),
        "back to the corner, after the push-out")
    add(52, "15:09.3", "15:11.4", shot_creature_lead(tk("15:09.3"), tk("15:11.4")), "creature lead, LL's walk-in")
    t53 = np.arange(tk("15:19.5"), tk("15:24.5") + 1, 2)
    add(53, "15:19.5", "15:24.5", fixed("15:19.5", "15:24.5", [-1150.0, 5300.0, -5838.0],
                                        sample(TC, t53)[0].mean(0) + [0, 0, 40], 45.0),
        "LL walks into frame from behind the camera, C beyond")
    add(54, "15:24.5", "15:29.0", fixed("15:24.5", "15:29.0", cpos("15:24.5") + [-70.0, -18.0, 70.0], SMOKE, 62.0),
        "over C's shoulder toward the smoke landing")
    add(55, "15:29.0", "15:34", shot_behind_ll(tk("15:29.0"), tk("15:34"), dist=60, side=12, weight=0.9),
        "behind LL at AC8 as C turns to face him (LL settles 15:29.6; clip starts 15:27.5)")
    add(56, "15:34", "15:39.5", fixed("15:34", "15:39.5", [-1290.0, 5060.0, -5836.0],
                                      sample(TC, np.arange(tk("15:34"), tk("15:39.5"), 8))[0].mean(0) + [0, 0, 46], 40.0),
        "from the corner: he backs away into the smoke, toward the pit")
    add(57, "15:39.5", "15:42.1", fixed("15:39.5", "15:42.1", [-420.0, 5260.0, -5800.0], [-430.0, 5090.0, -5950.0], 90.0),
        "SLOW MOTION 2x: over the edge 15:40.5, cut 15:42.1",
        cfg="demo_timescale 0.5")
    s35p = reuse(s35, "11:00", "11:04")
    add("35p", "11:00", "11:04", s35p, "CLEAN PLATE of #35's frame (nobody in frame) - not part of the edit",
        cfg="demo_timescale 1")

    spec = {"name": "a1-v2", "summary": "videos/demos/A1.summary.json", "outputFileName": "A1-v2.1",
            "concatenate": True, "order": "spec", "clips": CLIPS,
            # map maker's post-process off (its vignette), its tone curve restored in our grade (measured 2026-09-28)
            "cfg": "r_csgo_postprocess_enable 0",
            "finish": {"look": "cinematic", "letterbox": True, "vignette": False,
                       "curves": "0/0 0.088/0.035 0.136/0.075 0.193/0.136 0.293/0.254 0.409/0.401 1/1"}}
    out = REPO / "videos" / "specs" / "a1-v2.json"
    out.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    lines = ["| # | time | check | note |", "|---|---|---|---|"] + [
        f"| {n} | {a}–{b} | {r} | {note} |" for n, a, b, r, note in REPORT]
    (REPO / "videos" / "specs" / "a1v2" / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", out, len(CLIPS), "clips")


if __name__ == "__main__":
    main()
