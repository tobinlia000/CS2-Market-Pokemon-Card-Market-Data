---
name: cs-demo-video
description: Plan and program Counter-Strike 2 demo videos (frag movies, highlights, round replays, POV clips) rendered by CS Demo Manager + HLAE on the user's Windows PC. Use whenever the user wants a video, clip, highlight, frag movie or recording from a CS2 demo, shares a demo summary, asks to change a render, or reports a CS Demo Manager / HLAE recording problem.
---

# CS2 demo videos with CS Demo Manager + HLAE

## ⛔ SAFETY FIRST — never let a recording session reach a real game
HLAE is a cheat to VAC. Read and obey the SAFETY section of `CLAUDE.md` before anything else. In short:
- Configs/cfg/`mirv_cmd` must never contain server, map-loading, exec/bind or plugin-loading commands
  (`csdv.py validate` blocks them; treat a SAFETY error as a hard stop, never work around it).
- `-insecure` is mandatory and is what keeps VAC off; never touch it or HLAE/CS:DM launch parameters.
- `render.ps1` refuses to start while CS2 is running; after every session remind the user:
  close CS2 → `.\scripts\safety-check.ps1` → launch CS2 from Steam for online play.

**Where you run decides the loop.** On the user's Windows PC (local session, since 2026-09-27), run
`export-demo.ps1` and `render.ps1` yourself. Ask before each render, because CS2 takes over the screen. Then check the
output (ffprobe, extracted frames) and run `safety-check.ps1` afterwards. Never start CS2/HLAE any other way. In a
cloud container you can't run CS2, HLAE or CS Demo Manager (CS:DM): do the programming (moments, specs, configs), push,
and let the user run the two scripts. Git is the hand-off.

**Read `.claude/memory/cs-demo-manager-hlae.md` first** — it has how CS:DM drives HLAE, every setting, the
`mirv_*` commands and known gotchas. Update it (and its "Last updated" line) whenever a render teaches you something.

## User profile (defaults in `videos/profile.json`)
- Windows, CS2. Output **2560x1440 @ 60 fps**, HLAE → FFmpeg direct pipe (no TGA files), x264 CRF 18, mp4 + aac 256k.
- Clips concatenated into one video by default; X-ray off; in-game voice comms off; kill feed only
  (`showOnlyDeathNotices`) with the focused player's kills highlighted. Change a default only when asked,
  and record the preference in the memory file.

## The loop

```
 user (Windows)                              Claude (here)
 export-demo.ps1 -Demo x.dem  ──push──▶  read videos/demos/x.summary.json
                                          propose clips → write videos/specs/x.json
                                          python3 tools/csdv/csdv.py build videos/specs/x.json
 render.ps1 -Config ...        ◀──push──  commit spec + videos/configs/x.csdm.json
 feedback / videos/logs/*.log  ──push──▶  adjust spec, rebuild, update memory
```

1. **Get demo data.** If no summary exists in `videos/demos/`, ask the user to run
   `.\scripts\export-demo.ps1 -Demo "C:\path\match.dem"` and push (one-time setup is in `videos/README.md`).
   For a quick tick/time clip without a summary, the spec's `"demo"` path is enough (tickrate assumed 64).
2. **Plan.** `git pull`, read the summary: `players`, `rounds` (ticks, winner, score), `multiKills` (sorted best
   first), `clutches`, `kills` (tick, `time` = demo m:ss, weapon, headshot, wallbang, noScope, throughSmoke…).
   Propose a short numbered clip list (player, round, what happens, ~length) and confirm with the user
   before building if the request was open-ended ("make a highlight video"). Specific requests: just build.
3. **Write the spec** `videos/specs/<name>.json` (format below), then run
   `python3 tools/csdv/csdv.py build videos/specs/<name>.json` — it writes `videos/configs/<name>.csdm.json`,
   validates it, and prints the sequence plan. Fix every ERROR. Run `python3 -m unittest discover -s tools/csdv`
   if you changed `csdv.py`.
4. **Commit + push** spec and config to the session's branch. Give the user the exact commands:
   `git pull` then `.\scripts\render.ps1 -Config videos\configs\<name>.csdm.json`, and the expected
   footage length (render takes roughly real time plus game start-up per sequence).
5. **Iterate** on feedback. If a render failed, ask for `videos/logs/<name>.csdm.log` to be pushed and diagnose
   with the troubleshooting table.

## Spec format (`videos/specs/<name>.json`)

