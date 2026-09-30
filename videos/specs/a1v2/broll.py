"""Part 1 B-roll builder (overnight run, 2026-09-29/30): several cinematic angles per scene for A2, C1, D1, G1.

User direction (overnight-plan.md): focus on cinematic B-roll of Caillou; several angles per scene to choose from;
variant "a" is ALWAYS the still/static/slow-zoom option; the others are specials (tripod, side, crane, drone, arc,
push/pull, ground, dolly zoom, lead/follow). Rules: no noclip on screen; LL's front never readable (3D check: view
yaw + pitch) unless tiny (< 6% of frame height) or in the jungle-temple finale; campath gap rule (0 or not 40-100).

  python videos/specs/a1v2/broll.py build <DEMO>    -> specs broll-<demo>-pN.json + broll-<demo>-order.json + report
  python videos/specs/a1v2/broll.py export <DEMO>   -> graded, no-bar, 8-bit files + index.md + contact sheet
"""
from __future__ import annotations

import glob
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

import build as B
import mapgeo
import positions
from build import tk, sample, fixed, shot_cine, shot_behind_ll

REPO = B.REPO
DEMO_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo")
RENDERS = Path(r"C:\Users\Liam's PC\Videos\CS2 Renders")
PART1 = RENDERS / "Backrooms Part 1"          # the user's layout (2026-09-30): one folder per script scene
SCENE_DIRS = {"A2": "2 - Poolrooms (script scene 9)", "C1": "3 - School (script scene 11)",
              "D1": "4 - Insertion2 flash (script scene 13)", "G1": "5 - Jungle temple (script scene 17)"}
PASS_ROOTS = [RENDERS, PART1 / "7 - Work files (safe to delete)" / "B-roll passes"]


def pass_shots(pass_name, start, end):
    for root in PASS_ROOTS:
        hits = glob.glob(str(root / f"{pass_name.upper()}-shots" / f"sequence-*-tick-{start}-to-{end}.mp4"))
        if hits:
            return hits
    return []
FINISH = {"look": "cinematic", "letterbox": False, "vignette": False,
          "curves": "0/0 0.088/0.035 0.136/0.075 0.193/0.136 0.293/0.254 0.409/0.401 1/1"}
CNAME, LNAME = "Caillou (Canadian now)", "Lightning Lemur"


def fmt(sec):
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


# ---------------------------------------------------------------------------------------------------------------------
# demo setup
# ---------------------------------------------------------------------------------------------------------------------
def setup(demo):
    summary = json.loads((REPO / "videos" / "demos" / f"{demo}.summary.json").read_text(encoding="utf-8"))
    tracks = {t.name: t for t in positions.load_tracks(str(DEMO_DIR / f"{demo}.dem")).values()}
    B.TC = tracks[CNAME]
    if LNAME in tracks:
        B.TL = tracks[LNAME]
    else:
        n = len(B.TC.tick)
        B.TL = positions.Track("0", "none", B.TC.tick.copy(), np.tile([0.0, 0.0, -30000.0], (n, 1)), np.zeros(n),
                               np.zeros(n), np.ones(n, bool), np.zeros(n))
    B.G = mapgeo.MapGeometry.for_map(summary["map"])
    if summary["map"] in OUTDOOR:
        B.cam_ok = cam_ok_outdoor     # the indoor check rejects open sky, i.e. every outdoor spot
    return summary


OUTDOOR = {"de_lord"}


def cam_ok_outdoor(p) -> bool:
    for a in range(0, 360, 45):
        d = np.array([math.cos(math.radians(a)), math.sin(math.radians(a)), 0.0])
        if not B.G.clear(p, p + d * 14):
            return False
    return B.G.clear(p, p + [0, 0, -14.0]) and B.G.clear(p, p + [0, 0, 14.0])


def fast_windows(track, pad=0.35):
    """Noclip / teleport windows (seconds) of a track."""
    t = np.arange(track.tick[0], track.tick[-1], 8)
    p = sample(track, t)[0]
    hsp = np.r_[np.linalg.norm(np.diff(p[:, :2], axis=0), axis=1), 0] * 8
    vz = np.abs(np.r_[np.diff(p[:, 2]), 0]) * 8
    bad = (hsp > 320) | ((vz > 350) & (hsp > 100))
    out, s = [], None
    for k in range(len(bad) + 1):
        on = k < len(bad) and bad[k]
        if on and s is None:
            s = k
        if not on and s is not None:
            out.append((t[s] / 64 - pad, t[k - 1] / 64 + pad))
            s = None
    return out


