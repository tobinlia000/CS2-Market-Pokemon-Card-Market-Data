"""Planning brief: one PDF that hands a demo (or several) to another chat for shot planning.

    python tools/csdv/brief.py A1 A2 C1 D1 G1 [-o out.pdf]

Contents: how to read the maps and write a script in the format Claude builds from, the shot catalogue, then per
demo: facts, key moments (jumps, knife swings, shots, damage, teleports), every floor map with its landmarks and
spaces, and the 5-second movement timeline. Needs `csdv.py maps` to have run for each demo. The PDF is printed by
Microsoft Edge (headless) from a generated HTML page.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

import mapgeo
import mapview

REPO = Path(__file__).resolve().parents[2]
DEMOS = REPO / "videos" / "demos"
EDGE = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")]
TICK = 64

GUIDE = """
<h1>CS2 demo video — planning brief</h1>
<p class="lead">This document lets you (the planning chat) direct cinematic videos made from CS2 demo recordings.
Another Claude (in Claude Code, on the PC) has the demos, the full map geometry and the renderer. It turns your
shot list into video. Everything below was generated from the actual demo data and map files.</p>

<h2>1. What you are planning</h2>
<ul>
<li>Each demo is a recorded free-roam session of two players (<b>Caillou (Canadian now)</b> and
<b>Lightning Lemur</b>) on a workshop map. There are no rounds or scores. Lightning Lemur mostly stands still;
Caillou does most of the moving.</li>
<li>Output: 2560×1440, 60 fps. The final look is the default: in-game ambient occlusion and depth of field, motion
blur, cinematic grade, 2.39:1 letterbox.</li>
<li>Any camera is possible: first person, a player's own view, or a free camera anywhere on the map, still or
moving. Cameras are checked against the real map geometry, so they never sit inside walls, and each shot is checked
so the subject is actually visible.</li>
<li><b>Style the director likes:</b> mostly still or slowly zooming shots. Special or intense shots (handheld,
dolly zoom, dutch angle, drone moves) are reserved for intense moments, with plenty of normal shots between them so
the special ones keep their effect. Avoid cameras pinned to or chasing the character unless there's a reason.</li>
</ul>

<h2>2. How to read the maps</h2>
<ul>
<li><b>Floors</b> are lettered A, B, C… from lowest to highest. Only floors the players actually used are drawn.</li>
<li><b>Grid squares</b> (for example F7): columns A, B, C… run west→east and rows 1, 2, 3… run north→south. The same
grid is used on every floor of a map. One square is 256 units (about 6.5 m; a player is about 72 units tall).</li>
<li><b>Spaces</b> (for example C12 = floor C, space 12) are rooms, corridors and nooks detected from the geometry.
Nooks are small pockets and corners.</li>
<li><b>Landmarks</b> (L1, L2…) are numbered objects: ✚ signs, posters, pictures and named objects (read from
head-on renders of the actual textures); ■ doors; ◆ props; ▲ exit signs. The numbering is per demo. "off-grid" means
the object is outside the area drawn for that demo.</li>
<li><b>Routes:</b> each player's path in their colour, with arrows every 5 s and m:ss labels every 15 s. ★ marks a
spot where someone stood still for 6 s or more.</li>
<li><b>Movement timeline:</b> every 5 s, each player's <i>floor·space grid-square state</i>, for example
<code>C·C12 F7 walk</code>. <i>still / walk / run</i> is their movement at that moment.</li>
<li>Times are demo time (m:ss from the start of the recording). The renderer is exact to 1/64 s.</li>
</ul>

