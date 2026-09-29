"""A1 v4 (2026-09-29): v3 re-checked against the story bible (videos/briefs/LORE.md).
Changes from v3: #21 added (the still-life stands unseen right behind him; LL standing, no noclip), #49 re-shot
(v3's camera sat where LL stops, so he never walked into frame).

v3: cut to the Part 1 script beats under the user's rules.

Rules (user, 2026-09-29):
  1. If a player is noclipping, that footage is unusable (windows below are excluded, the moving player in frame or not).
  2. LL's face is never seen: the camera must be BEHIND him (>120 deg from his facing) or he's out of frame.
  3. Show Caillou when he's moving; standing only where the script has a line/beat, and briefly. No long/redundant shots.
Run: python videos/specs/a1v2/build_v3.py  ->  videos/specs/a1-v3.json + videos/specs/a1v2/report_v4.md
"""
import json
import math

import numpy as np

import build as B
from build import tk, st, fixed, reuse, sample, TC, TL, add, CLIPS, REPORT, shot_ots_c, shot_behind_ll, \
    shot_creature_lead, shot_cine, shot_whip, shot_pull, solve_static, look_at

# noclip windows (demo seconds) found in A1: C's dashes at the bolt, LL's flights (see memory §26)
NOCLIP = [(164.4, 165.1), (168.2, 168.9),                       # C: 2:44.4, 2:48.2 dashes
          (189.8, 191.2), (193.3, 193.5), (199.5, 205.6),       # LL: 3:09.8 in behind C, 3:13.4 hop, 3:19.5-3:25.5
          (267.8, 274.9)]                                       # LL: the 4:27.8-4:34.9 charge
BEHIND_DEG = 120.0


def strict_ll(s, step=2):
    """(ticks where LL is visible and the camera is less than 120 deg behind him, max apparent size)."""
    bad, worst = 0, 0.0
    ticks = s["ticks"]
    lb = B.body(TL, ticks)
    _, lyaw, _, _ = sample(TL, ticks)
    for i in range(0, len(ticks), step):
        inf = B.cine._in_frame(s["cam"][i], float(s["pitch"][i]), float(s["yaw"][i]), float(s["fov"][i]), lb[i])
        if not any(inf[j] and B.G.clear(s["cam"][i], lb[i][j]) for j in range(4)):
            continue
        off = abs(B.adiff(B.bearing(lb[i][3], s["cam"][i]), lyaw[i]))
        if off < BEHIND_DEG:
            dist = np.linalg.norm(lb[i][3] - s["cam"][i])
            vfov = 2 * math.atan(math.tan(math.radians(s["fov"][i]) / 2) / 0.75 * 9 / 16)
            worst = max(worst, 72 / (2 * dist * math.tan(vfov / 2)))
            bad += 1
    return bad, worst


def noclip_overlap(a_s, b_s):
    a, b = tk(a_s) / 64, tk(b_s) / 64
    return any(a < y and b > x for x, y in NOCLIP)


def ll_out(a_s, b_s, **kw):
    """Static solver with LL fully out of frame."""
    kw.setdefault("ll", "out")
    return st(a_s, b_s, **kw)