def overlaps(a, b, wins):
    return any(a < y and b > x for x, y in wins)


# ---------------------------------------------------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------------------------------------------------
def ll_bad(s, policy, ll_fast):
    """Samples breaking the LL rule. policy: out | back | reveal."""
    if policy == "reveal":
        return 0
    ticks = s["ticks"]
    lb = B.body(B.TL, ticks)
    _, lyaw, lpitch, _ = sample(B.TL, ticks)
    bad = 0
    for i in range(0, len(ticks), 2):
        cam = s["cam"][i]
        inf = B.cine._in_frame(cam, float(s["pitch"][i]), float(s["yaw"][i]), float(s["fov"][i]), lb[i],
                               margin=B.LL_MARGIN)
        if not any(inf[j] and B.G.clear(cam, lb[i][j]) for j in range(4)):
            continue
        if overlaps(ticks[i] / 64, ticks[i] / 64 + 0.01, ll_fast):
            bad += 1          # LL noclipping on screen
            continue
        if policy == "out":
            bad += 1
            continue
        head = lb[i][3]
        dist = float(np.linalg.norm(head - cam))
        vfov = 2 * math.atan(math.tan(math.radians(float(s["fov"][i])) / 2) / 0.75 * 9 / 16)
        size = 72 / (2 * dist * math.tan(vfov / 2))
        if size < 0.06:
            continue          # tiny: an unreadable silhouette is allowed
        y, p = math.radians(float(lyaw[i])), math.radians(float(lpitch[i]))
        face = np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p)])
        d = (cam - head) / max(dist, 1e-6)
        if float(face @ d) > -0.5:   # camera less than 120 deg behind the face direction
            bad += 1
    return bad


# ---------------------------------------------------------------------------------------------------------------------
# variant makers: each returns (angle name, shot) or None
# ---------------------------------------------------------------------------------------------------------------------
def v_static(sc):
    ll = "out" if sc["ll"] == "out" else "backonly"
    tries = ([dict(rel="ahead", anchor="end")] if sc["move"] else []) + [
        dict(rel="front-left"), dict(rel="front-right"), dict(rel="left"), dict(rel="right"), dict(absb=None)]
    for kw in tries:
        for dists in ((260, 360, 480), (420, 600, 800)):
            s = B.st(sc["a"], sc["b"], dists=dists, margin=1.4, zoom=0.85, need_c=0.6 if sc["move"] else 0.8,
                     ll=ll, fov_range=(30, 85), **{k: v for k, v in kw.items() if v is not None})
            if s is not None:
                return ("static, slow zoom", s)
    # fallback: the shot engine's own static placement
    try:
        s = shot_cine("static", tk(sc["a"]), tk(sc["b"]), {}, track=B.TC)
        return ("static", s)
    except Exception:
        return None


def v_cine(kind, params=None, label=None):
    def make(sc):
        try:
            s = shot_cine(kind, tk(sc["a"]), tk(sc["b"]), params or {}, track=B.TC)
        except Exception:
            return None
        return (label or kind, s)
    return make


SPECIALS_MOVE = [v_cine("tripod", {"size": "wide"}, "tripod pan (wide)"), v_cine("side", None, "side tracking"),
                 v_cine("crane", None, "crane"), v_cine("drone", {"move": "flyover"}, "drone flyover"),
                 v_cine("lead", None, "leading"), v_cine("ground", {"facing": "away"}, "ground level"),
                 v_cine("follow", None, "following")]
SPECIALS_STILL = [v_cine("arc", None, "slow arc"), v_cine("push", None, "slow push-in"),
                  v_cine("crane", None, "crane"), v_cine("drone", {"move": "orbit"}, "drone orbit"),
                  v_cine("dolly_zoom", None, "dolly zoom"), v_cine("pull", None, "pull-out"),
                  v_cine("tripod", {"size": "full"}, "tripod")]