<h2>3. What to send back: the shot list</h2>
<p>One line per shot, in order. Fields:</p>
<pre>DEMO: C1   TITLE: (optional)
#  | start–end | shot (catalogue name or number) | subject      | camera place                  | options / notes
1  | 0:12–0:18 | 1 Locked-off static (wide)      | Caillou      | floor C, C12, looking at L85  | slow zoom 100→85%
2  | 0:18–0:22 | 11 Over-the-shoulder            | Caillou→Lemur| behind Caillou, right shoulder| —
3  | 0:22–0:30 | 7 Landmark shot                 | L118 sign    | from C13 doorway               | hold 3 s after he leaves
HIDDEN: (optional) places that must never be on screen during given times, e.g. "N1 nook C29, 4:55–5:03"</pre>
<ul>
<li>Places can be a space ID, grid square, landmark number, or "auto" (the renderer picks the best clear spot).</li>
<li>Shots can overlap or leave gaps; each is cut in order. A typical shot lasts 3–9 s.</li>
<li>Rough drafts are fine. The Claude on the PC checks every place and time against the real map and demo data,
flags anything that won't work (for example, a player not visible from that spot), and suggests fixes before
rendering.</li>
<li>Useful beats to cut on: the <b>key moments</b> table for each demo (jumps, knife swings, shots, damage,
teleports) and the ★ still spots.</li>
</ul>

