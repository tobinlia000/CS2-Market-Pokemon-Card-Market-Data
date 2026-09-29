"""A1 v6 (2026-09-29): the user's review of v5 (A1-direction.md)
+ v5: the user's review of v4 (videos/briefs/A1-direction.md) + the A11 B-roll demo.

Story order mixes two demos:
  A1  = the original take (both players);  A11 = Caillou's solo B-roll (spawn-corner acting, the run, the hiding corner).
Each demo renders as its own spec (a1-v6.json, a11-v6.json); the shots are joined in story order afterwards
(order file: videos/specs/a1-v6-order.json, used by join_v5.py --v6).

Rules: no noclip on screen; LL only from behind (>120 deg) or out of frame; smiley spray out of frame; C shown moving
except scripted lines; geography anchored on the spawn corner until LL drives him away.
Run: python videos/specs/a1v2/build_v5.py
"""
import json
import math

import numpy as np

import build as B
import positions
from build import tk, sample, fixed, shot_ots_c, shot_behind_ll, shot_cine, shot_whip, shot_pull, shot_creature_lead
from rev_search import strict, smiley_in

A11_DEMO = r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\A11.dem"
A1_TC, A1_TL = B.TC, B.TL
A11_TC = [t for t in positions.load_tracks(A11_DEMO).values()][0]
# A11 has no LL: a stand-in far below the map, so every LL check passes trivially
A11_TL = positions.Track("0", "none", A11_TC.tick.copy(), np.tile([0.0, 0.0, -20000.0], (len(A11_TC.tick), 1)),
                         np.zeros(len(A11_TC.tick)), np.zeros(len(A11_TC.tick)), np.ones(len(A11_TC.tick), bool),
                         np.zeros(len(A11_TC.tick)))

# noclip windows (demo seconds); a clip may not overlap them
NOCLIP = {"A1": [(164.4, 165.1), (168.2, 168.9), (189.8, 191.2), (193.3, 193.5), (199.5, 205.6), (267.8, 274.9)],
          "A11": [(0.0, 16.3), (91.8, 100.7)]}
FINISH = {"look": "cinematic", "letterbox": True, "vignette": False,
          "curves": "0/0 0.088/0.035 0.136/0.075 0.193/0.136 0.293/0.254 0.409/0.401 1/1"}

SHOTS = []   # (story#, demo, a, b, shot-dict, note, cfg)


def use(demo):
    B.TC, B.TL = (A1_TC, A1_TL) if demo == "A1" else (A11_TC, A11_TL)


def S(n, demo, a, b, make, note, cfg=None):
    use(demo)
    SHOTS.append((n, demo, a, b, make() if callable(make) else make, note, cfg))


def solve(a, b, **kw):
    kw.setdefault("ll", "out")
    return B.st(a, b, **kw)


def cpos(s):
    return sample(B.TC, np.array([tk(s)]))[0][0]


def cmean(a, b):
    return sample(B.TC, np.arange(tk(a), tk(b) + 1, 8))[0].mean(0)


def clips_sorted(clips):
    return sorted(clips, key=lambda c: c["start"])


def far_zoom(a, b):
    """a slow zoom from far away, looking through the corridors toward his corner (LL out of frame)"""
    c = cmean(a, b) + [0, 0, 50]
    best = None
    for d in range(1500, 700, -100):
        for ang in range(0, 360, 10):
            r = math.radians(ang)
            cam = c + [d * math.cos(r), d * math.sin(r), 10]
            if not B.cam_ok(cam) or not B.G.clear(cam, c):
                continue
            s = fixed(a, b, cam, c, 30.0, zoom=0.6)
            if strict(s, 2) == 0 and s["res"][1] == 0 and s["res"][0] > 0.5:
                if best is None or d > best[0]:
                    best = (d, s)
        if best:
            break
    return best[1] if best else None