def accept(sc, shot, ll_fast, c_fast, need):
    if shot is None:
        return False, "no camera"
    if not sc.get("allow_noclip") and overlaps(tk(sc["a"]) / 64, tk(sc["b"]) / 64, c_fast):
        return False, "C noclip"
    cv = shot["res"][0]
    if cv < need:
        return False, f"C visible {cv:.0%}"
    bad = ll_bad(shot, sc["ll"], ll_fast)
    if bad:
        return False, f"LL rule ({bad})"
    return True, ""


# ---------------------------------------------------------------------------------------------------------------------
# scenes per demo  (n, title, a, b, move?, ll policy, extra)
# ---------------------------------------------------------------------------------------------------------------------
def S(n, title, a, b, move=True, ll="out", **kw):
    return dict(n=n, title=title, a=a, b=b, move=move, ll=ll, **kw)


def scenes_for(demo):
    if demo == "A2":
        return [
            S(1, "crossing the Poolrooms", "0:06", "0:12.5"),
            S(2, "deeper into the Poolrooms", "0:13", "0:19.5"),
            S(3, "heading past the pools", "0:26", "0:32"),
            S(4, "a still-life under the water (1)", "0:59.2", "1:04.4", ll="back", water=True),
            S(5, "a still-life under the water (2)", "1:57.4", "2:03.4", ll="back", water=True),
            S(6, "a still-life under the water (3)", "2:14.6", "2:22.4", ll="back", water=True),
            S(7, "a still-life under the water (4)", "2:24.6", "2:30", ll="back", water=True),
            S(8, "on toward the edge", "2:50.8", "2:56.4"),
            S(9, "running to the edge", "3:15.4", "3:22.4"),
            S(10, "the edge: he looks down ('Quack, quack')", "3:28", "3:32.8", move=False),
            S(11, "the edge, the duck ('Should I jump?')", "3:36.2", "3:48", move=False),
            S(12, "along the ledge", "3:59.6", "4:06.8"),
            S(13, "crouched at the edge, looking down", "4:09.4", "4:20.4", move=False),
            S(14, "the run-up and the jump", "4:31.5", "4:40", allow_noclip=True),  # the fall itself trips the detector; cut before the 4:40 teleport
        ]
    if demo == "C1":
        return [dict(sc, ll=("back" if sc["ll"] == "out" else sc["ll"])) for sc in [
            S(1, "ARRIVAL, first person (bodycam)", "0:40", "0:48", pov=True, allow_noclip=True),
            S(2, "the lamp poles (lingering)", "0:43", "0:55.8", move=False),
            S(3, "he turns around: the school", "0:56", "1:04.3", move=False),
            S(4, "walking around the school", "1:08.5", "1:18"),
            S(5, "toward the flags", "1:19", "1:30.3"),
            S(6, "the flags", "1:32.8", "1:42.5"),
            S(7, "into the building", "1:44", "1:58.5"),
            S(8, "the stopwatch, the halls", "1:58.8", "2:10", move=False),
            S(9, "the blown-open door (aftermath)", "2:26.6", "2:35"),
            S(10, "exploring: the classroom", "2:35", "2:45"),
            S(11, "exploring the halls", "2:45", "2:57.5"),
            S(12, "looking around", "3:00.8", "3:06"),
            S(13, "outside: jumping at the high windows", "3:41.3", "3:47.5"),
            S(14, "the locked door (the girl's voice)", "4:37", "4:45"),
            S(15, "the locked door, again", "4:45", "4:53"),
            S(16, "upstairs: the window over the trees", "4:54", "5:00", move=False),
            S(17, "the courtyard, the hopscotch", "5:12", "5:21", ll="back"),
            S(18, "THIRD FLOOR: the still-life watches him (behind its head)", "7:41.5", "7:58.5", ll="back",
              behind_ll=True, need=0.2),  # he moves in and out of view behind the window: that's the shot
            S(19, "toward the gap in the grass", "7:08", "7:19.2"),
        ]]
    if demo in ("D1", "G1"):
        return auto_scenes(demo)
    raise SystemExit(f"no scenes for {demo}")