<h2>4. What isn't known</h2>
<ul>
<li>Voice chat, facial expressions and exact animations (for example, which knife move played) aren't in the data.</li>
<li>Landmark names come from textures and file names. Some are descriptive rather than exact (for example "framed
photo: three students posing"). Areas the players never visited aren't drawn.</li>
<li>Foreign text is translated, with the original in quotes.</li>
</ul>
"""


def _fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _catalogue_html() -> str:
    """Shot catalogue sections 1-4 + looks, converted from the markdown tables (dropping the status column)."""
    text = (REPO / "videos" / "SHOT-CATALOG.md").read_text(encoding="utf-8")
    out, rows = ["<h2>5. Shot catalogue (sorted by expected frequency)</h2>"], []

    def flush():
        if rows:
            head, body = rows[0], rows[2:]
            keep = [i for i, h in enumerate(head) if h.lower() != "status"]
            out.append("<table class='cat'><tr>" + "".join(f"<th>{html.escape(head[i])}</th>" for i in keep) + "</tr>")
            for r in body:
                out.append("<tr>" + "".join(f"<td>{_inline(r[i])}</td>" for i in keep if i < len(r)) + "</tr>")
            out.append("</table>")
            rows.clear()

    for line in text.splitlines():
        if line.startswith("|"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        flush()
        if line.startswith("## ") and not line.startswith("## Workflow") and not line.startswith("## Default"):
            out.append(f"<h3>{html.escape(line[3:])}</h3>")
        if line.startswith("## Workflow"):
            break
    flush()
    return "\n".join(out)


def _inline(s: str) -> str:
    s = html.escape(s.replace("`", ""))
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)


def _moments(demo: Path, grid: mapview.Grid, levels: list[dict]) -> list[tuple]:
    """(tick, player, what, where) for jumps, knife swings, shots, damage, deaths, teleports."""
    from demoparser2 import DemoParser

    p = DemoParser(str(demo))
    df = p.parse_ticks(["X", "Y", "Z", "is_airborne", "velocity_Z"])
    where_cache = {}

    def where(name, tick):
        key = (name, tick)
        if key not in where_cache:
            g = df[(df.name == name) & (df.tick <= tick)].tail(1)
            if not len(g):
                where_cache[key] = ""
            else:
                x, y, z = g.X.iloc[0], g.Y.iloc[0], g.Z.iloc[0]
                floor = min(levels, key=lambda l: abs(l["z"] - z))["letter"] if levels else "?"
                where_cache[key] = f"{floor} {grid.square(x, y)}"
        return where_cache[key]

    out = []
    for name, g in df.groupby("name"):
        g = g.sort_values("tick").reset_index(drop=True)
        air = g.is_airborne.fillna(False).astype(bool).to_numpy()
        ticks = g.tick.to_numpy()
        i = 1
        while i < len(g):
            if air[i] and not air[i - 1]:
                j = i
                while j < len(g) and air[j]:
                    j += 1
                vz = g.velocity_Z.iloc[i:i + 3].max()
                dz = g.Z.iloc[min(j, len(g) - 1)] - g.Z.iloc[i - 1]
                if vz > 150:
                    out.append((ticks[i], name, "jump" if abs(dz) < 16 else f"jump ({dz:+.0f} u)", where(name, ticks[i])))
                elif dz < -64:
                    out.append((ticks[i], name, f"drop ({dz:+.0f} u)", where(name, ticks[i])))
                i = j
            i += 1
        pos = g[["X", "Y"]].to_numpy()
        step = np.linalg.norm(np.diff(pos, axis=0), axis=1)
        for k in np.flatnonzero(step > 300):
            out.append((ticks[k + 1], name, "teleport", where(name, ticks[k + 1])))
    events = set(p.list_game_events())
    if "weapon_fire" in events:
        for _, e in p.parse_event("weapon_fire").iterrows():
            weapon = str(e.get("weapon", ""))
            what = "knife swing" if "knife" in weapon or "bayonet" in weapon else f"shot ({weapon.replace('weapon_', '')})"
            out.append((int(e.tick), e.user_name, what, where(e.user_name, int(e.tick))))
    if "player_hurt" in events:
        for _, e in p.parse_event("player_hurt").iterrows():
            out.append((int(e.tick), e.get("user_name", "?"), f"hurt (-{e.get('dmg_health', '?')} hp)",
                        where(e.get("user_name", ""), int(e.tick))))
    if "player_death" in events:
        for _, e in p.parse_event("player_death").iterrows():
            out.append((int(e.tick), e.get("user_name", "?"), "died", where(e.get("user_name", ""), int(e.tick))))
    out.sort()
    # collapse bursts (e.g. 5 knife swings in 2 s) into one line
    merged = []
    for t, name, what, place in out:
        if merged and merged[-1][1] == name and merged[-1][2].split(" ×")[0] == what and t - merged[-1][4] < 3 * TICK:
            m = merged[-1]
            n = int(m[2].split(" ×")[1]) + 1 if " ×" in m[2] else 2
            merged[-1] = (m[0], name, f"{what} ×{n}", m[3], t)
        else:
            merged.append((t, name, what, place, t))
    return merged


def _table(head, rows, cls="") -> str:
    return (f"<table class='{cls}'><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
            + "</table>")


def _movement_html(md: Path) -> str:
    lines = [l for l in md.read_text(encoding="utf-8").splitlines() if l.startswith("|")]
    rows = [[c.strip().strip("`") for c in l.strip().strip("|").split("|")] for l in lines]
    return _table(rows[0], rows[2:], "mv")


def demo_section(name: str, img_dir: Path) -> str:
    summary = json.loads((DEMOS / f"{name}.summary.json").read_text(encoding="utf-8"))
    map_name = summary["map"]
    ov = mapgeo.MAPS_DIR / map_name / f"overview-{name}"
    spaces = json.loads((ov / f"{map_name}-spaces.json").read_text(encoding="utf-8"))
    g = spaces["grid"]
    grid = mapview.Grid((g["x0"], 0), (0, g["ytop"]), g["cell"])
    levels = spaces["levels"]
    players = ", ".join(p["name"] for p in summary["players"])
    parts = [f"<h1 class='demo'>Demo {html.escape(name)} — {html.escape(map_name)}</h1>",
             f"<p><b>Length</b> {summary['duration']} · <b>Players</b> {html.escape(players)} · "
             f"<b>Floors drawn</b> {', '.join(l['letter'] + f' (z≈{l['z']:.0f})' for l in levels)} · "
             f"<b>Recorded</b> {summary['date'][:10]}</p>"]
    moments = _moments(Path(summary["demoPath"]), grid, levels)
    parts.append("<h2>Key moments</h2>")
    parts.append(_table(["time", "player", "what", "floor + square"],
                        [(_fmt(t / TICK), who, what, place) for t, who, what, place, _ in moments], "km")
                 if moments else "<p>None recorded.</p>")
    for lv in levels:
        letter = lv["letter"]
        png = ov / f"{map_name}-{letter}.png"
        jpg = img_dir / f"{name}-{letter}.jpg"
        from PIL import Image
        im = Image.open(png).convert("RGB")
        im.thumbnail((2600, 2600))
        im.save(jpg, quality=82)
        # same rule as the map image: signs on their own floor, other marks within 170 u of the floor
        here = [m for m in spaces["landmarks"] if (_floor_of(m, levels) == letter if m["kind"] == "sign"
                                                   else abs(m["pos"][2] - lv["z"]) <= 170)]
        sp = [s for s in spaces["spaces"] if s["level"] == letter]
        parts.append(f"<div class='floor'><h2>{html.escape(name)} · floor {letter} (z≈{lv['z']:.0f})</h2>"
                     f"<img src='{jpg.name}'></div>")
        parts.append(f"<h3>Floor {letter} landmarks</h3>" + (_table(
            ["id", "what", "square"], [(m["id"], m["name"].replace("_", " "), m["square"]) for m in here], "lm")
            if here else "<p>None.</p>"))
        parts.append(f"<h3>Floor {letter} spaces</h3><p class='sp'>" + " · ".join(
            f"<b>{s['id']}</b> {s['type']} {s['square']}" for s in sp) + "</p>")
    parts.append(f"<h2>{html.escape(name)} movement timeline (every 5 s)</h2>")
    parts.append(_movement_html(ov / f"{name}-movement.md"))
    return "\n".join(parts)


def _floor_of(mark: dict, levels: list[dict]) -> str:
    z = mark["pos"][2]
    if mark["kind"] == "sign":
        return mapview.sign_level(z, levels)["letter"]
    return min(levels, key=lambda l: abs(l["z"] - z))["letter"]


CSS = """
body{font-family:Segoe UI,Arial,sans-serif;font-size:10.5pt;color:#111;margin:0}
h1{font-size:20pt;margin:0 0 6px} h1.demo{page-break-before:always;border-bottom:3px solid #333}
h2{font-size:13.5pt;margin:14px 0 6px} h3{font-size:11pt;margin:10px 0 4px}
.lead{font-size:11.5pt} pre{background:#f3f3f3;padding:8px;font-size:8.5pt;white-space:pre-wrap}
table{border-collapse:collapse;width:100%;margin:4px 0 10px;font-size:8.5pt}
th,td{border:1px solid #bbb;padding:2px 5px;text-align:left;vertical-align:top} th{background:#e8e8e8}
table.mv,table.km{font-size:8pt} .sp{font-size:8pt;line-height:1.5}
.floor{page-break-before:always;page-break-inside:avoid} .floor h2{margin-top:0}
.floor img{max-width:100%;max-height:168mm;border:1px solid #999;display:block;margin:auto}
@page{size:A4 landscape;margin:12mm}
"""


def build(demos: list[str], out: Path) -> Path:
    work = out.parent / (out.stem + "-html")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    body = [GUIDE, _catalogue_html()]
    body.append("<h2>6. Demos in this brief</h2>" + _table(
        ["demo", "map", "length", "floors"],
        [(d, (s := json.loads((DEMOS / f"{d}.summary.json").read_text(encoding="utf-8")))["map"], s["duration"],
          len(json.loads((mapgeo.MAPS_DIR / s["map"] / f"overview-{d}" / f"{s['map']}-spaces.json")
                         .read_text(encoding="utf-8"))["levels"])) for d in demos]))
    for d in demos:
        body.append(demo_section(d, work))
    page = work / "brief.html"
    page.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>CS2 planning brief</title>"
                    f"<style>{CSS}</style></head><body>{''.join(body)}</body></html>", encoding="utf-8")
    edge = next((e for e in EDGE if e.is_file()), None)
    if not edge:
        raise SystemExit(f"Edge not found; open {page} in a browser and print to PDF.")
    subprocess.run([str(edge), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out}", page.as_uri()], check=True, capture_output=True, timeout=600)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demos", nargs="+")
    ap.add_argument("-o", "--out", default=str(REPO / "videos" / "briefs" / "planning-brief.pdf"))
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    print(build(args.demos, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
