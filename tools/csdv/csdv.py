#!/usr/bin/env python3
"""csdv - CS Demo Video helper for CS Demo Manager (CS:DM) + HLAE.

Standard library for build/validate; cinematic cameras (cine.py) also need numpy + scipy, demo positions need
demoparser2 and map collision uses Source2Viewer-CLI (Windows PC). Python 3.9+.

Subcommands:
  summarize <export.json> [-o out.json]   Shrink a `csdm json` match export into a compact summary
                                          (players, rounds, kills, multi-kills, clutches).
  build <spec.json> [-o config.json]      Turn a clip spec into a `csdm video --config-file` config.
  validate <config.json>                  Check a CS:DM video config for problems before rendering.

See .claude/skills/cs-demo-video/SKILL.md for the spec format and the full workflow.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

import campath

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = REPO_ROOT / "videos" / "profile.json"

TEAM_SIDES = {2: "T", 3: "CT"}
MIN_SECONDS_BETWEEN_SEQUENCES = 2  # Same merge rules as CS:DM's "Generate players' sequences".
MAX_SECONDS_BETWEEN_KILLS = 10

# CS:DM strips `#...` and `//...` from the config file with a regex before JSON parsing, even inside strings.
# Any string containing them corrupts the config, so they must never be written.
FORBIDDEN_CONFIG_PATTERNS = ("#", "//", "/*")


# SAFETY: HLAE is a cheat as far as VAC is concerned. Configs must never make the game join a server or load
# anything except the demo CS:DM plays. First token of any cfg command (split on ';') that is refused:
FORBIDDEN_COMMANDS = {
    "connect", "retry", "redirect", "password", "joingame", "join", "matchmaking",
    "map", "changelevel", "map_workshop", "host_workshop_map", "host_workshop_collection", "ds_workshop_changelevel",
    "playdemo", "sv_lan", "rcon", "rcon_password", "rcon_address", "mirv_loadlibrary", "bind", "unbindall",
    "exec", "execifexists", "host_writeconfig", "mirv_exec", "mirv_script_load", "mirv_script_exec", "mirv_vscript_exec",
}
# Placeholder for the repo folder on the Windows PC; scripts/render.ps1 substitutes it before rendering.
REPO_PLACEHOLDER = "{REPO}"
CAMPATH_DIR = "videos/campaths"


class CsdvError(Exception):
    pass


# ---------------------------------------------------------------------------
# helpers


def load_json(path: str | Path) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError as error:
        raise CsdvError(f"File not found: {path}") from error
    except json.JSONDecodeError as error:
        raise CsdvError(f"Invalid JSON in {path}: {error}") from error


def write_json(path: str | Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def sanitize_text(value: str) -> str:
    """Make a string safe for the CS:DM config parser and for HLAE console commands."""
    value = value.replace("#", "＃")  # fullwidth number sign, looks the same in the kill feed
    value = value.replace("//", "/ /").replace("/*", "/ *")
    value = value.replace('"', "'")  # double quotes break mirv_replace_name in CS2
    return value


def parse_time(value, tickrate: float) -> int:
    """Seconds (number) or "mm:ss" / "h:mm:ss" demo time -> tick."""
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        parts = [float(p) for p in str(value).split(":")]
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
    return max(1, round(seconds * tickrate))


# ---------------------------------------------------------------------------
# summarize


def summarize_match(match: dict) -> dict:
    tickrate = match.get("tickrate") or 64
    players = [
        {
            "steamId": p.get("steamId"),
            "name": p.get("name"),
            "team": p.get("teamName"),
            "kills": p.get("killCount"),
            "deaths": p.get("deathCount"),
            "assists": p.get("assistCount"),
            "headshots": p.get("headshotCount"),
        }
        for p in match.get("players", [])
    ]
    rounds = [
        {
            "number": r.get("number"),
            "startTick": r.get("startTick"),
            "freezetimeEndTick": r.get("freezetimeEndTick"),
            "endTick": r.get("endTick"),
            "endOfficiallyTick": r.get("endOfficiallyTick"),
            "winnerSide": TEAM_SIDES.get(r.get("winnerSide"), r.get("winnerSide")),
            "winnerTeam": r.get("winnerTeamName"),
            "endReason": r.get("endReason"),
            "score": f"{r.get('teamAScore')}-{r.get('teamBScore')}",
        }
        for r in match.get("rounds", [])
    ]
    kills = sorted(
        (
            {
                "tick": k.get("tick"),
                "time": format_seconds(k.get("tick", 0) / tickrate),
                "round": k.get("roundNumber"),
                "killer": k.get("killerName"),
                "killerSteamId": k.get("killerSteamId"),
                "victim": k.get("victimName"),
                "victimSteamId": k.get("victimSteamId"),
                "weapon": k.get("weaponName"),
                "headshot": bool(k.get("isHeadshot")),
                "wallbang": (k.get("penetratedObjects") or 0) > 0,
                "noScope": bool(k.get("isNoScope")),
                "throughSmoke": bool(k.get("isThroughSmoke")),
                "killerBlind": bool(k.get("isKillerBlinded")),
                "airborne": bool(k.get("isKillerAirborne")),
                "trade": bool(k.get("isTradeKill")),
                "distance": round(k.get("distance") or 0, 1),
                "killerPos": [round(k.get(f"killer{a}") or 0, 1) for a in "XYZ"],
                "victimPos": [round(k.get(f"victim{a}") or 0, 1) for a in "XYZ"],
            }
            for k in match.get("kills", [])
        ),
        key=lambda k: k["tick"] or 0,
    )

    # Multi-kills: kills by the same player in the same round (teamkills and suicides excluded).
    by_player_round: dict[tuple[str, int], list[dict]] = {}
    for kill in kills:
        if not kill["killerSteamId"] or kill["killerSteamId"] == kill["victimSteamId"]:
            continue
        by_player_round.setdefault((kill["killerSteamId"], kill["round"]), []).append(kill)
    multi_kills = []
    for (steam_id, round_number), round_kills in by_player_round.items():
        if len(round_kills) < 2:
            continue
        first, last = round_kills[0]["tick"], round_kills[-1]["tick"]
        multi_kills.append(
            {
                "player": round_kills[0]["killer"],
                "steamId": steam_id,
                "round": round_number,
                "kills": len(round_kills),
                "label": {2: "2k", 3: "3k", 4: "4k", 5: "ACE"}.get(len(round_kills), f"{len(round_kills)}k"),
                "firstKillTick": first,
                "lastKillTick": last,
                "spanSeconds": round((last - first) / tickrate, 1),
                "headshots": sum(1 for k in round_kills if k["headshot"]),
                "weapons": sorted({k["weapon"] for k in round_kills if k["weapon"]}),
            }
        )
    multi_kills.sort(key=lambda m: (-m["kills"], m["spanSeconds"]))

    clutches = [
        {
            "player": c.get("clutcherName"),
            "steamId": c.get("clutcherSteamId"),
            "round": c.get("roundNumber"),
            "situation": f"1v{c.get('opponentCount')}",
            "won": c.get("won"),
            "kills": c.get("clutcherKillCount"),
            "tick": c.get("tick"),
        }
        for c in match.get("clutches", [])
    ]

    team_a, team_b = match.get("teamA") or {}, match.get("teamB") or {}
    return {
        "summaryVersion": 1,
        "name": match.get("name"),
        "demoPath": match.get("demoFilePath"),
        "checksum": match.get("checksum"),
        "game": match.get("game"),
        "map": match.get("mapName"),
        "date": match.get("date"),
        "source": match.get("source"),
        "tickrate": tickrate,
        "tickCount": match.get("tickCount"),
        "duration": format_seconds(match.get("duration") or 0),
        "teams": [
            {"name": team_a.get("name"), "score": team_a.get("score")},
            {"name": team_b.get("name"), "score": team_b.get("score")},
        ],
        "players": players,
        "rounds": rounds,
        "multiKills": multi_kills,
        "clutches": clutches,
        "kills": kills,
    }


def format_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def command_summarize(args) -> int:
    data = load_json(args.export)
    matches = data if isinstance(data, list) else [data]
    for match in matches:
        summary = summarize_match(match)
        output = args.output or str(Path(args.export).with_suffix("")) + ".summary.json"
        write_json(output, summary)
        top = ", ".join(f"{m['player']} {m['label']} (R{m['round']})" for m in summary["multiKills"][:5])
        print(
            f"Wrote {output}\n  {summary['map']} | {len(summary['rounds'])} rounds | {len(summary['kills'])} kills"
            f" | tickrate {summary['tickrate']}\n  Top multi-kills: {top or 'none'}"
        )
    return 0


# ---------------------------------------------------------------------------
# build


class Match:
    """Lookup helpers over a summary (or a minimal stand-in when no summary is given)."""

    def __init__(self, summary: dict | None):
        self.summary = summary or {}
        self.tickrate = self.summary.get("tickrate") or 64
        self.tick_count = self.summary.get("tickCount") or 10**9
        self.players = self.summary.get("players", [])
        self.kills = self.summary.get("kills", [])
        self.rounds = {r["number"]: r for r in self.summary.get("rounds", [])}

    def require_summary(self, what: str) -> None:
        if not self.summary:
            raise CsdvError(f"A clip of type '{what}' needs \"summary\" in the spec (run export-demo.ps1 first).")

    def player(self, ref: str) -> dict:
        """Find a player by SteamID64 or case-insensitive name (exact, then substring)."""
        ref = str(ref)
        for p in self.players:
            if p["steamId"] == ref:
                return p
        if re.fullmatch(r"\d{17}", ref):
            return {"steamId": ref, "name": ref}
        self.require_summary("player name lookup")
        lowered = ref.lower()
        exact = [p for p in self.players if (p["name"] or "").lower() == lowered]
        partial = [p for p in self.players if lowered in (p["name"] or "").lower()]
        matches = exact or partial
        if len(matches) != 1:
            names = ", ".join(p["name"] for p in self.players)
            raise CsdvError(f"Player '{ref}' matched {len(matches)} players. Players: {names}")
        return matches[0]

    def round(self, number: int) -> dict:
        self.require_summary("round")
        if number not in self.rounds:
            raise CsdvError(f"Round {number} not found (demo has rounds {min(self.rounds)}-{max(self.rounds)}).")
        return self.rounds[number]

    def clamp(self, tick: int) -> int:
        return max(1, min(self.tick_count, int(tick)))


def make_sequence(start: int, end: int, settings: dict, cameras: list[dict], cfg: str | None) -> dict:
    sequence = {
        "number": 0,  # assigned later
        "startTick": start,
        "endTick": end,
        "showXRay": settings["showXRay"],
        "showAssists": settings["showAssists"],
        "showOnlyDeathNotices": settings["showOnlyDeathNotices"],
        "deathNoticesDuration": settings["deathNoticesDuration"],
        "playerVoicesEnabled": settings["playerVoicesEnabled"],
        "recordAudio": settings["recordAudio"],
        "playersOptions": [],
        "playerCameras": cameras,
        "cameras": [],
    }
    if cfg:
        sequence["cfg"] = cfg
    return sequence


def camera(tick: int, player: dict) -> dict:
    return {"tick": tick, "playerSteamId": player["steamId"], "playerName": sanitize_text(player["name"] or "")}


def event_sequences(clip: dict, match: Match, settings: dict, cfg: str | None) -> list[dict]:
    """kills/deaths clips, merged the same way as CS:DM's sequence generator."""
    kind = clip["type"]
    match.require_summary(kind)
    player = match.player(clip["player"])
    key = "killerSteamId" if kind == "kills" else "victimSteamId"
    rounds = set(clip.get("rounds") or [])
    weapons = {w.lower() for w in clip.get("weapons") or []}
    events = [
        k
        for k in match.kills
        if k[key] == player["steamId"]
        and (not rounds or k["round"] in rounds)
        and (not weapons or (k["weapon"] or "").lower() in weapons)
        and (not clip.get("headshotsOnly") or k["headshot"])
    ]
    if clip.get("minKillsInRound") and kind == "kills":
        counts: dict[int, int] = {}
        for k in events:
            counts[k["round"]] = counts.get(k["round"], 0) + 1
        events = [k for k in events if counts[k["round"]] >= clip["minKillsInRound"]]
    if not events:
        raise CsdvError(f"Clip {clip} matched no {kind} for {player['name']}.")

    tickrate = match.tickrate
    before = round(tickrate * clip.get("before", settings["secondsBefore"]))
    after = round(tickrate * clip.get("after", settings["secondsAfter"]))
    gap = round(tickrate * MIN_SECONDS_BETWEEN_SEQUENCES)
    max_between = round(tickrate * MAX_SECONDS_BETWEEN_KILLS)
    enemy_pov = clip.get("perspective") == "enemy"

    def focus(kill: dict) -> dict:
        steam_id = kill[key]
        if enemy_pov:
            steam_id = kill["victimSteamId"] if kind == "kills" else kill["killerSteamId"]
        return match.player(steam_id)

    sequences: list[dict] = []
    for index, kill in enumerate(events):
        start = match.clamp(kill["tick"] - before)
        end = match.clamp(kill["tick"] + after)
        next_kill = events[index + 1] if index + 1 < len(events) else None
        if next_kill and kill["tick"] + max_between >= next_kill["tick"]:
            end = match.clamp(next_kill["tick"] + after)
        if sequences and sequences[-1]["endTick"] + gap >= start:
            previous = sequences[-1]
            previous["endTick"] = max(previous["endTick"], end)
            midpoint = round((events[index - 1]["tick"] + kill["tick"]) / 2)
            target = focus(kill)
            if previous["playerCameras"][-1]["playerSteamId"] != target["steamId"]:
                previous["playerCameras"].append(camera(midpoint, target))
            continue
        sequences.append(make_sequence(start, end, settings, [camera(start, focus(kill))], cfg))
    return sequences