def auto_scenes(demo, chunk=6.5, min_len=3.0):
    """Chunk every clean stretch of Caillou's movement into scenes (D1: follow him to the end; G1: exploring)."""
    c_fast = fast_windows(B.TC)
    t0, t1 = B.TC.tick[0] / 64, B.TC.tick[-1] / 64
    alive_end = t1
    if demo == "G1":
        alive_end = 3 * 60 + 51.5      # he dies (falls out) at 3:51.8
    cuts = sorted([(max(t0, x), min(alive_end, y)) for x, y in c_fast if x < alive_end])
    free, cur = [], max(t0, 1.0)
    for x, y in cuts:
        if x - cur >= min_len:
            free.append((cur, x))
        cur = max(cur, y)
    if alive_end - cur >= min_len:
        free.append((cur, alive_end - 0.3))
    out, n = [], 1
    for x, y in free:
        k = x
        while y - k >= min_len:
            e = min(y, k + chunk)
            if y - e < min_len:
                e = y
            ll = "back"
            out.append(S(n, f"{'following Caillou' if demo == 'D1' else 'the jungle temple'} ({fmt(k)})", fmt(k),
                         fmt(e), move=True, ll=ll))
            n += 1
            k = e
    if demo == "D1":
        # a flash scene in the edit: thin out the middle (every 3rd moment); keep all of the road + bridge ending
        keep = [sc for k, sc in enumerate(out) if tk(sc["a"]) / 64 >= 270 or k % 3 == 0]
        out = [dict(sc, n=k + 1) for k, sc in enumerate(keep)]
        n = len(out) + 1
    if demo == "G1":
        out.append(S(n, "FINALE: around the still-life to its face (blank it in editing)", "5:03", "5:23",
                     move=False, ll="reveal", finale=True))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# custom variants
# ---------------------------------------------------------------------------------------------------------------------
def v_water(sc):
    """High above the pool, looking down through the water at the still-life (only if it stays tiny/back)."""
    lp = sample(B.TL, np.arange(tk(sc["a"]), tk(sc["b"]) + 1, 8))[0].mean(0) + [0, 0, 40]
    for off in ((60, -60, 520), (-60, 60, 520), (120, 0, 480), (0, 120, 480), (0, 0, 560)):
        cam = lp + np.array(off, float)
        if not B.cam_ok(cam):
            continue
        s = fixed(sc["a"], sc["b"], cam, lp, 42.0, zoom=0.9)
        return ("looking down through the water", s)
    return None


def v_behind_ll(dist, side, fov, label, push=False):
    def make(sc):
        if not push:
            return (label, shot_behind_ll(tk(sc["a"]), tk(sc["b"]), dist=dist, side=side, up=8, weight=0.9, fov=fov))
        a, b = tk(sc["a"]), tk(sc["b"])
        s0 = shot_behind_ll(a, b, dist=dist, side=side, up=8, weight=0.9, fov=fov)
        s1 = shot_behind_ll(a, b, dist=max(40, dist * 0.45), side=side * 0.5, up=8, weight=0.9, fov=fov)
        f = np.linspace(0, 1, len(s0["ticks"]))[:, None]
        f = f * f * (3 - 2 * f)
        cam = s0["cam"] * (1 - f) + s1["cam"] * f
        target = np.tile(B.eye(B.TC, s0["ticks"]).mean(0) - [0, 0, 10], (len(f), 1))
        p, y = B.look(cam, target)
        s = dict(ticks=s0["ticks"], cam=cam, pitch=p, yaw=y, fov=s0["fov"])
        s["res"] = B.evaluate(s["ticks"], cam, p, y, s["fov"])
        return (label, s)
    return make


