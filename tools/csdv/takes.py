"""Take sheet: read a demo the way a script supervisor would, so intentional takes can be told apart from rehearsals,
repositioning and goofing around.

For every player: movement segments (still / walk / run / NOCLIP, teleports), where they go, where they look; the
events (weapon fire, grenades, equips, deaths, chat); moments one player looks straight at the other with a clear line
of sight; and repeated visits to the same spot (retakes). Writes a markdown sheet next to the maps.

Run: python tools/csdv/takes.py videos/demos/A2.summary.json [-o videos/briefs/takes-A2.md]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mapgeo  # noqa: E402
import positions  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
STEP = 16  # ticks (0.25 s)


def fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def states(track, ticks):
    i = np.searchsorted(track.tick, ticks).clip(0, len(track.tick) - 1)
    p, yaw, pitch, duck = track.pos[i], track.yaw[i], track.pitch[i], track.duck[i]
    alive = track.alive[i]
    step = np.r_[np.linalg.norm(np.diff(p, axis=0), axis=1), 0.0] * (64.0 / STEP)
    hsp = np.r_[np.linalg.norm(np.diff(p[:, :2], axis=0), axis=1), 0.0] * (64.0 / STEP)
    vz = np.r_[np.diff(p[:, 2]), 0.0] * (64.0 / STEP)
    st = np.where(~alive, "dead", np.where(step > 900, "TELEPORT", np.where(
        (hsp > 320) | (np.abs(vz) > 350) & (hsp > 100), "NOCLIP",
        np.where(hsp > 160, "run", np.where(hsp > 25, "walk", "still")))))
    return p, yaw, pitch, duck, st, hsp


def segments(ticks, st, min_len=0.75):
    out, s = [], 0
    for k in range(1, len(st) + 1):
        if k == len(st) or st[k] != st[s]:
            out.append([st[s], s, k - 1])
            s = k
    # merge blips shorter than min_len into their neighbours (keep NOCLIP/TELEPORT always)
    merged = []
    for seg in out:
        dur = (seg[2] - seg[1] + 1) * STEP / 64
        if merged and dur < min_len and seg[0] not in ("NOCLIP", "TELEPORT", "dead"):
            merged[-1][2] = seg[2]
        else:
            merged.append(seg)
    return merged


def compass(yaw):
    return ["E", "NE", "N", "NW", "W", "SW", "S", "SE"][int(((yaw % 360) + 22.5) // 45) % 8]


def build(summary_path: Path, out: Path | None):
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    demo, name, map_name = summary["demoPath"], summary_path.stem.replace(".summary", ""), summary["map"]
    tracks = [t for t in positions.load_tracks(demo).values()]
    g = mapgeo.MapGeometry.for_map(map_name)
    t0 = min(int(t.tick[0]) for t in tracks)
    t1 = max(int(t.tick[-1]) for t in tracks)
    ticks = np.arange(t0, t1, STEP)
    lines = [f"# Take sheet: {name} ({map_name}, {summary['duration']})", "",
             "Automatic read of the demo (no audio). Segments: still / walk / run / NOCLIP (> 320 u/s or flying) / "
             "TELEPORT / dead. Times are demo time. Blips under 0.75 s are merged into their neighbours.", ""]
    S = {}
    for tr in tracks:
        S[tr.name] = states(tr, ticks)
    for tr in tracks:
        p, yaw, pitch, duck, st, hsp = S[tr.name]
        lines += [f"## {tr.name}: movement", "", "| from | to | state | from (x,y,z) | to (x,y,z) | facing | notes |",
                  "|---|---|---|---|---|---|---|"]
        for s_, a, b in segments(ticks, st):
            fa, fb = ticks[a] / 64, (ticks[b] + STEP) / 64
            note = []
            if s_ == "still" and fb - fa >= 5:
                note.append(f"holds {fb - fa:.0f} s")
            if pitch[a:b + 1].max() > 35:
                note.append("looks DOWN")
            if pitch[a:b + 1].min() < -35:
                note.append("looks UP")
            if duck[a:b + 1].mean() > 0.5:
                note.append("crouched")
            turn = float(np.ptp(np.unwrap(np.radians(yaw[a:b + 1])))) * 57.3
            if s_ == "still" and turn > 120:
                note.append(f"looks around ({turn:.0f}°)")
            lines.append(f"| {fmt(fa)} | {fmt(fb)} | **{s_}** | {p[a][0]:.0f},{p[a][1]:.0f},{p[a][2]:.0f} | "
                         f"{p[b][0]:.0f},{p[b][1]:.0f},{p[b][2]:.0f} | {compass(yaw[a])}→{compass(yaw[b])} | "
                         f"{'; '.join(note)} |")
        lines.append("")
        # repeated visits: 150 u cells visited in >= 2 separate stays of >= 2 s
        cells = {}
        cur, since = None, 0
        for k in range(len(ticks)):
            c = (int(p[k][0] // 150), int(p[k][1] // 150), int(p[k][2] // 100))
            if c != cur:
                if cur is not None and (k - since) * STEP / 64 >= 2 and st[since] != "NOCLIP":
                    cells.setdefault(cur, []).append((ticks[since] / 64, ticks[k - 1] / 64))
                cur, since = c, k
        rep = [(c, v) for c, v in cells.items() if len(v) >= 2]
        if rep:
            lines += [f"**{tr.name}: spots revisited (possible retakes)**", ""]
            for c, v in sorted(rep, key=lambda x: x[1][0][0]):
                lines.append(f"- around ({c[0] * 150 + 75}, {c[1] * 150 + 75}): "
                             + ", ".join(f"{fmt(a)}–{fmt(b)}" for a, b in v))
            lines.append("")
    # events
    try:
        from demoparser2 import DemoParser
        dp = DemoParser(demo)
        ev = []
        for e in ("weapon_fire", "smokegrenade_detonate", "hegrenade_detonate", "flashbang_detonate", "item_equip",
                  "player_death", "player_jump", "round_start", "round_announce_match_start", "begin_new_match"):
            try:
                df = dp.parse_event(e)
            except Exception:
                continue
            if isinstance(df, list):
                if not df:
                    continue
                import pandas as pd
                df = pd.DataFrame(df)
            for _, r in df.iterrows():
                what = r.get("weapon") or r.get("item") or ""
                if e == "player_jump":
                    continue
                ev.append((int(r["tick"]), e, str(r.get("user_name", "")), str(what)))
        try:
            chat = dp.parse_chat_messages()
            if isinstance(chat, list):
                import pandas as pd
                chat = pd.DataFrame(chat)
            for _, r in chat.iterrows():
                ev.append((int(r["tick"]), "chat", str(r.get("name", r.get("player_name", ""))), str(r.get("message_text", r.get("text", "")))))
        except Exception:
            pass
        ev.sort()
        lines += ["## Events", "", "| time | event | who | what |", "|---|---|---|---|"]
        for t, e, who, what in ev:
            lines.append(f"| {fmt(t / 64)} | {e} | {who} | {what} |")
        lines.append("")
    except Exception as exc:  # noqa: BLE001
        lines += [f"(events unavailable: {exc})", ""]
    # who looks at whom (clear line of sight, within 25 deg of their facing)
    if len(tracks) >= 2:
        lines += ["## Looking at each other", "", "Windows where one player faces the other (within 25°) with a clear "
                  "line of sight.", ""]
        for a in tracks:
            for b in tracks:
                if a is b:
                    continue
                pa, ya = S[a.name][0], S[a.name][1]
                pb = S[b.name][0]
                look = np.zeros(len(ticks), bool)
                for k in range(0, len(ticks), 2):
                    d = pb[k][:2] - pa[k][:2]
                    if np.linalg.norm(d) < 1:
                        continue
                    ang = abs((math.degrees(math.atan2(d[1], d[0])) - ya[k] + 180) % 360 - 180)
                    if ang < 25 and g.clear(pa[k] + [0, 0, 64], pb[k] + [0, 0, 56]):
                        look[k:k + 2] = True
                wins, s = [], None
                for k in range(len(look) + 1):
                    on = k < len(look) and look[k]
                    if on and s is None:
                        s = k
                    if not on and s is not None:
                        if (k - s) * STEP / 64 >= 0.75:
                            wins.append((ticks[s] / 64, ticks[k - 1] / 64))
                        s = None
                if wins:
                    lines.append(f"- **{a.name} looks at {b.name}:** " + ", ".join(f"{fmt(x)}–{fmt(y)}" for x, y in wins))
        lines.append("")
    out = out or (REPO / "videos" / "briefs" / f"takes-{name}.md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("summary")
    ap.add_argument("-o", "--out")
    a = ap.parse_args(argv)
    print(build(Path(a.summary), Path(a.out) if a.out else None))


if __name__ == "__main__":
    main()