def build_clip(clip: dict, match: Match, settings: dict, spec_cfg: str | None) -> list[dict]:
    kind = clip.get("type")
    cfg_lines = [line for line in (spec_cfg, clip.get("cfg")) if line]
    cfg = "\n".join(cfg_lines) or None
    tickrate = match.tickrate

    if kind in ("kills", "deaths"):
        return event_sequences(clip, match, settings, cfg)

    if kind == "round":
        rnd = match.round(int(clip["round"]))
        start_from = rnd["freezetimeEndTick"] if clip.get("skipFreezetime", True) else rnd["startTick"]
        start = match.clamp(start_from - round(tickrate * clip.get("before", 2)))
        end = match.clamp(rnd["endTick"] + round(tickrate * clip.get("after", 2)))
        cameras = [camera(start, match.player(clip["player"]))] if clip.get("player") else []
        return [make_sequence(start, end, settings, cameras, cfg)]

    if kind in ("ticks", "time"):
        if kind == "ticks":
            start, end = int(clip["start"]), int(clip["end"])
        else:
            start, end = parse_time(clip["start"], tickrate), parse_time(clip["end"], tickrate)
        start, end = match.clamp(start), match.clamp(end)
        pov = clip.get("pov") or clip.get("player")
        cameras = [camera(start, match.player(pov))] if pov else []
        for switch in clip.get("povSwitches", []):
            tick = int(switch["tick"]) if "tick" in switch else parse_time(switch["time"], tickrate)
            cameras.append(camera(match.clamp(tick), match.player(switch["player"])))
        return [make_sequence(start, end, settings, cameras, cfg)]

    raise CsdvError(f"Unknown clip type '{kind}'. Use kills, deaths, round, ticks or time.")