def main():
    C_WALL = np.array([-2730.0, 362.0, -5836.0])
    T14 = np.array([-3472.0, 3921.0, -5844.0])
    SMOKE = np.array([-650.0, 5135.0, -5850.0])

    def cpos(s):
        return sample(TC, np.array([tk(s)]))[0][0]

    shots = []

    def S(n, a, b, s, note, cfg=None):
        shots.append((n, a, b, s, note, cfg))

    # --- INT. THE BACKROOMS: phases through, looks around, finds the wall sealed -------------------------------------
    S(1, "0:25.9", "0:30", ll_out("0:25.9", "0:30", rel="left", anchor="start", dists=(180, 240, 320), margin=1.3),
      "transition: steps away from the wall (locked)")
    S(2, "0:30.2", "0:35", ll_out("0:30.2", "0:35", rel="front", dists=(220, 320, 450), fov_range=(40, 95), margin=1.35),
      "looks around, peering (wide)")
    S(3, "0:38", "0:42", "native_pov", "POV: peering behind corners")
    S(4, "1:08", "1:14", ll_out("1:08", "1:14", absb=0, dists=(500, 700, 900), margin=1.8, zoom=0.72, avoid_yaw=(90, 60)),
      "slow zoom from afar, pounding the wall (HELLO!? ~1:12)")
    S(5, "1:18", "1:22.3", ll_out("1:18", "1:22.3", rel="left", dists=(90, 130, 170), heights=(18, 30), margin=1.1,
                                  fov_range=(30, 85)), "low close along the wall, last hits")
    S(6, "1:22.5", "1:25.5", ll_out("1:22.5", "1:25.5", absb=0, dists=(200, 280, 360), margin=1.4, avoid_yaw=(90, 50)),
      "he swivels toward the mumbling (1:24); corridor out of frame")
    S(7, "1:32.3", "1:38", shot_ots_c(tk("1:32.3"), tk("1:38")), "OTS as he backs away (custom-map prank line)")
    # the abyss: aim down the dark part of the corridor, the lit doorway where LL stands kept out of frame
    # the abyss: aim down the dark part of the corridor, well left of the lit doorway where LL stands (LL out of frame)
    abyss = fixed("1:44", "1:49", C_WALL + [0, 60, 0], np.array([-3086.0, 1124.0, -5846.0]), 50.0, zoom=0.8)
    S(8, "1:44", "1:49", abyss, "the abyss: slow zoom into the dark (\"No.\" at the end); LL out of frame")
    S(9, "1:51", "1:55", ll_out("1:51", "1:55", absb=53, dists=(160, 220, 300), margin=1.3),
      "\"did you really think...\" (then the console insert)")
    S(10, "1:59", "2:03", fixed("1:59", "2:03", [-3112.0, 696.0, -5830.0],
                               sample(TC, np.arange(tk("1:59"), tk("2:03"), 8))[0].mean(0) + [0, 0, 10], 85.0),
      "high angle, \"what's that stupid command\"")
    S(11, "2:17", "2:21", ll_out("2:17", "2:21", rel="front-right", dists=(170, 230, 300), margin=1.3),
      "relief speech starts")
    S(12, "2:21", "2:26", ll_out("2:21", "2:26", rel="ahead", anchor="end", dists=(140, 220, 300), need_c=0.7,
                                  fov_range=(40, 85), margin=1.3, avoid_yaw=(90, 35)), "walks toward the corridor mouth")
    # --- the reveal -------------------------------------------------------------------------------------------------
    s13 = shot_behind_ll(tk("2:26"), tk("2:30.5"), dist=120, side=-18, up=14, weight=0.97, fov=35)
    S(13, "2:26", "2:30.5", s13, "THE REVEAL: back of LL's head, C far")
    S(14, "2:30.5", "2:33.5", ll_out("2:30.5", "2:33.5", absb=90, dists=(80, 100, 120), fit=False, fixed_fov=38,
                                      need_c=0.8, aim_point=sample(TC, np.arange(tk("2:30.5"), tk("2:33.5"), 8))[0].mean(0)
                                      + [0, 0, 62]), "close: \"no, no, that's not right\"")
    S(15, "2:34", "2:37", reuse(s13, "2:34", "2:37"), "the creature stands still, watching him go")
    # --- running sequence (no noclip: 2:44.4-2:45.1 and 2:48.2-2:48.9 excluded) ------------------------------------
    S(16, "2:38", "2:44.2", shot_cine("ground", tk("2:38"), tk("2:44.2"), {"facing": "away"}), "ground lock-off, runs away")
    S(17, "2:49.2", "2:54", shot_cine("tripod", tk("2:49.2"), tk("2:54"), {"angle": "front-right", "distance": 850}),
      "tripod pan, sprint north")
    S(18, "2:54", "2:59", ll_out("2:54", "2:59", rel="ahead", anchor="end", dists=(150, 250, 350), need_c=0.55, fit=False,
                                  fixed_fov=82), "liminal, runs through, checks behind")
    S(19, "3:02", "3:07.4", ll_out("3:02", "3:07.4", absb=90, anchor="end", dists=(200, 300, 400), need_c=0.8, margin=1.4),
      "rounds the corner, stops")
    S(20, "3:07.5", "3:09.7", ll_out("3:07.5", "3:09.7", rel="front-left", dists=(150, 220, 300), margin=1.3),
      "\"What the hell was that thing?\"")
    S(21, "3:14.2", "3:17.3", shot_behind_ll(tk("3:14.2"), tk("3:17.3"), dist=200, side=0, up=10, weight=0.9, fov=45),
      "unseen, right behind him: LL's back in the foreground, C beyond (\"calm down... it has to be a prank\")")
    S(22, "3:26", "3:32", shot_pull(tk("3:26"), tk("3:32"), bearing_deg=-150, d0=150, d1=420, h0=50, h1=220, fov=58),
      "camera pans out: the scale of the place")
    s23 = ll_out("3:34", "3:38", absb=180, dists=(160, 220, 300), margin=1.3)
    S(23, "3:34", "3:38", s23, "\"If it was a custom map...\" (after the B&W insert)")
    S(24, "3:44", "3:50", ll_out("3:44", "3:50", absb=90, dists=(120, 170, 230), margin=1.3, zoom=0.72,
                                  fov_range=(25, 85)), "slow zoom in: \"is this place ACTUALLY real?\"")
    c = cpos("3:57.4")
    wcam = c + [-70.0, -20.0, 60.0]
    S(25, "3:57.4", "3:58.9", shot_whip(tk("3:57.4"), tk("3:58.9"), wcam, c + [0, 0, 50], T14 + [900, 0, 0], whip=0.28, fov=50),
      "WHIP PAN to the darkness (nothing visible)")
    S(26, "3:58.9", "4:01", shot_whip(tk("3:58.9"), tk("4:01"), wcam, T14 + [900, 0, 0], c + [0, 0, 45], whip=0.28, fov=50),
      "WHIP PAN back: he's already running")
    S(27, "4:05", "4:09.5", shot_cine("ground", tk("4:05"), tk("4:09.5"), {"facing": "toward"}), "running, toward the lens")
    S(28, "4:10", "4:14", ll_out("4:10", "4:14", rel="front-left", dists=(350, 500, 650), fit=False, fixed_fov=80,
                                  need_c=0.8), "running through the generations (wide)")
    S(29, "4:15", "4:18", shot_cine("tripod", tk("4:15"), tk("4:18"), {"angle": "front-left"}), "tripod pan")
    S(30, "4:19", "4:23", ll_out("4:19", "4:23", rel="behind-travel", anchor="start", dists=(120, 200), need_c=0.5,
                                  fit=False, fixed_fov=78, aim_point=cpos("4:22") + [0, 0, 50]), "rounds a corner")
    S(31, "4:25", "4:27.7", ll_out("4:25", "4:27.7", rel="left", dists=(200, 280, 360), margin=1.3), "skids to a stop")
    # --- the still-life at the far end of the hallway: LL standing at Q19 after his noclip ended (4:38-5:12) -------
    s32 = shot_behind_ll(tk("4:40"), tk("4:44"), dist=220, side=0, up=10, weight=0.5, fov=40, follow_facing=True)
    S(32, "4:40", "4:44", s32, "the still-life at the far end of the hallway, its back to us (easter-egg plate: #32p)")
    S("32p", "11:00", "11:04", reuse(s32, "11:00", "11:04"), "clean plate of #32 for the second still-life composite")
    S(33, "5:41", "5:46", ll_out("5:41", "5:46", rel="ahead", anchor="end", dists=(150, 250, 350), need_c=0.5, fit=False,
                                  fixed_fov=80), "backpedals and sprints the other way")
    S(34, "5:48", "5:54", shot_cine("ground", tk("5:48"), tk("5:54"), {"facing": "away"}), "sprinting away")
    # --- the pillars ------------------------------------------------------------------------------------------------
    S(35, "6:13", "6:19", ll_out("6:13", "6:19", rel="front-left", dists=(350, 500, 650), fit=False, fixed_fov=82,
                                  need_c=0.8), "\"I'm gonna get lost if I keep this up\" (wide on the pillars)")
    S(36, "6:21", "6:25", ll_out("6:21", "6:25", rel="front-left", dists=(200, 280, 360), margin=1.3, need_c=0.85),
      "\"How? How? How? The pillars.\"")
    S(37, "6:27", "6:38", ll_out("6:27", "6:38", rel="left", dists=(300, 420, 550), margin=1.5, zoom=0.9,
                                  fov_range=(35, 85)), "counting: 1 (6:33.2), 2 (6:35.3), 3 (6:36.3)")
    # --- INT. THE BACKROOMS (SOLO): the corner ------------------------------------------------------------------------
    S(38, "13:01", "13:06", ll_out("13:01", "13:06", absb=0, dists=(500, 650, 800), margin=2.4, fov_range=(30, 85)),
      "\"I think I'm safe here\"")
    S(39, "13:12", "13:17.5", ll_out("13:12", "13:17.5", absb=0, dists=(300, 400, 500), margin=1.6, zoom=0.8),
      "\"...wait... what if...\" readies the smoke")
    t40 = np.arange(tk("13:17.5"), tk("13:21") + 1, 2)
    S(40, "13:17.5", "13:21", fixed("13:17.5", "13:21", SMOKE, sample(TC, t40)[0].mean(0) + [0, 0, 46], 70.0),
      "the smoke fills the frame (transition to Ancient)")
    S(41, "13:43", "13:48", ll_out("13:43", "13:48", absb=0, dists=(450, 600, 750), margin=1.8, zoom=0.85),
      "back in the corner: lobs a smoke, singing")
    S(42, "13:49.2", "13:51.3", shot_creature_lead(tk("13:49.2"), tk("13:51.3")), "creature lead: distant footsteps")
    S(43, "13:51.5", "13:54", ll_out("13:51.5", "13:54", absb=90, dists=(200, 280, 360), margin=1.3),
      "he turns his head toward the sound")
    S(44, "14:11", "14:14", ll_out("14:11", "14:14", rel="front-left", dists=(180, 250), margin=1.3),
      "\"I really hope that video posted.\"")
    S(45, "14:20", "14:24", ll_out("14:20", "14:24", rel="front-left", dists=(80, 110, 140), margin=1.1, fov_range=(18, 80)),
      "\"Hang on... did I just hear...\"")
    t46 = np.arange(tk("14:33.8"), tk("14:38.2"), 8)
    S(46, "14:33.8", "14:38.2", fixed("14:33.8", "14:38.2", [-1290.0, 5060.0, -5836.0],
                                      sample(TC, t46)[0].mean(0) + [0, 0, 46], 55.0), "he goes to check (cut before the edge)")
    S(47, "14:42.6", "14:45", ll_out("14:42.6", "14:45", absb=0, dists=(300, 400, 500), margin=1.4),
      "gives up, back to the corner")
    S(48, "15:09.3", "15:11.4", shot_creature_lead(tk("15:09.3"), tk("15:11.4")), "creature POV approaching; it stops")
    t49 = np.arange(tk("15:20.5"), tk("15:24.5") + 1, 2)
    S(49, "15:20.5", "15:24.5", fixed("15:20.5", "15:24.5", [-1198.0, 5346.0, -5836.0],
                                      sample(TC, t49)[0].mean(0) + [0, 0, 40], 45.0),
      "the creature walks into frame, back of its head")
    S(50, "15:24.5", "15:29", fixed("15:24.5", "15:29", cpos("15:24.5") + [-70.0, -18.0, 70.0], SMOKE, 62.0),
      "the last smoke (15:25.9)")
    S(51, "15:29", "15:33", shot_behind_ll(tk("15:29"), tk("15:33"), dist=90, side=40, up=6, weight=0.9),
      "he turns and sees it: \"How- how?\"")
    t52 = np.arange(tk("15:34"), tk("15:39.5"), 8)
    S(52, "15:34", "15:39.5", fixed("15:34", "15:39.5", [-1290.0, 5060.0, -5836.0],
                                    sample(TC, t52)[0].mean(0) + [0, 0, 46], 40.0), "backs up frantically through the smoke")
    S(53, "15:39.5", "15:40.6", fixed("15:39.5", "15:40.6", [-420.0, 5260.0, -5800.0], [-430.0, 5090.0, -5950.0], 90.0),
      "SLOW MOTION 2x: over the edge (cut before the teleport)", cfg="demo_timescale 0.5")

    problems = []
    for n, a, b, s, note, cfg in shots:
        if n != "32p" and noclip_overlap(a, b):
            problems.append(f"#{n}: overlaps a noclip window")
        if isinstance(s, dict):
            bad, worst = strict_ll(s)
            if bad:
                problems.append(f"#{n}: LL visible and not from behind in {bad} samples (max {worst:.0%} of frame)")
        elif s is None:
            problems.append(f"#{n}: no camera met the rules")
        add(n, a, b, s, note, cfg=cfg if cfg else ("demo_timescale 1" if n == "32p" else None))
    # 53 is slow motion: reset the timescale on the clean plate that follows it in the render order
    spec = {"name": "a1-v4", "summary": "videos/demos/A1.summary.json", "outputFileName": "A1-v4",
            "concatenate": True, "order": "spec", "clips": CLIPS,
            "cfg": "r_csgo_postprocess_enable 0",
            "finish": {"look": "cinematic", "letterbox": True, "vignette": False,
                       "curves": "0/0 0.088/0.035 0.136/0.075 0.193/0.136 0.293/0.254 0.409/0.401 1/1"}}
    # render the clean plate last (after the slow-motion clip), with the timescale reset
    plate = [c for c in spec["clips"] if c["why"].startswith("#32p")]
    spec["clips"] = [c for c in spec["clips"] if not c["why"].startswith("#32p")]
    plate_spec = dict(spec, name="a1-v4-plate", outputFileName="A1-v4-plate32", clips=plate)
    (B.REPO / "videos" / "specs" / "a1-v4-plate.json").write_text(json.dumps(plate_spec, indent=1), encoding="utf-8")
    (B.REPO / "videos" / "specs" / "a1-v4.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    total = sum((c["end"] - c["start"]) / 64 * (2 if "SLOW" in c["why"] else 1) for c in spec["clips"]
                if not c["why"].startswith("#32p"))
    lines = ["| # | time | check | note |", "|---|---|---|---|"] + [
        f"| {n} | {a}–{b} | {r} | {note} |" for n, a, b, r, note in REPORT]
    lines += ["", f"Edit length ≈ {total:.0f} s", "", "Problems:"] + (problems or ["none"])
    (B.REPO / "videos" / "specs" / "a1v2" / "report_v4.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote a1-v4.json: {len(spec['clips'])} clips, edit ~ {total:.0f} s")
    print("PROBLEMS:", *(problems or ["none"]), sep="\n  ")


if __name__ == "__main__":
    main()