```jsonc
{
  "name": "mirage-liam-highlights",              // config file name + default output file name
  "summary": "videos/demos/mirage.summary.json", // needed for kills/deaths/round clips and name lookup
  "demo": "C:\\Users\\me\\demos\\mirage.dem",      // optional; defaults to the summary's demoPath
  "outputFileName": "{map}-liam-{date}",         // concat file; placeholders {map} {date} {time} {resolution} {framerate} {game} {checksum} {encoder}
  "outputFolder": "D:\\Renders",                 // optional; default = demo folder (CS:DM adds a per-video sub-folder)
  "concatenate": true,
  "order": "chronological",                      // or "spec" = keep clip order
  "cfg": "cl_draw_only_deathnotices 1",          // console lines run before every sequence (\n-separated)
  "highlightPlayers": ["Liam"],                  // extra players whose kills are highlighted red in the feed
  "overrides": { "framerate": 60, "sequenceDefaults": { "showXRay": false } },  // deep-merged over profile.json
  "clips": [
    { "type": "kills",  "player": "Liam", "rounds": [4, 11], "weapons": ["awp"], "headshotsOnly": false,
      "minKillsInRound": 3, "before": 3, "after": 2, "perspective": "player" },   // perspective "enemy" = victim POV
    { "type": "deaths", "player": "7656119...", "perspective": "enemy" },          // killer's POV of each death
    { "type": "round",  "round": 14, "player": "Liam", "skipFreezetime": true, "before": 2, "after": 2 },
    { "type": "time",   "start": "31:05", "end": "31:20", "pov": "Liam",
      "povSwitches": [{ "time": "31:12", "player": "Bob" }] },
    { "type": "ticks",  "start": 120000, "end": 121000, "pov": "76561198...", "cfg": "mirv_fov 100" }
  ]
}
```
Rules the builder enforces/assumes:
- `player`/`pov` = SteamID64 or name (case-insensitive, exact then substring; ambiguous → error listing names).
- Kill clips merge like CS:DM: kills ≤10 s apart share a sequence (camera switches at the midpoint when the
  focus changes), sequences closer than 2 s merge.
- `time` values are demo time (`m:ss` or seconds) = tick / tickrate — the same as the summary's `time` field,
  **not** the round timer.
- Every sequence gets all CS:DM fields; the config CLI does not fill defaults.
- No `#`, `//` or `/*` anywhere in the config: CS:DM strips them as comments even inside strings. The
  builder rewrites player names (`#` → `＃`, `"` → `'`); in `cfg` never write comments.
- Never put `startmovie`/`mirv_streams record start|end` in `cfg`; CS:DM controls recording.

## Camerawork and stories (custom cameras, workshop maps)
Coordinates are plain world coordinates (x, y, z; z up; yaw 0 = +x, 90 = +y; pitch + = looking down). They work on
**any** map, official or workshop — CS:DM's "map support" only matters for its 2D viewer/radar, not for cameras.

**Third person is the user's main style** and the profile default (`"view": "third"`). Set it per clip or per spec
(`"view": "first"` for POV). It uses CS2's chase camera (`spec_mode 3`, sent through `mirv_cmd addAtTick` a couple of
ticks after each player camera; the first camera is moved 0.5 s before the recording start). Clips with a `"camera"`
ignore `view`.