def main():
    SMOKE = np.array([-650.0, 5135.0, -5850.0])
    T14 = np.array([-3472.0, 3921.0, -5844.0])

    LLPOS = np.array([-2686.0, 1124.0, -5900.0])     # LL's corridor-mouth spot (A1 1:22-2:47), facing south

    def pan(a, b, cam, fov):
        t = np.arange(tk(a), tk(b) + 1, 2)
        c = B.smooth(B.eye(B.TC, t) - [0, 0, 14], 12)
        camt = np.tile(np.asarray(cam, float), (len(t), 1))
        p, y = B.look(camt, c)
        s = dict(ticks=t, cam=camt, pitch=p, yaw=y, fov=np.full(len(t), float(fov)))
        s["res"] = B.evaluate(t, s["cam"], s["pitch"], s["yaw"], s["fov"])
        return s

    def dolly(a, b, p0, p1, aim, fov, move=None):
        """camera from p0 to p1 (eased) over `move` seconds, then holds; always aimed at `aim`"""
        t = np.arange(tk(a), tk(b) + 1, 2)
        n = len(t)
        m = n if move is None else min(n, int(move * 64 / 2))
        f = np.ones(n)
        f[:m] = np.linspace(0, 1, m)
        f = f * f * (3 - 2 * f)
        cam = np.asarray(p0, float)[None] + f[:, None] * (np.asarray(p1, float) - np.asarray(p0, float))[None]
        p, y = B.look(cam, np.tile(np.asarray(aim, float), (n, 1)))
        s = dict(ticks=t, cam=cam, pitch=p, yaw=y, fov=np.full(n, float(fov)))
        s["res"] = B.evaluate(t, s["cam"], s["pitch"], s["yaw"], s["fov"])
        return s

    def pan_yaw(a, b, cam, yaw0, yaw1, pitch, fov):
        t = np.arange(tk(a), tk(b) + 1, 2)
        n = len(t)
        f = np.linspace(0, 1, n)
        f = f * f * (3 - 2 * f)
        s = dict(ticks=t, cam=np.tile(np.asarray(cam, float), (n, 1)), pitch=np.full(n, float(pitch)),
                 yaw=yaw0 + (yaw1 - yaw0) * f, fov=np.full(n, float(fov)))
        s["res"] = B.evaluate(t, s["cam"], s["pitch"], s["yaw"], s["fov"])
        return s

    # ================= SCENE 1: TRAPPED (spawn corner; walls stay clean until he knifes them in #4) =================
    S(1, "A11", "0:18", "0:23", lambda: fixed("0:18", "0:23", [-2705.0, 300.0, -5840.0],
                                             np.array([-2705.0, 403.0, -5842.0]), 75.0),
      "THROUGH THE WALL: static at his height, from his right side; back to the wall, he steps out and looks around")
    S(2, "A11", "0:28", "0:34.5", lambda: pan_yaw("0:28", "0:34.5", [-2730.0, 300.0, -5836.0], 115.0, 70.0, 4.0, 80.0),
      "behind him in the spawn corner, slow pan across the area toward the corridor")
    S(3, "A11", "0:34.5", "0:36.8", lambda: shot_ots_c(tk("0:34.5"), tk("0:36.8"), back=80, side=20, up=6, fov=62),
      "over the shoulder, same movement: he walks up the corridor and peeks around the corner")
    S(4, "A1", "1:08", "1:14", lambda: solve("1:08", "1:14", absb=0, dists=(500, 700, 900), margin=1.8, zoom=0.72,
                                             avoid_yaw=(90, 60)), "slow zoom from afar, pounding the wall (HELLO!?)")
    S(5, "A1", "1:18", "1:22.3", lambda: solve("1:18", "1:22.3", rel="left", dists=(90, 130, 170), heights=(18, 30),
                                               margin=1.1, fov_range=(30, 85)), "low close along the wall, last hits")
    S(6, "A1", "1:22.5", "1:25.5", lambda: solve("1:22.5", "1:25.5", absb=0, dists=(200, 280, 360), margin=1.4,
                                                 avoid_yaw=(90, 50)), "he swivels toward the mumbling")
    S(7, "A11", "0:50.5", "0:54.5", lambda: fixed("0:50.5", "0:54.5", [-2705.0, 470.0, -5836.0],
                                                 np.array([-2700.0, 1150.0, -5840.0]), 50.0, zoom=0.7),
      "slow zoom up the empty corridor the mumbling comes from")
    S(8, "A11", "0:39.5", "0:46", lambda: solve("0:39.5", "0:46", rel="front-left", dists=(180, 240, 320), margin=1.3),
      "he backs away into the corner: 'custom map... prank... let me out'")

    def s9():
        c = cmean("0:43", "0:48")
        return fixed("0:43", "0:48", c + [8.0, -70.0, 66.0], np.array([-2700.0, 1150.0, -5840.0]), 50.0, zoom=0.7)
    S(9, "A11", "0:43", "0:48", s9, "the abyss: over his shoulder, slow zoom up the dark corridor ('No.')")
    S(10, "A11", "1:04", "1:08.5", lambda: solve("1:04", "1:08.5", rel="front-right", dists=(160, 220, 300), margin=1.3),
      "'did you really think this would work... fly out' (then the console insert)")
    S(11, "A11", "1:14", "1:20", lambda: solve("1:14", "1:20", rel="left", dists=(220, 300, 380), heights=(110, 150),
                                              margin=1.4), "'what's that stupid command again?'")
    S(12, "A11", "1:20", "1:26", lambda: solve("1:20", "1:26", rel="back", dists=(200, 280, 360), margin=1.4,
                                              need_c=0.8), "relief speech, the corridor ahead")
    S(13, "A11", "1:26", "1:31", lambda: solve("1:26", "1:31", rel="ahead", anchor="end", dists=(180, 260, 340),
                                              margin=1.3, need_c=0.8), "walks up the corridor toward the video 'we were making'")
    # --- the reveal (A1): LL already in place; the camera backs away from just above his head to reveal it ---
    REV_AIM = LLPOS + [300 * math.cos(math.radians(-110)), 300 * math.sin(math.radians(-110)), 40]
    S(14, "A1", "2:22.5", "2:30.5", lambda: dolly("2:22.5", "2:30.5", LLPOS + [12.0, 30.0, 92.0],
                                                  [-2652.0, 1218.0, -5838.0], REV_AIM, 30.0, move=4.0),
      "THE REVEAL: the camera backs away to reveal the back of its head; Caillou walks in and notices it")
    S(15, "A1", "2:30.5", "2:32.5", lambda: solve("2:30.5", "2:32.5", absb=90, dists=(80, 100, 120), fit=False,
                                                  fixed_fov=38, need_c=0.8, aim_point=cmean("2:30.5", "2:32.5") + [0, 0, 62]),
      "close: 'no, no, that's not right'")
    S(16, "A1", "2:32.5", "2:38", lambda: fixed("2:32.5", "2:38", LLPOS + [0.0, 70.0, 70.0],
                                               LLPOS + [-10.0, -420.0, 45.0], 32.0),
      "straight behind its head, centered: it watches him run")
    S(17, "A1", "2:38", "2:40.5", lambda: shot_cine("ground", tk("2:38"), tk("2:40.5"), {"facing": "away"}, track=B.TC),
      "ground lock-off, runs away (ends before the wall glitch)")
    S(18, "A1", "2:49.2", "2:54", lambda: shot_cine("tripod", tk("2:49.2"), tk("2:54"),
                                                    {"angle": "front-right", "distance": 950, "size": "wide"}, track=B.TC),
      "wide tripod pan, sprint north")
    S(19, "A1", "2:54", "2:56.3", lambda: solve("2:54", "2:56.3", rel="ahead", anchor="end", dists=(150, 250, 350),
                                                need_c=0.55, fit=False, fixed_fov=82), "lost: runs through (cut before he turns back)")

    # ================= SCENE 2: LL WATCHING, THE RUN, THE HIDING CORNER =================
    def s20():
        # a pillar stands ~60 u behind LL, so the camera is close (45 u): his head is a dark foreground shape on one
        # side, the lit gap where Caillou darts across on the other
        H = sample(B.TL, np.array([tk("4:03")]))[0][0] + [0, 0, 62]
        G = np.array([-3175.0, 2515.0, -5830.0])
        u = (G - H)[:2] / np.linalg.norm((G - H)[:2])
        perp = np.array([u[1], -u[0]])
        cam = np.r_[H[:2] - u * 45 + perp * -14, H[2] + 2]
        dh = (H - cam) / np.linalg.norm(H - cam)
        dg = (G - cam) / np.linalg.norm(G - cam)
        return fixed("4:02.4", "4:05", cam, cam + (dh + dg) / 2 * 500, 40.0)
    S(20, "A1", "4:02.4", "4:05", s20, "close behind it: its head a dark silhouette beside the lit gap as he darts past")
    S(21, "A11", "1:43.8", "1:47.3", lambda: pan("1:43.8", "1:47.3", [-3904.0, 2440.0, -5840.0], 60.0),
      "the run: he races past, checking behind him")
    S(22, "A11", "1:47.3", "1:52.5", lambda: pan("1:47.3", "1:52.5", [-4762.0, 2420.0, -5840.0], 60.0),
      "the run continues, and he ducks around the corner")
    S(24, "A11", "1:52.5", "1:58", lambda: solve("1:52.5", "1:58", absb=-90, dists=(160, 220, 300), margin=1.3),
      "'What the hell was that thing? Its face... WHAT IS HAPPENING?'")
    S(25, "A11", "1:58", "2:05", lambda: solve("1:58", "2:05", rel="front-left", dists=(90, 120, 150), margin=1.15,
                                              fov_range=(25, 70)), "close: 'Calm down, calm down... my heart's racing'")
    S(26, "A11", "2:05", "2:11", lambda: shot_pull(tk("2:05"), tk("2:11"), bearing_deg=-120, d0=150, d1=420, h0=50,
                                                  h1=220, fov=58), "camera pulls out: the scale of the place ('...it said Cache')")
    S(27, "A11", "2:11", "2:20", lambda: solve("2:11", "2:20", absb=-30, dists=(180, 240, 320), margin=1.3),
      "'If it was a custom map...' (after the B&W insert)")
    S(28, "A11", "2:20", "2:26.8", lambda: solve("2:20", "2:26.8", rel="front-right", dists=(120, 170, 230), margin=1.3,
                                                zoom=0.72, fov_range=(25, 85)), "slow zoom in: 'is this place ACTUALLY real?'")
    # the footstep: whip toward the corridor mouth he came from (aimed further right than v5), hold while he slips away
    # behind the camera, whip back to his spot (empty); the next shot shows him running away
    WCAM = np.array([-5200.0, 2200.0, -5838.0])
    MOUTH = WCAM + [400 * math.cos(math.radians(75)), 400 * math.sin(math.radians(75)), -12]
    SPOT = np.array([-5087.0, 2300.0, -5860.0])
    S(29, "A11", "2:26.8", "2:30.6", lambda: shot_whip(tk("2:26.8"), tk("2:30.6"), WCAM, cpos("2:26.8") + [0, 0, 40],
                                                      MOUTH, whip=0.28, fov=35), "WHIP to where he came from: nothing there")
    S(30, "A11", "2:30.6", "2:32", lambda: shot_whip(tk("2:30.6"), tk("2:32"), WCAM, MOUTH, SPOT, whip=0.28, fov=35),
      "WHIP back: his spot is empty")
    S(31, "A11", "2:31.4", "2:33.2", lambda: fixed("2:31.4", "2:33.2", [-5060.0, 2085.0, -5840.0],
                                                  np.array([-4400.0, 2060.0, -5850.0]), 60.0),
      "he's already sprinting away down the corridor")

    # ================= SCENE 3: THE PILLARS =================
    S(32, "A1", "6:13", "6:19", lambda: solve("6:13", "6:19", rel="front-left", dists=(350, 500, 650), fit=False,
                                              fixed_fov=82, need_c=0.8), "'I'm gonna get lost if I keep this up' (wide on the pillars)")
    S(33, "A1", "6:21", "6:25", lambda: solve("6:21", "6:25", rel="front-left", dists=(200, 280, 360), margin=1.3,
                                              need_c=0.85), "'How? How? How? The pillars.'")
    S(34, "A1", "6:27", "6:29.5", lambda: solve("6:27", "6:29.5", rel="left", dists=(300, 420, 550), margin=1.5,
                                                fov_range=(35, 85)), "counting, he walks off into the dark")

    # ================= SCENE 4: THE CORNER AND THE PIT (first time) =================
    S(35, "A1", "12:26", "12:34", lambda: solve("12:26", "12:34", rel="behind-travel", dists=(200, 300, 400),
                                                need_c=0.6, margin=1.4), "he walks into the pit room, toward the corner")
    S(36, "A1", "13:01", "13:06", lambda: shot_ots_c(tk("13:01"), tk("13:06"), back=80, side=18, up=10, fov=58, ahead=700),
      "over his shoulder: the huge pit ('I think I'm safe here')")
    S(37, "A1", "13:12", "13:17.5", lambda: solve("13:12", "13:17.5", rel="front-left", dists=(110, 150, 200), margin=1.2,
                                                  zoom=0.85, fov_range=(25, 80)), "'...wait... what if...' readies the smoke")
    t38 = np.arange(tk("13:17.5"), tk("13:21") + 1, 2)
    S(38, "A1", "13:17.5", "13:21", lambda: fixed("13:17.5", "13:21", SMOKE, sample(B.TC, t38)[0].mean(0) + [0, 0, 46], 70.0),
      "the smoke fills the frame (to Ancient)")

    # ================= SCENE 5: THE CREATURE RETURNS =================
    S(39, "A1", "13:43", "13:49.2", lambda: solve("13:43", "13:49.2", absb=0, dists=(650, 850, 1050), margin=2.2,
                                                  fov_range=(30, 85)), "wider, further back: back in the corner, lobbing a smoke, singing")
    S(40, "A1", "13:49.2", "13:51.3", lambda: far_zoom("13:49.2", "13:51.3"),
      "far away, a slow zoom through the corridors toward him: distant footsteps")
    S(41, "A1", "13:51.5", "13:54", lambda: solve("13:51.5", "13:54", absb=90, dists=(200, 280, 360), margin=1.3),
      "he turns his head toward the sound")
    S(42, "A1", "14:10.5", "14:13", lambda: solve("14:10.5", "14:13", rel="front-left", dists=(180, 250), margin=1.3),
      "'I really hope that video posted.'")
    S(43, "A1", "14:12.5", "14:17", lambda: solve("14:12.5", "14:17", absb=180, dists=(250, 350, 450), margin=1.5,
                                                  need_c=0.7), "'Hang on... did I just hear...': checks around the corner, nothing, walks back")
    S(44, "A1", "15:29.5", "15:33", lambda: shot_behind_ll(tk("15:29.5"), tk("15:33"), dist=70, side=20, up=6, weight=0.9,
                                                          fov=48, follow_facing=True),
      "CLOSE, behind its head: the smoke is up; he turns and sees it: 'How- how?'")
    t45 = np.arange(tk("15:33"), tk("15:39.5"), 8)
    S(45, "A1", "15:33", "15:39.5", lambda: fixed("15:33", "15:39.5", [-1290.0, 5060.0, -5836.0],
                                                  sample(B.TC, t45)[0].mean(0) + [0, 0, 46], 40.0),
      "he backs away through the smoke")
    S(46, "A1", "15:39.5", "15:40.6", lambda: fixed("15:39.5", "15:40.6", [-420.0, 5260.0, -5800.0],
                                                    [-430.0, 5090.0, -5950.0], 90.0),
      "SLOW MOTION 2x: over the edge", "demo_timescale 0.5")

    # ---- checks + per-demo specs ----
    problems = []
    report = ["| # | demo | time | C | LL in | note |", "|---|---|---|---|---|---|"]
    order = []
    per = {"A1": [], "A11": []}
    for n, demo, a, b, s, note, cfg in SHOTS:
        use(demo)
        if any(tk(a) / 64 < y and tk(b) / 64 > x for x, y in NOCLIP[demo]):
            problems.append(f"#{n} ({demo}): overlaps a noclip window")
        if s is None:
            problems.append(f"#{n} ({demo}): no camera met the rules")
            continue
        bad = strict(s, 1)
        if bad:
            problems.append(f"#{n} ({demo}): LL visible and not from behind in {bad} samples")
        if demo == "A1" and smiley_in(s):
            problems.append(f"#{n} (A1): smiley spray in frame")
        cv, li, lf = s["res"]
        report.append(f"| {n} | {demo} | {a}–{b} | {cv:.0%} | {li:.0%} | {note} |")
        per[demo].append((n, a, b, s, note, cfg))
        order.append({"n": n, "demo": demo, "start": tk(a), "end": tk(b)})
    for demo, items in per.items():
        B.CLIPS.clear()
        B.REPORT.clear()
        use(demo)
        for n, a, b, s, note, cfg in items:
            B.add(n, a, b, s, note, cfg=cfg)
        # split into render passes: CS:DM needs sequences in increasing, non-overlapping order, and a 40-100 tick
        # gap loses the campath; a clip that breaks either goes to the next pass
        passes = []
        for c in clips_sorted(list(B.CLIPS)):
            for ps in passes:
                gap = c["start"] - ps[-1]["end"]
                if gap >= 0 and not 40 <= gap <= 100:
                    ps.append(c)
                    break
            else:
                passes.append([c])
        for k, ps in enumerate(passes):
            name = ("a1-v6" if demo == "A1" else "a11-v6") + ("" if k == 0 else f"-p{k + 1}")
            spec = {"name": name, "summary": f"videos/demos/{demo}.summary.json", "outputFileName": name.upper(),
                    "concatenate": True, "order": "spec", "clips": ps, "cfg": "r_csgo_postprocess_enable 0",
                    "finish": False}
            (B.REPO / "videos" / "specs" / f"{name}.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
            for c in ps:
                for o in order:
                    if o["demo"] == demo and o["start"] == c["start"] and o["end"] == c["end"]:
                        o["pass"] = name
            print(f"  pass {name}: {len(ps)} clips")
    (B.REPO / "videos" / "specs" / "a1-v6-order.json").write_text(json.dumps({"finish": FINISH, "shots": order}, indent=1),
                                                                  encoding="utf-8")
    total = sum((o["end"] - o["start"]) / 64 * (2 if o["n"] == 46 else 1) for o in order)
    report += ["", f"Edit length ~ {total:.0f} s", "", "Problems:"] + (problems or ["none"])
    (B.REPO / "videos" / "specs" / "a1v2" / "report_v6.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"shots {len(order)} (A1 {len(per['A1'])}, A11 {len(per['A11'])}), edit ~ {total:.0f} s")
    print("PROBLEMS:", *(problems or ["none"]), sep="\n  ")


if __name__ == "__main__":
    main()