def v_finale(sc):
    """A slow orbit from behind the still-life's head around to its face (the only allowed face shot). The radius
    adapts per angle to stay clear of pillars (nearest clear distance to the previous one)."""
    a, b = tk(sc["a"]), tk(sc["b"])
    t = np.arange(a, b + 1, 2)
    lp, lyaw, _, _ = sample(B.TL, t)
    head = lp.mean(0) + [0, 0, 60]
    y0 = float(np.median(lyaw))
    best = None
    for sgn in (1, -1):
        path, r_prev, fails = [], 130.0, 0
        for k in range(0, 181, 5):
            ang = math.radians(y0 + 180 + sgn * k)
            opts = [r for r in range(70, 241, 10)
                    if B.cam_ok(head + [r * math.cos(ang), r * math.sin(ang), 15])]
            if not opts:
                fails += 1
                path.append(r_prev)
                continue
            r_prev = min(opts, key=lambda r: abs(r - r_prev))
            path.append(float(r_prev))
        if best is None or fails < best[0]:
            best = (fails, sgn, path)
    fails, sgn, path = best
    if fails > 4:
        return None
    n = len(t)
    f = np.linspace(0, 1, n)
    f = f * f * (3 - 2 * f)
    kk = f * 180
    radius = np.interp(kk, np.arange(0, 181, 5), B.smooth(np.array(path), 2) if len(path) > 4 else np.array(path))
    ang = np.radians(y0 + 180 + sgn * kk)
    cam = np.c_[head[0] + radius * np.cos(ang), head[1] + radius * np.sin(ang), head[2] + 15 - 15 * f]
    p, y = B.look(cam, np.tile(head, (n, 1)))
    s = dict(ticks=t, cam=cam, pitch=p, yaw=y, fov=np.full(n, 45.0))
    s["res"] = (1.0, 1.0, 1.0)
    return ("orbit from behind to its face", s)


def variants_for(sc):
    if sc.get("pov"):
        return [("first person (bodycam: hands, no crosshair)", "native_pov")]
    if sc.get("finale"):
        v = v_finale(sc)
        return [v] if v else []
    makers = []
    if sc.get("behind_ll"):
        makers = [v_behind_ll(70, 16, 40, "behind its head (static)"),
                  v_behind_ll(110, -20, 32, "behind its head, slow push-in", push=True),
                  v_behind_ll(55, 30, 50, "over its shoulder (wide)")]
        return [m(sc) for m in makers]
    out = [v_static(sc)]
    if sc.get("water"):
        out.append(v_water(sc))
    out.append(("__specials__", None))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------------------------------