_GEOMETRY_CACHE: dict = {}
# The campath is anchored this many ticks before the recording starts. Anchored exactly on startTick, the first
# recorded frame still showed the spectator view (verified 2026-09-27, one frame per clip).
CAMPATH_PREROLL_TICKS = 8


def campath_start_tick(sequence: dict) -> int:
    return max(1, sequence["startTick"] - CAMPATH_PREROLL_TICKS)


def map_geometry(map_name: str):
    """Collision mesh for camera placement, or None (with a warning) if it can't be exported/loaded."""
    if map_name not in _GEOMETRY_CACHE:
        try:
            import mapgeo

            _GEOMETRY_CACHE[map_name] = mapgeo.MapGeometry.for_map(map_name)
        except Exception as error:  # noqa: BLE001 - cameras still work without collision checks
            print(f"WARNING: no map geometry for {map_name} ({error}); cameras are not checked against walls.",
                  file=sys.stderr)
            _GEOMETRY_CACHE[map_name] = None
    return _GEOMETRY_CACHE[map_name]


def cinematic_keys(clip: dict, shot: dict, sequence: dict, match: Match, demo_path: str, spec_name: str,
                   clip_index: int, write_files: bool) -> list:
    """Film-style shot around a player, from per-tick demo positions (see cine.py)."""
    import cine
    import positions

    subject_ref = shot.get("subject") or clip.get("pov") or clip.get("player")
    if not subject_ref:
        raise CsdvError(f"Clip {clip_index + 1}: cinematic camera needs \"subject\" (player name or SteamID64).")
    subject = match.player(subject_ref)
    tracks = positions.load_tracks(demo_path)
    if subject["steamId"] not in tracks:
        raise CsdvError(f"Clip {clip_index + 1}: no positions for {subject['name']} in the demo.")
    geometry = map_geometry(match.summary.get("map", "")) if shot.get("collision", True) else None
    params = {k: v for k, v in shot.items() if k not in ("shot", "subject", "collision", "interp", "hideViewmodel", "hud",
                                                           "over", "why", "technique")}
    if shot.get("over"):
        over = match.player(shot["over"])
        if over["steamId"] not in tracks:
            raise CsdvError(f"Clip {clip_index + 1}: no positions for {over['name']} in the demo.")
        params["overTrack"] = tracks[over["steamId"]]
    try:
        result = cine.build(shot["shot"], tracks[subject["steamId"]], campath_start_tick(sequence), sequence["endTick"],
                            match.tickrate, params, geometry)
    except cine.ShotError as error:
        raise CsdvError(f"Clip {clip_index + 1}: {error}") from error
    for warning in result.warnings:
        print(f"WARNING: clip {clip_index + 1} ({shot['shot']}): {warning}", file=sys.stderr)
    if write_files:
        preview = REPO_ROOT / "videos" / "previews" / f"{spec_name}-clip{clip_index + 1}.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        cine.preview(result, preview, geometry, f"clip {clip_index + 1}: {shot['shot']} {subject['name']}")
    # Spectate the subject in chase mode (apply_view): in first person CS2 hides the spectated player's model.
    if not sequence["playerCameras"]:
        sequence["playerCameras"] = [camera(sequence["startTick"], subject)]
    return result.keys