Add `"camera"` to a `round`/`ticks`/`time` clip (one sequence). csdv writes an HLAE campath XML to
`videos/campaths/<spec>-clip<N>.xml` and adds cfg that loads it and anchors keyframe 0 to the sequence start tick via
`mirv_cmd addAtTick <startTick> mirv_campath offset current` (HLAE's command system runs on exact demo ticks).
```jsonc
"camera": { "shot": "static", "pos": [x, y, z], "lookAt": [x, y, z], "fov": 75 }
"camera": { "shot": "dolly",  "from": [x, y, z], "to": [x, y, z], "lookAt": [x, y, z] }   // or startAngles/endAngles [pitch, yaw]
"camera": { "shot": "orbit",  "center": [x, y, z], "radius": 250, "height": 60, "startDeg": 0, "degrees": 120 }
"camera": { "shot": "keys",   "keys": [ { "t": 0, "pos": [..], "lookAt": [..] }, { "t": 2.5, "pos": [..], "pitch": 10, "yaw": 45, "fov": 70 } ] }
"camera": { "file": "videos/campaths/scout/alley.xml" }        // a path the user recorded with mirv_campath save
// common: "interp": "cubic"|"linear", "hideViewmodel": true
```
Where coordinates come from (best first):
1. **User scouting inside the demo** (works on workshop maps because the demo loads the map): watch the demo from
   CS:DM, switch to free camera, fly to a spot and run `getpos` → paste the `setpos ...;setang ...` line
   (`campath.parse_getpos` reads it). For moves: `mirv_campath clear`, `mirv_campath add` at each spot (≥ 4), then
   `mirv_campath save "<repo>/videos/campaths/scout/<name>.xml"` and push. I retime, smooth and reuse those.
2. **Demo data**: summary `kills[].killerPos/victimPos` (feet; eyes ≈ +64 z). Full per-tick player positions:
   CS:DM's analyzer `csda -demo-path x.dem -format json -positions` — runs here in the cloud if the user shares the
   .dem (e.g. Google Drive); I can then follow players (`campath.track`).
3. Map geometry (walls) is unknown to me → cameras placed only from positions can clip through walls. Keep cameras
   near space players occupied or scouted spots, and ask for a short low-res test render before a long one.
   (Possible future step, unverified: export the workshop VPK geometry with Source2Viewer CLI for collision checks.)
Stories: plan a shot list (scene, subject, shot type, duration), map each shot to a clip + camera, keep
`order: "spec"` so scenes stay in story order, and use `concatenate`.

## Useful per-clip `cfg` lines (CS2 + HLAE)
| Goal | cfg |
|---|---|
| Wider/narrower FOV | `mirv_fov 100` (reset: `mirv_fov default`) |
| Hide weapon model | `r_drawviewmodel 0` |
| Viewmodel position | `mirv_viewmodel enabled 1` + `mirv_viewmodel set <x> <y> <z> <fov> <leftHanded 0/1>` (`*` keeps a value) |
| Reduce flash whiteout | `mirv_noflash <0.0-1.0>` (1 = remove completely, 0 = game default) |
| Hide whole HUD except kill feed | `cl_draw_only_deathnotices 1` (profile default) |
| Full HUD | `cl_draw_only_deathnotices 0` (or `"overrides":{"sequenceDefaults":{"showOnlyDeathNotices":false}}`) |
| Cinematic camera path | user records keyframes in-game (`mirv_campath add` ×≥4, `mirv_campath save "C:\\cams\\a.xml"`), then cfg `mirv_campath load "C:\\cams\\a.xml"` + `mirv_campath enabled 1` |
| Longer kill feed | sequence default `deathNoticesDuration` (seconds) |
Anything uncertain: tell the user to test it on a short clip first, and record the outcome in memory.

## Quality knobs
- CRF 18 ≈ visually lossless for YouTube; 14–16 for heavy editing; ≥ 20 for smaller files.
- Editing-grade: `"overrides": {"recordingOutput": "images-and-video"}` keeps TGA + WAV (≈ 10 MB/frame at
  1440p → 60 fps × 10 s ≈ 6 GB). Warn the user about disk space.
- Motion blur / 4K / 120 fps: possible (HLAE `afxSampler30`, higher fps) but not via CS:DM's standard pipeline —
  discuss before promising.

## Troubleshooting (ask for the log; see memory §2 for the pipeline)
| Symptom | Likely cause → fix |
|---|---|
| Any error when starting CS from CS:DM right after a CS2 update | Update HLAE (CS:DM Settings > Video > HLAE, or a newer release from github.com/advancedfx/advancedfx); try once **without** HLAE (Settings > Playback > Use HLAE off) to tell an HLAE problem from a CS:DM plugin problem; get the exact error text |
| "Steam is not running" / game never starts | Start Steam; CS2 must be installed and up to date |
| HLAE error window, instant exit | CS2 update broke HLAE → update HLAE in CS:DM Settings > Video; else wait for an HLAE release |
| Demo plays but nothing recorded / "raw files not found" | CS:DM CS2 plugin incompatible after a CS2 patch → update CS:DM, or pick a plugin version in Settings > Playback |
| Wrong player on screen | camera tick too close to the seek; add a `povSwitches` entry 1 s after start |
| Window larger than screen at 1440p | Fine: HLAE records the render target; don't move/minimize the window |
| No audio | `recordAudio` true? CS2 volume; audio.wav must exist in `take0000` |
| Kill feed names odd | Names are replaced via `mirv_replace_name`; `#`/`"` are substituted on purpose |
| Config parse error | Something wrote `#` or `//` into a string → rebuild with csdv |