def build(demo):
    setup(demo)
    c_fast, ll_fast = fast_windows(B.TC), fast_windows(B.TL)
    scenes = scenes_for(demo)
    shots, report = [], [f"# B-roll {demo}", "", "| shot | demo time | angle | C | result |", "|---|---|---|---|---|"]
    for sc in scenes:
        got = []
        for item in variants_for(sc):
            if item is None:
                continue
            label, shot = item
            if label == "__specials__":
                pool = SPECIALS_MOVE if sc["move"] else SPECIALS_STILL
                for mk in pool:
                    if len(got) >= 3:
                        break
                    r = mk(sc)
                    if r is None:
                        continue
                    ok, why = accept(sc, r[1], ll_fast, c_fast, 0.55 if sc["move"] else 0.7)
                    if ok and r[0] not in [g[0] for g in got]:
                        got.append(r)
                continue
            if shot == "native_pov":
                got.append((label, shot))
                continue
            ok, why = accept(sc, shot, ll_fast, c_fast, sc.get("need", 0.0 if sc.get("water") else 0.5))
            if ok:
                got.append((label, shot))
            else:
                report.append(f"| {sc['n']:02d} | {sc['a']}–{sc['b']} | {label} | – | rejected: {why} |")
        for k, (label, shot) in enumerate(got):
            letter = "abcdefg"[k]
            cv = 1.0 if shot == "native_pov" else shot["res"][0]
            shots.append(dict(n=sc["n"], letter=letter, title=sc["title"], label=label, a=sc["a"], b=sc["b"], shot=shot,
                              cfg="crosshair 0\ncl_crosshairalpha 0" if shot == "native_pov" else None))
            report.append(f"| {sc['n']:02d}{letter} | {sc['a']}–{sc['b']} | {label} | {cv:.0%} | ok |")
        if not got:
            report.append(f"| {sc['n']:02d} | {sc['a']}–{sc['b']} | – | – | **no usable angle** |")
    # clips + passes
    B.CLIPS.clear()
    B.REPORT.clear()
    for sh in shots:
        B.add(f"{demo}-{sh['n']:02d}{sh['letter']}", sh["a"], sh["b"], sh["shot"], sh["title"], cfg=sh["cfg"])
        if sh["cfg"] and "cfg" not in B.CLIPS[-1]:
            B.CLIPS[-1]["cfg"] = sh["cfg"]   # B.add drops cfg for native POV clips
    clips = sorted(B.CLIPS, key=lambda c: c["start"])
    passes = []
    for c in clips:
        for ps in passes:
            gap = c["start"] - ps[-1]["end"]
            if gap >= 0 and not 40 <= gap <= 100:
                ps.append(c)
                break
        else:
            passes.append([c])
    order = []
    for k, ps in enumerate(passes, 1):
        name = f"broll-{demo.lower()}-p{k}"
        spec = {"name": name, "summary": f"videos/demos/{demo}.summary.json", "outputFileName": name.upper(),
                "concatenate": True, "order": "spec", "clips": ps, "cfg": "r_csgo_postprocess_enable 0",
                "finish": False}
        (REPO / "videos" / "specs" / f"{name}.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        for c in ps:
            sid = c["why"].split()[0].lstrip("#")
            sh = next(s for s in shots if f"{demo}-{s['n']:02d}{s['letter']}" == sid)
            order.append(dict(id=sid, n=sh["n"], letter=sh["letter"], title=sh["title"], label=sh["label"],
                              a=sh["a"], b=sh["b"], start=c["start"], end=c["end"], pass_=name))
    order.sort(key=lambda o: (o["n"], o["letter"]))
    (REPO / "videos" / "specs" / f"broll-{demo.lower()}-order.json").write_text(
        json.dumps({"demo": demo, "finish": FINISH, "passes": [f"broll-{demo.lower()}-p{k}" for k in range(1, len(passes) + 1)],
                    "shots": order}, indent=1), encoding="utf-8")
    report += ["", f"{len(shots)} shots, {len(passes)} render passes, ~{sum((o['end'] - o['start']) / 64 for o in order):.0f} s"]
    (REPO / "videos" / "specs" / "a1v2" / f"report_broll_{demo}.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[-12:]))
    print(" ".join(f"broll-{demo.lower()}-p{k}" for k in range(1, len(passes) + 1)))


# ---------------------------------------------------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------------------------------------------------
def slug(t, n=70):
    t = re.sub(r"[\\/:*?\"<>|']", "", t)
    return re.sub(r"\s+", " ", t).strip()[:n].rstrip(" .,")


BACKROOMS_CURVE_DEMOS = {"A1", "A2", "A11", "A12", "A13"}   # the restored nino2 tone curve belongs to ze_backrooms only


def export(demo, only=None, curve=None, raw=False):
    sys.path.insert(0, str(REPO / "tools" / "csdv"))
    from post import ffmpeg_path
    ff = ffmpeg_path()
    order = json.loads((REPO / "videos" / "specs" / f"broll-{demo.lower()}-order.json").read_text(encoding="utf-8"))
    dest = PART1 / SCENE_DIRS.get(demo, f"B-roll {demo}")
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / "_tmp10bit.mp4"
    index = [f"# {demo} B-roll", "", "Variant **a** = the still / static / slow-zoom option. Times are demo time.", "",
             "| file | demo time | angle |", "|---|---|---|"]
    thumbs = []
    for o in order["shots"]:
        hits = pass_shots(o["pass_"], o["start"], o["end"])
        name = f"{demo} {o['n']:02d}{o['letter']} - {slug(o['title'], 50)} - {slug(o['label'], 30)}.mp4"
        if len(hits) != 1:
            index.append(f"| (missing) {name} | {o['a']}–{o['b']} | {o['label']} |")
            continue
        if (only is None or o["id"] in only) and raw:
            import shutil
            shutil.copyfile(hits[0], dest / name)   # ungraded: the user's preference (2026-09-30)
        elif only is None or o["id"] in only:
            use_curve = (demo in BACKROOMS_CURVE_DEMOS) if curve is None else curve
            args = [sys.executable, str(REPO / "tools" / "csdv" / "post.py"), hits[0], "--look", FINISH["look"], "-o",
                    str(tmp), "--no-vignette"] + (["--curves", FINISH["curves"]] if use_curve else [])
            subprocess.run(args, check=True, capture_output=True)
            subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp), "-vf",
                            "scale=flags=lanczos:sws_dither=ed,format=yuv420p", "-c:v", "libx264", "-profile:v", "high",
                            "-preset", "slow", "-crf", "14", "-c:a", "copy", "-movflags", "+faststart", str(dest / name)],
                           check=True)
        index.append(f"| {name} | {o['a']}–{o['b']} | {o['label']} |")
        th = dest / f"_th_{o['id']}.jpg"
        subprocess.run([ff, "-v", "error", "-y", "-ss", "1.2", "-i", str(dest / name), "-frames:v", "1", "-vf",
                        "scale=384:-1", str(th)])
        if th.exists():
            thumbs.append((f"{o['n']:02d}{o['letter']} {o['label']}", th))
        print("exported", name, flush=True)
    tmp.unlink(missing_ok=True)
    (dest / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    contact(dest / "contact-sheet.jpg", thumbs)
    for _, th in thumbs:
        th.unlink(missing_ok=True)
    print("done ->", dest)


def contact(path, thumbs, cols=5):
    from PIL import Image, ImageDraw
    if not thumbs:
        return
    w, h = 384, 216 + 22
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * w, rows * h), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for k, (label, th) in enumerate(thumbs):
        im = Image.open(th).convert("RGB").resize((384, 216))
        x, y = (k % cols) * w, (k // cols) * h
        sheet.paste(im, (x, y + 22))
        d.text((x + 4, y + 4), label[:60], fill=(255, 230, 120))
    sheet.save(path, quality=85)


def fix(demo, n, a, b, allow_noclip=True, ll="out", move=True, title=None, **extra):
    """Shoot one scene again (new time window) as separate passes and replace it in the demo's order file."""
    setup(demo)
    c_fast, ll_fast = fast_windows(B.TC), fast_windows(B.TL)
    old = json.loads((REPO / "videos" / "specs" / f"broll-{demo.lower()}-order.json").read_text(encoding="utf-8"))
    title = title or next((o["title"] for o in old["shots"] if o["n"] == n), f"scene {n}")
    sc = S(n, title, a, b, move=move, ll=ll, allow_noclip=allow_noclip, **extra)
    got = []
    for item in variants_for(sc):
        if item is None:
            continue
        label, shot = item
        if label == "__specials__":
            for mk in (SPECIALS_MOVE if move else SPECIALS_STILL):
                if len(got) >= 4:
                    break
                r = mk(sc)
                if r and accept(sc, r[1], ll_fast, c_fast, 0.55)[0] and r[0] not in [g[0] for g in got]:
                    got.append(r)
            continue
        if accept(sc, shot, ll_fast, c_fast, sc.get("need", 0.5))[0]:
            got.append((label, shot))
    shots = [o for o in old["shots"] if o["n"] != n]
    passes = list(old["passes"])
    for k, (label, shot) in enumerate(got):
        B.CLIPS.clear()
        B.REPORT.clear()
        sid = f"{demo}-{n:02d}{'abcdefg'[k]}"
        B.add(sid, a, b, shot, title)
        name = f"broll-{demo.lower()}-fix{n:02d}{'abcdefg'[k]}"
        spec = {"name": name, "summary": f"videos/demos/{demo}.summary.json", "outputFileName": name.upper(),
                "concatenate": True, "order": "spec", "clips": list(B.CLIPS), "cfg": "r_csgo_postprocess_enable 0",
                "finish": False}
        (REPO / "videos" / "specs" / f"{name}.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
        c = B.CLIPS[0]
        shots.append(dict(id=sid, n=n, letter="abcdefg"[k], title=title, label=label, a=a, b=b, start=c["start"],
                          end=c["end"], pass_=name))
        passes.append(name)
        print(name, label)
    shots.sort(key=lambda o: (o["n"], o["letter"]))
    old["shots"], old["passes"] = shots, passes
    (REPO / "videos" / "specs" / f"broll-{demo.lower()}-order.json").write_text(json.dumps(old, indent=1), encoding="utf-8")


if __name__ == "__main__":
    cmd, demo = sys.argv[1], sys.argv[2].upper()
    if cmd == "build":
        build(demo)
    elif cmd == "fix":
        fix(demo, int(sys.argv[3]), sys.argv[4], sys.argv[5])
    elif cmd == "export":
        only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 and sys.argv[3] != "-" else None
        mode = sys.argv[4] if len(sys.argv) > 4 else ""
        curve = {"curve": True, "nocurve": False}.get(mode)
        export(demo, only, curve, raw=(mode == "raw"))