def attach_camera(clip: dict, sequences: list[dict], match: Match, spec_name: str, clip_index: int,
                  write_files: bool, demo_path: str = "") -> None:
    """Drive the view with an HLAE campath for this clip, synced to the recording start tick."""
    shot = clip.get("camera")
    if not shot:
        return
    if len(sequences) != 1:
        raise CsdvError(f"Clip {clip_index + 1}: \"camera\" needs a clip that makes exactly one sequence (round/ticks/time).")
    sequence = sequences[0]
    duration = (sequence["endTick"] - sequence["startTick"]) / match.tickrate
    if "file" in shot:
        relative = shot["file"].replace("\\", "/")
    else:
        import cine

        # "static" exists in both: with a subject (or a clip POV) it is the placed cinematic shot, otherwise the
        # plain coordinate shot from campath ("pos" + "lookAt").
        has_subject = bool(shot.get("subject") or clip.get("pov") or clip.get("player"))
        if shot.get("shot") in cine.SHOTS and (shot.get("shot") != "static" or has_subject):
            keys = cinematic_keys(clip, shot, sequence, match, demo_path, spec_name, clip_index, write_files)
        else:
            try:
                keys = campath.build_shot(shot, duration)
            except (KeyError, ValueError) as error:
                raise CsdvError(f"Clip {clip_index + 1}: bad camera {shot}: {error}") from error
            # Hold the first view during the pre-roll so the shot itself still starts on the first recorded frame.
            pre = (sequence["startTick"] - campath_start_tick(sequence)) / match.tickrate
            if pre > 0:
                first = keys[0]
                keys = [campath.Key(0.0, first.x, first.y, first.z, first.pitch, first.yaw, first.roll, first.fov)] + [
                    campath.Key(k.t + pre, k.x, k.y, k.z, k.pitch, k.yaw, k.roll, k.fov) for k in keys]
        relative = f"{CAMPATH_DIR}/{spec_name}-clip{clip_index + 1}.xml"
        if write_files:
            path = REPO_ROOT / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(campath.to_xml(keys, shot.get("interp", "cubic")), encoding="utf-8", newline="\n")
    lines = [
        "mirv_campath enabled 0",
        "mirv_campath clear",
        f'mirv_campath load "{REPO_PLACEHOLDER}/{relative}"',
        "mirv_campath offset none",
        "mirv_campath enabled 1",
        # CS:DM runs cfg ~1 s before the start tick; HLAE's command system fires on exact demo ticks.
        "mirv_cmd clear",
        f"mirv_cmd addAtTick {campath_start_tick(sequence)} mirv_campath offset current",
        f"mirv_cmd addAtTick {sequence['endTick']} mirv_campath enabled 0",
    ]
    if shot.get("hideViewmodel", True):
        lines.append("r_drawviewmodel 0")
    if not shot.get("hud", False):  # film shots: no HUD, kill feed included; restored when the shot ends
        lines += ["cl_drawhud 0", f"mirv_cmd addAtTick {sequence['endTick']} cl_drawhud 1"]
    sequence["cfg"] = "\n".join(filter(None, [sequence.get("cfg"), *lines]))


# Third person: CS:DM always selects a player with `spec_mode 1` + `spec_player` (first person). HLAE's mirv_cmd then
# switches to CS2's chase camera (`spec_mode 3`, verified 2026-09-27; `spec_mode 5` stays first person) a couple of
# ticks after each player selection. The first selection is moved before the recording start so frame 1 is already
# third person.
VIEW_PREROLL_TICKS = 32
VIEW_SWITCH_DELAY_TICKS = 2
SPEC_MODE_BY_VIEW = {"third": 3}


def apply_view(clip: dict, sequences: list[dict], default_view: str) -> None:
    view = clip.get("view", default_view)
    if view not in ("first", "third"):
        raise CsdvError(f"Unknown view '{view}'. Use \"first\" or \"third\".")
    camera_clip = bool(clip.get("camera"))
    if camera_clip:
        # An HLAE campath drives the view. Spectate in chase mode anyway, so the spectated player's model is drawn
        # (first-person spectating hides it). attach_camera already cleared mirv_cmd, so don't clear again.
        view = "third"
    if view == "first":
        # Drop switches scheduled by earlier sequences: with order "spec" CS:DM can seek back over their ticks.
        for sequence in sequences:
            sequence["cfg"] = "\n".join(filter(None, [sequence.get("cfg"), "mirv_cmd clear"]))
        return
    for sequence in sequences:
        cameras = sequence["playerCameras"]
        if not cameras:
            continue
        if cameras[0]["tick"] == sequence["startTick"]:
            cameras[0]["tick"] = max(1, sequence["startTick"] - VIEW_PREROLL_TICKS)
        lines = ([] if camera_clip else ["mirv_cmd clear"]) + [
            f"mirv_cmd addAtTick {cam['tick'] + VIEW_SWITCH_DELAY_TICKS} spec_mode {SPEC_MODE_BY_VIEW[view]}"
            for cam in cameras
        ]
        sequence["cfg"] = "\n".join(filter(None, [sequence.get("cfg"), *lines]))


def players_options(match: Match, highlight: set[str], voice: bool) -> list[dict]:
    return [
        {
            "playerName": sanitize_text(p["name"] or ""),
            "steamId": p["steamId"],
            "showKill": True,
            "highlightKill": p["steamId"] in highlight,
            "isVoiceEnabled": voice,
        }
        for p in match.players
    ]


def build_config(spec: dict, profile: dict, summary: dict | None, write_files: bool = False) -> dict:
    settings = deep_merge(profile, spec.get("overrides", {}))
    seq_settings = settings["sequenceDefaults"]
    match = Match(summary)

    demo_path = spec.get("demo") or match.summary.get("demoPath")
    if not demo_path or not str(demo_path).lower().endswith(".dem"):
        raise CsdvError("The spec needs \"demo\" (Windows path to the .dem) or a summary containing demoPath.")

    sequences: list[dict] = []
    spec_name = re.sub(r"[^A-Za-z0-9._-]", "-", spec.get("name") or "spec")
    for index, clip in enumerate(spec.get("clips", [])):
        clip_sequences = build_clip(clip, match, seq_settings, spec.get("cfg"))
        attach_camera(clip, clip_sequences, match, spec_name, index, write_files, demo_path)
        apply_view(clip, clip_sequences, spec.get("view", settings.get("view", "first")))
        sequences.extend(clip_sequences)
    if not sequences:
        raise CsdvError("The spec has no clips.")

    # Kill-feed options: show everyone's kills, highlight the focused players' kills (HLAE only).
    highlight = {match.player(ref)["steamId"] for ref in spec.get("highlightPlayers", [])}
    for clip in spec.get("clips", []):
        if clip.get("player") and clip.get("type") in ("kills", "round"):
            highlight.add(match.player(clip["player"])["steamId"])
    if match.players and settings["recordingSystem"] == "HLAE":
        options = players_options(match, highlight, seq_settings["playerVoicesEnabled"])
        for sequence in sequences:
            sequence["playersOptions"] = copy.deepcopy(options)

    if spec.get("order", "chronological") == "chronological":
        sequences.sort(key=lambda s: s["startTick"])
    for number, sequence in enumerate(sequences, start=1):
        sequence["number"] = number

    ffmpeg = settings["ffmpegSettings"]
    config = {
        "demoPath": demo_path,
        "recordingSystem": settings["recordingSystem"],
        "recordingOutput": settings["recordingOutput"],
        "encoderSoftware": settings["encoderSoftware"],
        "framerate": settings["framerate"],
        "width": settings["width"],
        "height": settings["height"],
        "closeGameAfterRecording": settings["closeGameAfterRecording"],
        "trueView": settings["trueView"],
        "concatenateSequences": spec.get("concatenate", settings["concatenateSequences"]),
        "outputFileName": sanitize_text(spec.get("outputFileName") or spec.get("name") or "{map}-{date}"),
        "ffmpegSettings": {
            "customLocationEnabled": False,
            "customExecutableLocation": "",
            "audioBitrate": ffmpeg["audioBitrate"],
            "constantRateFactor": ffmpeg["constantRateFactor"],
            "videoContainer": ffmpeg["videoContainer"],
            "videoCodec": ffmpeg["videoCodec"],
            "audioCodec": ffmpeg["audioCodec"],
            "inputParameters": ffmpeg["inputParameters"],
            "outputParameters": ffmpeg["outputParameters"],
        },
        "sequences": sequences,
    }
    output_folder = spec.get("outputFolder") or settings.get("outputFolder")
    if output_folder:
        config["outputFolderPath"] = output_folder
    return config


def command_build(args) -> int:
    spec_path = Path(args.spec)
    spec = load_json(spec_path)
    profile = load_json(args.profile)
    summary = None
    if spec.get("summary"):
        summary_path = Path(spec["summary"])
        if not summary_path.is_absolute():
            summary_path = REPO_ROOT / summary_path
        summary = load_json(summary_path)
    config = build_config(spec, profile, summary, write_files=True)
    problems = validate_config(config)
    errors = [p for p in problems if p.startswith("ERROR")]
    for problem in problems:
        print(problem, file=sys.stderr)
    if errors:
        return 1
    output = args.output or str(REPO_ROOT / "videos" / "configs" / f"{spec.get('name') or spec_path.stem}.csdm.json")
    write_json(output, config)
    print_plan(config, output)
    return 0


def print_plan(config: dict, output: str) -> None:
    fps, tickrate = config["framerate"], 64
    total = 0.0
    print(f"Wrote {output}")
    print(f"  {config['width']}x{config['height']} @ {fps} fps | {config['recordingSystem']} -> {config['encoderSoftware']}")
    for s in config["sequences"]:
        seconds = (s["endTick"] - s["startTick"]) / tickrate
        total += seconds
        povs = " -> ".join(c["playerName"] for c in s["playerCameras"]) or "(demo camera)"
        print(f"  #{s['number']:>2}  ticks {s['startTick']}-{s['endTick']}  {seconds:5.1f}s  POV {povs}")
    print(f"  Total ~{total:.0f}s of footage (assuming 64 tick), concatenate={config['concatenateSequences']}")


# ---------------------------------------------------------------------------
# validate

SEQUENCE_KEYS = {
    "number": int,
    "startTick": int,
    "endTick": int,
    "showXRay": bool,
    "showAssists": bool,
    "showOnlyDeathNotices": bool,
    "deathNoticesDuration": (int, float),
    "playerVoicesEnabled": bool,
    "recordAudio": bool,
    "playersOptions": list,
    "playerCameras": list,
    "cameras": list,
}


def iter_strings(value, path="$"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from iter_strings(key, f"{path}.<key>")
            yield from iter_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_strings(item, f"{path}[{index}]")


def validate_config(config: dict) -> list[str]:
    problems: list[str] = []

    def error(message):
        problems.append(f"ERROR: {message}")

    def warn(message):
        problems.append(f"WARNING: {message}")

    for path, text in iter_strings(config):
        for pattern in FORBIDDEN_CONFIG_PATTERNS:
            if pattern in text:
                error(f"{path} contains '{pattern}' which CS:DM's comment stripper removes: {text!r}")

    for path, text in iter_strings(config):
        if "steam://" in text.lower():
            error(f"SAFETY: {path} contains a steam:// link")
    if not str(config.get("demoPath", "")).lower().endswith(".dem"):
        error("demoPath must end with .dem")
    if config.get("recordingSystem") not in ("HLAE", "CS"):
        error("recordingSystem must be HLAE or CS")
    if config.get("recordingOutput") not in ("video", "images", "images-and-video"):
        error("recordingOutput must be video, images or images-and-video")
    if config.get("encoderSoftware") not in ("FFmpeg", "VirtualDub"):
        error("encoderSoftware must be FFmpeg or VirtualDub")
    if (config.get("width") or 0) < 800 or (config.get("height") or 0) < 600:
        error("width/height must be at least 800x600")
    ffmpeg = config.get("ffmpegSettings") or {}
    if ffmpeg.get("videoContainer") not in ("mp4", "avi", "mkv", "mov"):
        error("ffmpegSettings.videoContainer must be mp4, avi, mkv or mov")
    if ffmpeg.get("videoContainer") == "mp4" and "mp3" in str(ffmpeg.get("audioCodec")):
        warn("mp3 audio in mp4 has poor player support; prefer aac")
    if ffmpeg.get("outputParameters"):
        warn("outputParameters replaces -crf and is also appended after the output file in HLAE's mux step")
    if config.get("concatenateSequences") and config.get("encoderSoftware") != "FFmpeg":
        warn("concatenation always uses FFmpeg; it must be installed")

    sequences = config.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        error("sequences must be a non-empty list")
        return problems
    numbers = set()
    previous_end = 0
    for sequence in sorted(sequences, key=lambda s: s.get("startTick", 0)):
        label = f"sequence {sequence.get('number')}"
        for key, expected in SEQUENCE_KEYS.items():
            if not isinstance(sequence.get(key), expected) or (expected is int and isinstance(sequence.get(key), bool)):
                error(f"{label}: '{key}' missing or wrong type (CS:DM does not fill defaults for config sequences)")
        start, end = sequence.get("startTick", 0), sequence.get("endTick", 0)
        if isinstance(start, int) and isinstance(end, int):
            if start < 1 or end <= start:
                error(f"{label}: needs 1 <= startTick < endTick")
            if start <= previous_end:
                warn(f"{label}: overlaps the previous sequence (ticks {start}-{end})")
            previous_end = max(previous_end, end)
            if start < 64:
                warn(f"{label}: starts in the first second; the setup commands run at tick 1 anyway")
        if sequence.get("number") in numbers:
            error(f"{label}: duplicate sequence number")
        numbers.add(sequence.get("number"))
        for cam in sequence.get("playerCameras") or []:
            if not re.fullmatch(r"\d{17}", str(cam.get("playerSteamId", ""))):
                error(f"{label}: playerCameras entry has an invalid SteamID64: {cam}")
            # Up to 1 s before the start is fine: CS:DM seeks to startTick - tickrate (third-person pre-roll uses it).
            if isinstance(start, int) and isinstance(end, int) and not (start - 64 <= cam.get("tick", 0) <= end):
                warn(f"{label}: camera switch at tick {cam.get('tick')} is outside the sequence")
        for line in str(sequence.get("cfg") or "").split("\n"):
            if line.strip().startswith(("startmovie", "endmovie", "mirv_streams record start", "mirv_streams record end")):
                error(f"{label}: cfg must not start/stop recording itself: {line!r}")
            for problem in unsafe_command_problems(line):
                error(f"{label}: SAFETY: {problem}")
    return problems


def unsafe_command_problems(line: str) -> list[str]:
    """Commands that could join a server, load a map or run unknown code are never allowed in a config."""
    problems = []
    for part in re.split(r"[;\n]", line):
        tokens = part.strip().lstrip("+").split()
        if not tokens:
            continue
        # mirv_cmd addAtTick <tick> <command...> schedules another command: check that one too.
        if tokens[0].lower() == "mirv_cmd" and len(tokens) > 3 and tokens[1].lower() in ("addattick", "addattime"):
            tokens = tokens[3:]
        if tokens[0].lower() in FORBIDDEN_COMMANDS:
            problems.append(f"forbidden command {tokens[0]!r} in {line.strip()!r}")
    if "steam://" in line.lower():
        problems.append(f"steam:// links are forbidden: {line.strip()!r}")
    return problems


def command_validate(args) -> int:
    problems = validate_config(load_json(args.config))
    for problem in problems:
        print(problem)
    errors = [p for p in problems if p.startswith("ERROR")]
    print(f"{len(errors)} error(s), {len(problems) - len(errors)} warning(s)")
    return 1 if errors else 0


def command_suggest(args) -> int:
    """Analyze a stretch of a demo and suggest shots per beat; writes a ready-to-build spec with alternatives."""
    import positions
    import scene

    summary = load_json(args.summary)
    match = Match(summary)
    tickrate = match.tickrate
    subject = match.player(args.subject)
    start = parse_time(args.start, tickrate) if args.start else 1
    end = parse_time(args.end, tickrate) if args.end else int(summary.get("tickCount") or 0)
    demo = args.demo or summary.get("demoPath")
    tracks = positions.load_tracks(demo)
    if subject["steamId"] not in tracks:
        raise CsdvError(f"No positions for {subject['name']} in {demo}.")
    geometry = None if args.no_geometry else map_geometry(summary.get("map", ""))
    events = []
    for k in summary.get("kills", []):
        if k.get("victimSteamId") == subject["steamId"]:
            events.append({"type": "death", "tick": int(k["tick"])})
        elif k.get("killerSteamId") == subject["steamId"]:
            events.append({"type": "kill", "tick": int(k["tick"])})
    planned = scene.plan(tracks, subject["steamId"], start, end, args.mood, geometry, events, args.top, tickrate)

    name = args.name or re.sub(r"[^A-Za-z0-9._-]", "-", f"{summary.get('name', 'demo')}-{args.mood}-suggested")
    clips, lines = [], [f"# Shot suggestions: {summary.get('name')} / {subject['name']} / mood {args.mood}", ""]
    for number, (beat, options) in enumerate(planned, start=1):
        when = f"{format_seconds(beat.start / tickrate)}-{format_seconds(beat.end / tickrate)}"
        head = f"Beat {number}  {when} ({beat.seconds:.1f}s)  tension {beat.tension:.1f}  " \
               f"[{', '.join(sorted(beat.tags)) or '-'}]"
        print(head)
        print(f"  {beat.describe(subject['name'])}")
        lines += [f"## {head}", "", beat.describe(subject["name"]), ""]
        if not options:
            print("  (no shot keeps the subject in view here)")
            lines += ["(no shot keeps the subject in view here)", ""]
            continue
        for rank, option in enumerate(options, start=1):
            marker = "*" if rank == 1 else " "
            print(f"  {marker}{rank}. {option.recipe.technique:<42} shot={option.camera['shot']:<10} "
                  f"score {option.score:.2f}  visible {option.visible:.0%}")
            print(f"       {option.why}")
            lines.append(f"{rank}. **{option.recipe.technique}** (`{option.camera['shot']}`, score {option.score:.2f}): "
                         f"{option.why}")
        lines.append("")
        best = options[0]
        clips.append({
            "type": "ticks", "start": beat.start, "end": beat.end,
            "camera": {**best.camera, "why": best.why},
            "beat": beat.describe(subject["name"]),
            "alternatives": [{**o.camera, "why": o.why} for o in options[1:]],
        })
    spec = {"name": name, "summary": str(Path(args.summary).as_posix()), "outputFileName": name,
            "concatenate": True, "order": "spec", "clips": clips}
    out = REPO_ROOT / "videos" / "specs" / f"{name}.json"
    write_json(out, spec)
    (out.with_suffix(".md")).write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out} (top pick per beat; alternatives listed per clip) and {out.with_suffix('.md').name}")
    return 0


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="csdv", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("summarize", help="compact a `csdm json` export")
    p.add_argument("export")
    p.add_argument("-o", "--output")
    p.set_defaults(func=command_summarize)

    p = sub.add_parser("build", help="build a CS:DM video config from a clip spec")
    p.add_argument("spec")
    p.add_argument("-o", "--output")
    p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    p.set_defaults(func=command_build)

    p = sub.add_parser("validate", help="check a CS:DM video config")
    p.add_argument("config")
    p.set_defaults(func=command_validate)

    p = sub.add_parser("suggest", help="analyze a demo stretch and suggest cinematic shots per beat")
    p.add_argument("summary", help="videos/demos/<name>.summary.json")
    p.add_argument("--subject", required=True, help="player name or SteamID64")
    p.add_argument("--start", help="demo time (m:ss) or seconds; default: demo start")
    p.add_argument("--end", help="demo time (m:ss) or seconds; default: demo end")
    p.add_argument("--mood", default="neutral", choices=["neutral", "horror"],
                   help="horror adds horror/thriller techniques, weighted by each beat's tension")
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--name", help="output spec name")
    p.add_argument("--demo", help="override the .dem path")
    p.add_argument("--no-geometry", action="store_true")
    p.set_defaults(func=command_suggest)

    args = parser.parse_args(argv)
    # Player names are often non-ASCII; don't crash on legacy Windows consoles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        return args.func(args)
    except CsdvError as error:
        print(f"csdv: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
