# Working memory: CS Demo Manager + HLAE

Research notes for building a CS Demo Manager (CS:DM) + HLAE video skill. Facts come from reading the
source code, not only the docs, so they are precise but tied to the versions below. Re-verify after upgrades.

- **CS Demo Manager** v3.20.1 — github.com/akiver/cs-demo-manager (commit 10fc2a9, 2026-09-25). Docs: cs-demo-manager.com/docs (guides/video, cli).
- **HLAE** (Half-Life Advanced Effects, "advancedfx") — github.com/advancedfx/advancedfx (commit 96e13a0, 2026-09-27). Manual: github.com/advancedfx/advancedfx/wiki.
- Last updated: 2026-09-27 (safety, cameras, launch-error research added).

## 0. SAFETY (top priority — see CLAUDE.md for the binding rules)
Verified in source (2026-09-27):
- **HLAE (AfxHookSource2 `DllMain`)**: if the game command line lacks `-insecure`, shows "Please add -insecure…
  AfxHookSource2 will refuse to work without it!" and terminates the game. The HLAE CS2 launcher's
  "-insecure (prevents joining VAC secured server / VAC bans)" checkbox is checked and **disabled** (can't untick).
- HLAE once had a "YOU ARE TRYING TO CONNECT TO A SERVER - THIS WILL GET YOU VAC BANNED" prompt on `connect`;
  in the current CS2 code it is **commented out** → there is no connect guard in HLAE today. `-insecure` is the guard.
- **CS:DM**: always launches CS2 with `-insecure -novid` (both plain and HLAE launches). Its CS2 server plugin
  (`game/csgo/csdm/bin/server.dll`, loaded via a `Game csgo/csdm` line added to `gameinfo.gi`) calls
  `Plat_FatalError` ("CS:DM plugin loaded without the -insecure launch option. Aborting.") if `-insecure` is missing,
  so a leftover plugin can't silently run in a secure session. The plugin restores `gameinfo.gi` from
  `gameinfo.gi.backup` itself; CS:DM also uninstalls it after each run. A crash can leave leftovers →
  `scripts/safety-check.ps1` detects and removes them.
- `-insecure` = VAC not loaded; VAC-secured servers (all Valve matchmaking/Premier, most community servers) refuse
  the connection. Third-party anti-cheats (FACEIT, 5E…) are separate programs — never run them alongside.
- Our guards: `csdv validate` blocks server/map/exec/bind/plugin commands (also inside `mirv_cmd addAtTick`);
  `render.ps1` refuses to start if CS2 is already running and warns if CS2/HLAE stay open after; `safety-check.ps1`
  before online play.

## 1. What each tool is

| | CS Demo Manager | HLAE |
|---|---|---|
| Role | Electron desktop app + CLI (`csdm`) that analyzes CS2/CS:GO demos into PostgreSQL (stats, 2D viewer, heatmaps, downloads, export) and **orchestrates video generation** | Injects hooks into the game (`AfxHookSource2.dll` for CS2, `AfxHookSource` for CS:GO) that add `mirv_*` console commands for recording, cameras, death notices, etc. |
| Platforms | Windows, macOS, Linux | **Windows 10+ only**, .NET Framework 4.6.2+, genuine Steam |
| Safety | — | **Technically a hack. Never join VAC-secured servers with it.** Use only for demos. CS:DM launches with `-insecure`. |

Tip from HLAE: CS2 updates often break HLAE. Steam offline mode avoids surprise updates mid-project. Old HLAE builds for breaking changes are listed on the advancedfx wiki page "CS2: Old versions for breaking changes".

## 2. How CS:DM drives a recording (pipeline)

1. **Preconditions** (`assert-video-generation-is-possible.ts`): at least 1 sequence; HLAE installed if recording system = HLAE; VirtualDub installed if HLAE + VirtualDub + output includes video; FFmpeg installed if encoder = FFmpeg and output includes video; **Steam must be running**. Demo must be analyzed (in the DB).
2. **Cleanup** of old raw files and the old actions file.
3. **Writes a JSON "actions" file next to the demo**: `<demoPath>.json` — a list of sequences, each with `{tick, cmd}` actions (like a VDM file). CS:DM's own **game server plugin** reads it and executes commands at ticks.
4. **Installs the CS2 server plugin**: copies `server.dll` into `<CS2>/game/csgo/csdm/bin/` and patches `game/csgo/gameinfo.gi` (adds `Game csgo/csdm`, keeps a `.backup`). Plugin log: `game/bin/win64/csdm.log`. Uninstalled after the run. Plugin builds are pinned to CS2 patch versions (`14030` … `14168`, `latest`) — selectable in Settings > Playback if a CS2 update breaks things.
5. **Launches the game** (windowed, at the video width/height). With HLAE the command is:
   ```
   "hlae.exe" -noGui -autoStart -noConfig -afxDisableSteamStorage -customLoader
     -hookDllPath "<hlaeDir>\x64\AfxHookSource2.dll" -programPath "<cs2.exe>"
     -cmdLine "-insecure -novid +playdemo \"<demo>\" -width W -height H -sw <user launch params>"
     <user HLAE parameters>
   ```
   (CS:GO uses `-csgoLauncher -csgoExe ... -customLaunchOptions ...`.)
   - Env `USRLOCALCSGO` is set to `~/.csdm/cfg` (or the custom HLAE config folder) so the game uses a **separate cfg/keybinds folder** and doesn't touch the user's own config.
   - For HLAE + FFmpeg video output CS:DM writes `<hlaeDir>/ffmpeg/ffmpeg.ini` with `[Ffmpeg]\nPath=<ffmpeg.exe>` so HLAE can pipe frames straight to FFmpeg.
   - HLAE errors are detected by an "Error - AfxHookSource" window on the game process.
6. **Encodes** each sequence (FFmpeg or VirtualDub), optionally **concatenates** them, moves/deletes raw files, uninstalls the plugin.

### Commands CS:DM injects per sequence (CS2 + HLAE)
At tick 1: `sv_cheats 1`, `volume 1`, hides telemetry (`cl_hud_telemetry_*_show 0`, `cl_trueview_show_status 0`, `r_show_build_info 0`), `mirv_streams record screen enabled 1`, `cl_demo_predict <trueView>`, `cl_draw_only_deathnotices`, `mirv_deathmsg lifetime <sec>`, voice convars (`tv_listen_voice_indices[_h]`).

At setup tick = `startTick − tickrate` (≈1 s before):
```
mirv_streams record startMovieWav 0|1
mirv_streams record name "<output>/<N>-sequence"
mirv_deathmsg clear
spec_show_xray 0|1
mp_display_kill_assists 0|1
# only if output=Video and encoder=FFmpeg (direct pipe, no TGA files):
mirv_streams settings add ffmpeg csdmPreset<N> "-c:v <codec> -pix_fmt yuv420p -crf <crf>|<outputParams> {QUOTE}<folder>\\video.<container>{QUOTE}"
mirv_streams record screen settings csdmPreset<N>
# otherwise preset afxClassic (TGA images)
mirv_streams record fps <framerate>      # HLAE; plain CS uses host_framerate <fps>
<sequence cfg lines, one per line>
mirv_replace_name byXuid add x<steamId> "<name>"     # per-player options
mirv_deathmsg filter add block=1                     # if any player options
mirv_deathmsg filter add attackerMatch=x<steamId> attackerIsLocal=0|1 block=0   # showKill / highlightKill
```
Then: `pause_playback` at `startTick−4` (hides loading tint), `demo_gototick` to setup tick, `spec_mode 1` + `spec_player <slot>` at each player-camera tick, `spec_goto x y z pitch yaw` at each custom-camera tick, `mirv_streams record start` at `startTick`, `mirv_streams record end` at `endTick`, then jump to the next sequence at `endTick+64` or `quit` after the last one if "close game after recording".

Known gotchas encoded in the source:
- Since an Oct 2025 CS2 update, `spec_player` on the same tick as `demo_gototick` can be ignored — CS:DM seeks to setup tick − 1 first (akiver/cs-demo-manager#1238).
- `spec_mode 1` must precede `spec_player` (camera can stick in free-cam on some demos).
- CS2 player names containing double quotes can't be replaced (no `mirv_exec` workaround like CS:GO).

## 3. Settings reference (defaults)

Recording system `CS` (plain `startmovie`) | `HLAE` (Windows). Default **CS**.
Recording output `video` | `images` | `images-and-video`. Default **video**. `images` keeps raw files only.
Encoder `FFmpeg` | `VirtualDub` (VirtualDub = Windows, HLAE path). Default **FFmpeg**.

| Setting | Default |
|---|---|
| framerate / width / height | 30 / 1280 / 720 |
| closeGameAfterRecording | true |
| concatenateSequences | false (needs FFmpeg; >1 sequence) |
| outputFileName (concat) | `''` → `output`; placeholders `{map} {checksum} {game} {date} {time} {encoder} {resolution} {framerate}` |
| showXRay / showAssists / showOnlyDeathNotices | true / true / true |
| deathNoticesDuration (s, HLAE) | 5 |
| playerVoicesEnabled / recordAudio | true / true |
| trueView (CS2 only, `cl_demo_predict`) | false |
| FFmpeg videoCodec / audioCodec | `libx264` / `libmp3lame` |
| FFmpeg CRF / audio bitrate | 23 / 256 kbps (CRF ignored when outputParameters is set) |
| FFmpeg container | `avi` (options avi, mp4, mkv, mov) |
| FFmpeg input/output parameters | empty |
| HLAE location | `~/.csdm/hlae/hlae.exe` (auto-installed from latest non-prerelease GitHub release) or custom path |
| HLAE config folder | optional custom `USRLOCALCSGO` folder |
| HLAE parameters | optional extra `hlae.exe` args |

App folder: `%USERPROFILE%\.csdm` (Windows/macOS: `~/.csdm`; Linux: `$XDG_CONFIG_HOME/csdm` or `~/.config/csdm`). Also holds `cfg/`, FFmpeg, VirtualDub.

## 4. Sequences

`Sequence` fields: `number` (final order), `startTick`, `endTick`, `showXRay`, `showAssists`, `showOnlyDeathNotices`, `deathNoticesDuration`, `playerVoicesEnabled`, `recordAudio`, `cfg` (multiline console commands), `playersOptions[]` (`steamId`, `playerName` rename*, `showKill`*, `highlightKill`*, `isVoiceEnabled`), `playerCameras[]` (`tick`, `playerSteamId`, `playerName`), `cameras[]` (custom saved cameras: `tick`, `id`, `name`, `color`). *= HLAE only.

- CS2 = 64 ticks/second. Sequences are recorded sorted by start tick.
- Custom cameras are saved per map/game with x,y,z,pitch,yaw (applied via `spec_goto`).
- GUI: "Generate players' sequences" dialog (player filter, round filter, keep existing sequences); CLI `--mode player`.

## 5. Output layout

HLAE (CS2), per sequence under the output folder:
```
<N>-sequence/
  video.<container>      # only when output=video + FFmpeg (HLAE pipes to FFmpeg; no TGA)
  take0000/
    00000.tga ...        # only for images / images-and-video (preset afxClassic)
    audio.wav
```
(CS:GO puts TGAs in `take0000/defaultNormal/`.) CS:DM then muxes: video `-c copy` + converted audio → `<N>-sequence.<container>` in the output folder. With plain CS recording, TGAs are `<N>-sequence%08d.tga` (CS2) and FFmpeg runs `-framerate F -i pattern [-i wav] -vcodec … -acodec … -b:a …K -pix_fmt yuv420p -crf …`.

CLI videos land in a sub-folder named after the video id inside the output folder.

## 6. CLI (`csdm video`)

```
csdm video <demoPath> <startTick> <endTick> [options]
csdm video <demoPath> --mode player --steamids <id1,id2> --event kills|deaths|rounds [options]
csdm video list|pause|resume          # shared queue with the GUI; Ctrl+C aborts
```
Options: `--framerate --width --height --[no-]close-game-after-recording --[no-]concatenate-sequences --output-file-name --encoder-software FFmpeg|VirtualDub --recording-system HLAE|CS --recording-output video|images|images-and-video --ffmpeg-executable-path --ffmpeg-crf --ffmpeg-audio-bitrate --ffmpeg-video-codec --ffmpeg-audio-codec --ffmpeg-video-container --ffmpeg-input-parameters --ffmpeg-output-parameters --[no-]show-x-ray --[no-]show-assists --[no-]show-only-death-notices --[no-]player-voices --[no-]record-audio --death-notices-duration <s> --[no-]true-view --cfg <string> --focus-player <steamId> --config-file <json> --output <folder> --verbose`.
Player mode: `--perspective player|enemy` (default player), `--rounds 1,2,3`, `--start-seconds-before 2`, `--end-seconds-after 2`.

`--config-file` JSON (`VideoCommandConfig`): `{ demoPath, recordingSystem, recordingOutput, encoderSoftware, framerate, width, height, closeGameAfterRecording, trueView, concatenateSequences, outputFileName, ffmpegSettings{…all 9 fields}, outputFolderPath, sequences[] }` — config values override flags; `sequences` override generated ones.

## 7. HLAE command cheat sheet (CS2 — AfxHookSource2)

Available `mirv_*` commands: `mirv_streams, mirv_campath, mirv_camio, mirv_deathmsg, mirv_replace_name, mirv_skip, mirv_input, mirv_fov, mirv_viewmodel, mirv_fix, mirv_cmd, mirv_exec, mirv_colors, mirv_glow, mirv_noflash, mirv_sky, mirv_panorama, mirv_endofmatch, mirv_reshade, mirv_listentities, mirv_cvar_unhide_all, mirv_cvar_unlock_sv_cheats, mirv_loadlibrary, mirv_script_load/exec, mirv_vscript_exec`.

**Recording (`mirv_streams`)**
- `record name <path>`, `record start`, `record end`, `record fps <n>` (set it! HLAE warns if forgotten), `record format tga|bmp`, `record startMovieWav 0|1`.
- `record screen enabled 0|1` + `record screen settings <preset>` — capture final screen image (what CS:DM uses).
- `record cam enabled 1` (camera motion export for mirv_camio / After Effects/Blender), `record campath enabled 1`.
- Streams: `mirv_streams add normal|depth|hudBlack|hudWhite|world|playersMatte|weaponsMatte|viewModelMatte|particlesBlack|particlesWhite <name>`; `edit <name> record 0|1 | settings <preset> | depthMode … | clear … | beforeCommands/afterCommands …`; `print`, `remove`.
- Presets: `afxClassic` (TGA images), `afxFfmpeg` (x264 crf22 mp4), `afxFfmpegYuv420p`, `afxFfmpegLosslessBest` (libx264rgb crf0), `afxFfmpegLosslessFast`, `afxFfmpegHuffyuv` (avi), `afxFfmpegProres`, `afxFfmpegProresAlpha`, `afxFfmpegRaw`, `afxSampler30` (motion-blur sampling). Custom: `mirv_streams settings add ffmpeg <name> "<ffmpeg args> {QUOTE}{AFX_STREAM_PATH}\\video.mp4{QUOTE}"`. Needs `ffmpeg/ffmpeg.ini` next to hlae.exe.

**Cameras (`mirv_campath`)**: `add` (keyframe at current time/view), `enabled 1` (needs ≥4 keyframes), `clear`, `remove <id>`, `print`, `load/save <file.xml>`, `select …`, `edit start|duration|angles|fov|rotate|anchor|interp …`, `draw enabled 1`.

**Death notices (`mirv_deathmsg`)**: `lifetime <s>`, `clear`, `filter add attackerMatch=… victimMatch=… attackerIsLocal=0|1 block=0|1`, `filter clear|print|edit|move|remove`, `localPlayer …`, `help players` (lists ids; `x<steamId64>` = xuid match).

**Other**: `mirv_replace_name byXuid add x<steamid64> "<name>"`, `mirv_fov <f>|default`, `mirv_skip` (seek via demo_gototick), `mirv_input camera` (free cam control).

## 8. Practical recipes

- **Highest quality with HLAE**: output `images-and-video` or `images` → TGA + WAV → encode yourself (e.g. `ffmpeg -framerate 60 -i take0000/%05d.tga -i take0000/audio.wav -c:v libx264 -crf 16 -preset slow -pix_fmt yuv420p out.mp4`). Costs lots of disk.
- **Fast / low-disk**: HLAE + FFmpeg + output `video` → frames piped directly to FFmpeg, no TGA files.
- **Per-sequence tweaks** go in the sequence `cfg` (e.g. `cl_draw_only_deathnotices 1`, `mirv_fov 90`, `r_drawviewmodel 0`, `mirv_campath load "C:\\paths\\a.xml"; mirv_campath enabled 1`).
- **Frag movie for one player**: `csdm video demo.dem --mode player --steamids 7656… --event kills --recording-system HLAE --framerate 60 --width 1920 --height 1080 --concatenate-sequences --output-file-name "{map}-{date}"`.
- **Troubleshooting**: HLAE error window → update HLAE (Settings > Video) or pin an older HLAE/CS2 plugin version after a CS2 patch; "raw files not found" → check the recording ran (Steam running, plugin loaded, `csdm.log`); missing audio → `recordAudio` on and `audio.wav` present; spec camera wrong → add player camera at a tick after the seek.

## 9. Config-file gotchas (verified in CS:DM source, `video-command.ts`)
- The CLI strips comments with `/(#.*)/g` then `//.*|/\*[\s\S]*?\*/` **before** JSON.parse, even inside strings. A `#` in a
  player name, cfg line or file name silently truncates the JSON → "Failed to read or parse config file".
- `sequences` from a config are used as-is: every field (`playersOptions`, `playerCameras`, `cameras`, booleans,
  `deathNoticesDuration`) must be present or recording commands get `undefined` / crash.
- Config values override CLI flags; `--output` is ignored if the config has `outputFolderPath`.
- CLI `csdm video` auto-installs HLAE/FFmpeg if missing, and the demo must already be analyzed (`csdm json` analyzes it).
- `outputParameters` replaces `-crf` and, in the HLAE video mux step, is appended after the output file (ffmpeg
  warns "trailing options") — prefer leaving it empty and using CRF.
- Width/height minimum 800x600.
- Windows installer adds the install folder (contains `csdm.cmd`) to PATH.
- `csdm json <demo> --output-folder <dir> [--minify]` exports the full match (`<match name>.json`) incl. kills
  (`tick`, `roundNumber`, killer/victim SteamIDs, weapon, headshot…), rounds (start/freezetimeEnd/end ticks),
  players, clutches. Team numbers: 2 = T, 3 = CT.

## 10. User profile and project setup
- User is on **Windows**, CS2. Wants **1440p (2560x1440) @ 60 fps** outputs. Goal: Claude programs and generates
  videos from demos with the user's help (user runs the renders).
- Skill: `.claude/skills/cs-demo-video/SKILL.md`. Tool: `tools/csdv/csdv.py` (summarize / build / validate).
  Windows scripts: `scripts/export-demo.ps1`, `scripts/render.ps1`. Data: `videos/` (profile, demos, specs, configs, logs).
- Chosen defaults (`videos/profile.json`): HLAE + FFmpeg direct pipe, mp4, libx264 CRF 18, aac 256k, concatenate on,
  X-ray off, voice comms off, kill-feed-only HUD, 3 s before / 2 s after kills.
- Not yet verified on the user's machine: `export-demo.ps1`, `render.ps1` and a first real render
  (`safety-check.ps1` verified 2026-09-27). Record results of the first run here.

## 11. Cameras, custom/workshop maps (research 2026-09-27)
- CS:DM custom cameras = `{x,y,z,pitch,yaw}` per `(game, mapName)` in its DB (a few defaults for official maps, e.g.
  de_dust2 "Tunnels"). Applied in videos via `spec_goto x y z pitch yaw` at a tick. Added via Settings > Cameras (map
  list comes from Settings > Maps, where custom maps can be added); "Start CS2 on <map>" launches `+map <name>` in a
  local spectator session (-insecure) and "capture" runs `getposcopy` + a screenshot. Config-file sequences can only
  reference cameras by DB id → we use HLAE campaths instead (no DB access needed).
- Maps support in CS:DM (radar image, posX/posY/scale/thresholdZ) is only for the 2D viewer/heatmaps.
- HLAE campath XML: `<campath positionInterp="default|linear|cubic" rotationInterp="default|sLinear|sCubic"
  fovInterp=… [offset=…] [hold]><points><p t x y z fov rx(roll) ry(pitch) rz(yaw) [qw qx qy qz]/>…`. ≥ 4 keyframes to
  enable. Times are client curtime seconds; `mirv_campath offset current[+s]` shifts the path to start now.
  `mirv_campath add` records the current view; `save/load <file>`.
- HLAE `mirv_cmd addAtTick <demoTick> <cmd…>` / `addAtTime` / `addCurves` / `clear` / `print` / `load|save` (XML):
  the tick variant uses the **demo file tick** (+ interpolation) → exact sync with CS:DM sequence ticks.
  csdv uses: at setup tick load path + `mirv_cmd addAtTick <start> mirv_campath offset current` and
  `addAtTick <end> mirv_campath enabled 0`. UNTESTED on a real render yet.
- `mirv_viewmodel enabled 0|1`, `mirv_viewmodel set x y z fov leftHanded` (* keeps); `mirv_noflash <0..1>`;
  `mirv_fov <f>|default|handleZoom…`.
- CS:DM `csdm json` export has kill positions (killerX/Y/Z, victimX/Y/Z) but **no per-tick player positions**
  (`analyzePositions` is off by default and positions aren't exported). CS:DM's analyzer binary
  (`@akiver/cs-demo-analyzer`, npm, v1.11.0; linux/windows/mac binaries) runs in this cloud container:
  `csda -demo-path x.dem -output out -format json -positions [-minify]` → `playerPositions` (x,y,z,yaw,tick…),
  grenade/inferno positions. Sampling rate not yet verified. Needs the .dem shared with the cloud (Drive connector).
- Map geometry isn't available to me; untested idea: Source2Viewer (ValveResourceFormat) CLI can decompile the
  workshop map VPK (`steamapps/workshop/content/730/<id>/`) to glTF for wall/collision checks.
- Workshop-map demos: playback needs the map installed locally (subscribed). How CS2 resolves workshop map names in
  demos is not verified yet — check on the first workshop demo (summary `map` field).

## 12. Versions / launch errors (2026-09-27)
- CS2 updates ~2026-09-23 (build 14182) and 1.41.8.5 (~09-26). HLAE fixed them in 2.192.3 → **2.192.6
  (2026-09-26, latest)**; 2.192.5 broke sniper scopes (fixed in .6). Older HLAE → errors on launch.
- CS:DM latest release **v3.20.1 (2026-07-30)**. CS2 plugin last updated for the 2026-07-09 CS2 update (plugin
  versions 14030…14168 + latest). No CS:DM commit for the September CS2 updates as of 2026-09-25 → if the plain
  (non-HLAE) launch also fails, the CS:DM plugin may be broken by the CS2 update (check CS:DM GitHub issues/Discord).
- CS:DM checks HLAE updates from GitHub releases (latest non-prerelease) and can update it in Settings > Video.
- Triage: exact error text → update HLAE → retry → try with "Use HLAE" off (Settings > Playback) → check `csdm.log`
  in `<CS2>/game/bin/win64/` and CS:DM logs.

## 13. UI map (from CS:DM v3.20.1 UI source, src/ui — verify with the user's screenshots)
RULE: never give the user a click path from memory. Check `src/ui` in the cloned repo (or ask for a screenshot) first.
(2026-09-27: I wrongly said Settings > Video has an HLAE update button — it doesn't.)
- Sidebar pages: Matches, Demos, Players, Teams, Search, Ban, Analyses, Downloads (Valve/FACEIT/Renown/5EPlay/Pending),
  Videos (the generation queue), pinned player.
- Match page tabs: Overview, Rounds, Players, Heatmap, Duels, Weapons, Grenades, 2D viewer, **Video**, Chat, Economy.
- **Match > Video**: recording system (HLAE / CS), output, encoder, resolution, fps, concatenate, output file name…
  When recording system = **HLAE** (Windows) a box appears with the HLAE version and **Install / Update / Browse**
  buttons (Update is disabled unless CS:DM detects a newer GitHub release), config folder, parameters.
  Also FFmpeg and VirtualDub boxes, match comment, and the sequences timeline.
- Settings tabs: UI, Folders, Tags, Maps, Download, Playback, Analyze, Video, Cameras, Ban, Integrations, About
  (+ Database).
  - Settings > Video: HLAE location (custom hlae.exe), HLAE config folder, HLAE parameters; FFmpeg location;
    default recording settings. **No install/update button here.**
  - Settings > Playback: resolution/display mode, launch parameters, "Use HLAE" for watching, CS2 plugin version…
  - Settings > Maps: add/edit maps (radar, posX/posY/scale, thresholdZ, thumbnail) — needed for 2D viewer.
  - Settings > Cameras: pick a map, add/edit cameras (x, y, z, pitch, yaw, color, preview), "Start CS2 on <map>".

## 14. User's machine (from screenshots)
- 2026-09-27 Settings > Video: HLAE custom location **C:\Program Files (x86)\HLAE\HLAE.exe** (HLAE installer),
  config folder off, no parameters. FFmpeg custom location **C:\Program Files (x86)\HLAE FFMPEG\ffmpeg\bin\ffmpeg.exe**.
  Default recording settings were still 1280x720, X-ray on, player voices on (CS:DM defaults; our csdv configs override).
- With a custom location, CS:DM's HLAE "Update" downloads into that same folder, and for HLAE+FFmpeg video output it
  writes `<hlaeDir>\ffmpeg\ffmpeg.ini`. Program Files needs admin rights → likely to fail. Recommended: keep HLAE in a
  user-writable folder (e.g. `C:\Users\<user>\HLAE\2.192.6`) and point Settings > Video > HLAE > Change at it.
- 2026-09-27: user downloaded the HLAE 2.192.6 zip to install manually; the browser blocked it as "Virus detected".
  I fetched the same GitHub asset in the cloud and checked it: `hlae_2_192_6.zip`, 8,989,218 bytes,
  SHA256 `b3acae70babb536e3b4a34fbbbe4ca8e55a1028068eaaf5fc98817775b72f4fa`; HLAE.exe version
  `2.192.6.0+a9683e59c02c42146af4b73aa520daaf21615d05` = the official v2.192.6 tag commit; normal layout (HLAE.exe,
  injector.exe x86/x64, x64/AfxHookSource2.dll, MS runtimes, changelogs). Antivirus flags are the usual
  injector/hook false positives. Fix used: folder exclusion + download via PowerShell + Get-FileHash compare.
- 2026-09-27 (local session): installed HLAE 2.192.6 at **C:\Users\Liam's PC\HLAE\2.192.6\HLAE.exe**. Downloaded
  with PowerShell `Invoke-WebRequest`. The hash matched GitHub's asset digest. No exclusion was added. The old 2.191.1
  (`Program Files (x86)\HLAE`, intact, nothing quarantined there) is kept as a fallback. Logged in
  `Desktop\Demos\findings.md`. The user set CS:DM's HLAE custom location to the new path (confirmed in
  `%USERPROFILE%\.csdm\settings.json`: `video.hlae.customExecutableLocation`; read it, never edit it).
- Defender (`Get-MpThreatDetection`, readable without admin; the quarantine list via `MpCmdRun -Restore -ListAll`
  needs admin): `Trojan:Win32/Sabsik.EN.A!ml` (ML false positive) quarantined the **root `AfxHookSource.dll`** (32-bit
  Source 1/CS:GO hook) about 12 minutes after extraction. **`x64\AfxHookSource2.dll` (CS2) was not touched.**
  Recheck this whenever HLAE fails to start.
- **2026-09-27 RESOLVED:** with HLAE 2.192.6 (user-profile install, above) a demo started from CS:DM booted fine
  (Use HLAE on). So the launch errors were the old HLAE 2.191.1 vs the Sept CS2 updates; CS:DM v3.20.1's plugin still
  works on CS2 1.41.8.5. `scripts\safety-check.ps1` (report mode) verified on the real PC: prints SAFE, exit 0.
  csdm.log is at `C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\csdm.log`.

## 15. Render log (append: date, demo, what worked / broke)
- **2026-09-27, mango1.dem (totemlake, workshop map), first real render: WORKED.** `render.ps1` → `csdm video
  --config-file` → HLAE 2.192.6 → FFmpeg pipe. 3 × 8 s clips came out 2560x1440, 60 fps, h264 + aac, about 20–25 MB each,
  and CS2/HLAE closed by themselves. Findings:
  - **Third person = `spec_mode 3`** (CS2 chase cam). `spec_mode 5` stays first person. Sent with
    `mirv_cmd addAtTick <tick> spec_mode 3` from the sequence cfg it switches exactly at that tick (a visible cut from
    first person). csdv has `"view": "third"`: the first player camera moves 32 ticks before startTick and spec_mode 3
    fires 2 ticks after each player camera. **Correction (user, same day): first person is the default.** By
    "third person" the user means cinematic shots from a separate camera (see §16), not the chase cam.
    UNVERIFIED: whether 2 ticks is enough after CS:DM's `spec_mode 1; spec_player` (render mango1-test with view third).
  - **CS:DM ignores `--output` with `--config-file`**: files were written next to the demo (the csgo folder). With
    `concatenateSequences` false, they're named `sequence-<N>-tick-<start>-to-<end>.mp4`, with no video-id sub-folder.
    `render.ps1` now writes `outputFolderPath` into the run config. Profile default: `C:\Users\Liam's PC\Videos\CS2 Renders`.
  - **Self-recorded demos (the "?" Source column in CS:DM) fail analysis with `UnknownSource`** → `export-demo.ps1 -Source valve`.
    The summary still says source "unknown". A workshop map shows under its plain map name (`totemlake`).
  - mango1 has 2 players, no rounds or kills (a free-roam session), and runs 7:29. `csda -positions` returned **no
    playerPositions**; it seems to sample only inside rounds. For demos like this, camera coordinates must be scouted in-game.
  - PC scripts: Python is the Microsoft Store 3.13 (a WindowsApps alias). Two `_common.ps1` bugs are fixed:
    WindowsApps was skipped, and a one-item `Get-Python` result was indexed as a string (`$py[0]` = "C"). Also
    `Invoke-Csdv` had `-o` swallowed as `-OutVariable`.

## 16. Cinematic cameras: the user's main goal (2026-09-27)
- The user wants **movie-style shots from a separate camera** for most footage, on official AND workshop maps:
  follow from behind, from the front (leading) and from the side (tracking), tripod pans, push-ins, arcs,
  establishing shots. Shots should copy real film grammar, not gimmicky orbits. First person stays the default for plain clips.
- **Player positions: `demoparser2` (Python, already installed, 0.41.3)** `DemoParser(dem).parse_ticks(["X","Y","Z",
  "pitch","yaw","velocity_X","velocity_Y","is_alive","duck_amount"])` gives every player on **every tick**, with or
  without rounds (tested: Brycetopher de_dust2, 6158 ticks in 0.5 s; mango1 totemlake). It works where `csda -positions`
  gives nothing (all of the user's demos so far are free-roam sessions with 0 rounds). Teleports/respawns show up as huge
  speed spikes, so cut there. On Windows set `PYTHONIOENCODING=utf-8` (player names contain emoji).
- **Map geometry:** Source 2 Viewer GUI 20.0 is installed at `Desktop\Demos\Source2Viewer.exe`. It has **no CLI**, and
  running it with `--help` opens the GUI. The CLI is a separate release asset (`cli-windows-x64.zip`, 52.7 MB). Official
  maps: `game\csgo\maps\<map>.vpk`. Workshop maps: `steamapps\workshop\content\730\<id>\*.vpk`.
- CS:DM's saved cameras (Settings > Cameras, e.g. 15+ dust2 presets) are good tripod/establishing positions for
  official maps. They live in CS:DM's PostgreSQL DB.
- **Built 2026-09-27** (`tools/csdv/positions.py`, `mapgeo.py`, `cine.py`; spec: clip `"camera": {"shot": "follow" |
  "lead" | "side" | "arc" | "crane" | "push" | "pull" | "tripod", "subject": "<player>", ...}`). Map geometry = collision
  mesh `maps/<map>/world_physics.vmdl_c` exported to `videos/maps/<map>/<map>_physics.glb` (gitignored). The raw glb
  vertex positions are already world units, z up (the node matrix only converts to glTF meters). Skip
  playerclip/npcclip/grenadeclip/sky; glass/chain-link block the camera but not the view. 0.4 s load, ~0.8 ms per ray.
  Top-down previews: `videos/previews/<spec>-clipN.png` (gitignored). CLI gotcha: pass Windows paths, since `-o /c/...`
  wrote to `C:\c\...`. (Note: `C:\c\Users\...\Content Creator Hub` already existed from something earlier; not ours.)
- **First cinematic render (dust2, Brycetopher, Caillou): WORKED.** Tripod pan, follow from behind and side tracking
  all look right. The subject is drawn (spectated in chase mode) and `cl_drawhud 0` removes the HUD. Findings:
  - The campath anchored on startTick left **frame 1 in the spectator view** → it's now anchored 8 ticks earlier
    (`CAMPATH_PREROLL_TICKS`); cinematic shots are built from startTick-8. Not re-rendered yet.
  - **Doors and dynamic props aren't in world_physics** (the dust2 double doors blocked the end of a follow shot).
    TODO: add entity models (func_door / prop_dynamic from the vmap's entity lump) to the collision set.
  - FOV defaults (follow 70, side 55, tripod auto by size) look natural at 16:9. Tripod "full" at ~450 u reads well.
  - The output folder fix works: files land in `Videos\CS2 Renders` as `sequence-N-tick-A-to-B.mp4`.
- **2026-09-27, props, doors and workshop maps:** collision now = world_physics + solid entities from the entity lumps
  (`maps/<map>/entities/*.vents_c`, decompiled to `key value` blocks): prop_*, func_door*, func_brush, func_wall,
  func_breakable, func_movelinear... Models are exported from the map VPK, the workshop addon, or pak01, placed with
  the Source AngleMatrix + `scales` (verified: the dust2 soccer ball lands centred on its origin). Dust2 has only 3 (its
  doors are static world geometry); totemlake has 148 (126 prop_dynamic, 3 prop_door_rotating), all exported.
  **Workshop VPK = addon** (`content/730/<id>/<id>[_dir].vpk`) with a nested `maps/<map>.vpk` + custom models;
  `-l -f maps/` lists nothing, so search the full listing. totemlake = workshop 3581521200. The combined collision is
  cached in `videos/maps/<map>/collision_full.npz` (delete it after changing the collision code).
- **Dust2 doorway occlusion wasn't a collision miss:** the camera followed through a doorway and the frame filled the
  picture's edges. Fix: a "thick" collision arm (extra lines beside/above the lens, half-width 16 + 0.15 × distance).
- **Lead / push / arc render (dust2-cine-test2): lead and arc good.** Push v1 read as pull-out then push-in, because
  the wall pull-in at the start broke the distance ramp. Fixed: moving-distance shots shrink the whole move to fit,
  stay one-directional, and use the subject's overall travel direction, not the first-frame heading. Re-rendered:
  good push-in (wide to medium). The 8-tick campath pre-roll fixed the first-frame spectator view.
- push/pull/arc/crane default to `"angle": "auto"` (the candidate with the least wall pull-in wins).
- **User preference (2026-09-27):** mostly shots that are NOT glued to the character: overhead, locked-off
  statics, sweeping drone shots, low ground-level lock-offs with the runner leaving frame. Follow/lead/side are
  the minority. Built "placed" shots in cine.py: `static` (auto spot + lens that frames the whole action; with a
  subject/POV; without one, the old coordinate `static` is used), `overhead` (pitch 89, travel = up the frame,
  fit-to-action or `"track": true`, ceiling-aware), `drone` (`move`: flyover | orbit | rise; own path at altitude,
  look target lagged ~1 s), `ground` (lens 6 u above the floor, locked off, `facing` away|toward; picks the point on
  the route with the most time in view + in frame).
- **Render dust2-cine-test3: all four worked.** Overhead track (~630 u up, player small, wires cross the frame),
  ground (runner enters mid-frame and runs down the street; the first ~1.5 s show an empty street because the
  camera sits 20–30% along the route), static (high wide lock-off, ~350 u up, FOV 52), drone flyover (high pass
  over the rooftops, ends looking down on the runner at B).
- **Handheld (2026-09-27):** `"handheld": 0.5 | 1 | 2` on any shot (placed ones too). Model: filtered-noise drift
  (~0.4–0.5 s) + jitter (~0.07 s) on pitch/yaw/roll, a small positional wander, and a footstep bob (1.9 Hz) + sway
  while the camera travels. Handheld shots use keys every 2 ticks. Render dust2-cine-handheld: static 0.6, tripod
  pan 1, lead 1.5, follow 1, all framed fine. **Verification method:** phase-correlate each frame against frame 0
  (`scratchpad/shake.py` idea: ffmpeg → 320x180 gray raw → FFT); consecutive-frame shifts round to 0. The static
  handheld drifted 32×48 px in 3 s at 1440p vs 0 px for the locked-off static.
- **Shot suggestions (2026-09-27):** the user wants horror/thriller techniques used selectively, and wants me to
  suggest shots automatically from scene context. Research → `videos/HORROR-CAMERA.md`. `tools/csdv/scene.py`
  (analyze → 0.5 s windows, segment → 2–8 s beats, suggest → ranked recipes test-built with cine) + `csdv suggest`
  CLI. New shots: `dolly_zoom` (auto angle keeps the longest move), `stalker` (hidden telephoto watcher tucked
  against a wall; uses the real watcher's position for "watched" beats), `ots` (`over`). Horror weight =
  clip((tension-0.8)/1.6, 0, 1.3); repeats ×0.55 per use. On Brycetopher: 22 beats, ~6 horror picks, all at tense
  moments. Whole-demo analysis takes ~2 min (mostly test-building candidates).
  Brycetopher context: umbass is noclipping (flies); the players are close around 0:21–0:26 and 1:10–1:24.
- **User rule (2026-09-27): don't make every shot crazy.** Mostly plain, static-feeling shots so the special ones
  keep their effect. Implemented as pacing classes anchor/move/special in scene.py (`category`, `pacing`): open on
  an anchor, no two specials in a row, a breather after a special, specials <= 25%, anchors >= 50%, max two moves in
  a row. New anchors: "Static medium", "Eye-level lock-off (arrival)".
- **Backrooms mood** (the user's project is Backrooms-style): research in `videos/HORROR-CAMERA.md` (found-footage
  camcorder handheld with sweeping pans, wide lenses, restrained camera, empty liminal frames, withholding, VHS in
  post). Recipes: camcorder `pov` (new shot: eye position + view angles with inertia, FOV 95, handheld 0.7; csdv
  keeps first-person spectating for pov so the body is hidden), camcorder follow, liminal lock-off, empty-room wide,
  dropped camera (special). No drones in this mood. Not built yet: a VHS post-process pass.
- **Workshop map test (totemlake, mango1 2:55–3:40, backrooms mood, 14 beats, 2026-09-27): all shots worked.**
  Context detection works indoors on a workshop map (indoor, watched, face-off). The camcorder `pov` works in game
  (first person, no body/gun, the other player visible down the hall). Stalker vision placed at the real watcher's
  spot, OTS over David. Totemlake = grey concrete liminal halls, a natural fit for the Backrooms look.
- **CS:DM bug: `concatenateSequences` fails for any path with an apostrophe.** CS:DM writes `~/.csdm/videos.txt` as
  `file 'C:\Users\Liam's PC\...'` without escaping → FFmpeg "Impossible to open 'C:\Users\Liams'"; csdm still
  exits 0 and only prints "FFmpeg error". Fix in render.ps1: turn concatenation off in the run config, then
  `Join-Sequences` (`_common.ps1`) joins in number order with `-c copy`, escaping `'` as `'\''`, writes
  `<Folder>\<name>.mp4` and moves the shots to `<name>-shots\`. render.ps1 now fails if the log says "FFmpeg error".
  Contiguous beats (next start = previous end) rendered fine.
- **OTS fix (2026-09-27, user: "the back of the character's head is basically invisible"):** a lens chosen for the
  far subject (~13° half-width) put the over-player's head (~20° off-axis) outside the frame. Now: back 70,
  offset 16, height 8; the aim sits between head and subject (headWeight 0.45); the lens is fitted after aiming so
  both land within 70% of the half-width, with a max-filter so it never crops. Totemlake beat 7: head in frame
  99% (was mostly out), subject 100%. Not re-rendered yet.
- **Map semantics (what I can read):** (1) collision geometry, (2) entities with class + model names
  (prop_dynamic/doors/lights/spawns/buttons), (3) static detail in world-node meshes named by content, e.g.
  `n0_lr0_agg_merge_exit_ceiling_0`. Exporting a single agg mesh gives meaningless positions (instances); export
  `maps/<map>/world.vwrld_c` to glb (138 MB for totemlake, ~3 s, `videos/maps/<map>/render/`), apply node matrices,
  and convert glTF meters/y-up to Source: x=x/0.0254, y=-z/0.0254, z=y/0.0254. Totemlake: one ceiling exit sign
  at (1426, 1266, 902), size 5×22×13. Colour isn't in the names (verify with a test frame, or read materials).
- **Gap scene (user request, 2026-09-27) rendered: `totemlake-gap-scene.mp4`, 15 shots.** "The CT leaves the T and
  stands still in the corner; that spot must never be in view, even when he isn't there." CT = Caillou (team 3),
  T = David. Corner N1 = (-1528, 3351, 773), CT still 4:55–5:03 (walls 43–136 u on most sides, open to the NW).
  `csdv suggest ... --start 4:28 --end 5:04 --mood backrooms --hide N1 --while-hidden David`. Independent check
  (campath XML × zone points × collision): N1 on screen in 0 frames in all 15 shots.
- **Exit signs on totemlake = green light entities** (6 light_omni2 with colour [0,255,0], each at a sign). The
  mesh named `exit_ceiling` is a different ceiling fixture; my first zoom from (1726,1266,837) rendered pure black
  (the camera was inside render-only geometry the collision mesh lacks). A LESSON: prefer camera spots already
  proven by a render, or check with a test frame. The glowing EXIT above the doorway = (-1455, 2966, 905); the
  zoom from (-1141, 2995, 829), FOV 75→10 over 5 s, worked (`totemlake-exit-sign`). Located by casting a ray
  from a rendered frame's camera through the sign's pixel — a reusable trick for "that thing in the shot".
- **Map overview** (`tools/csdv/mapview.py`, output in videos/maps/<map>/overview/): levels the players use,
  spaces (room/corridor/nook) with IDs, borders, grid, doors, exit signs (green lights), props, routes with time
  marks, still spots, hidden zones. Known limit: wide-open halls stay one big space (C13) → use grid coordinates.
- **USER STYLE PREFERENCE (2026-09-27, strong): still or slightly zooming shots; avoid cameras pinned to /
  moving with the character (follow, lead, side, camcorder POV/follow, drone, arc, and even tripod pans). Special
  shots only for intense moments.** Implemented in scene.py: MOVE_WEIGHT 0.35, max 1 move in a row, ANCHOR_SHARE
  0.7, SPECIAL_SHARE 0.15, specials need beat tension >= 2.0, beats >= 3 s; new "slow zoom" recipes (static,
  `"zoom": [1.0, 0.8]` = the lens creeps in, the camera stays still); tripod pans down-weighted.
- **Hidden place = the whole pocket, not the spot** (user: "we still saw inside the gap"). The 40 u circle missed
  most of the pocket (walls E/S up to 104–126 u, open NW). `cine.nook_zone(geometry, spot)` measures it: 72 rays at
  chest height, depth = the farthest nearby wall (166 u here), open directions cut at that depth (the mouth line),
  up to the ceiling → a polygon; `hide_points` = 16 u grid × 4 heights + inner-wall points. Placement for locked-off
  shots checks the exact frame (`view_shows_zone`), not "can see any of it", otherwise open halls allow no camera.
  LOS results are cached per camera spot. Re-check of the old render: the pocket was on screen in 8 of 15 shots.
  New plan (4:28–5:12, CT reappears): 10/12 still shots, 2 specials; dense independent check (3636 pts): 0 frames.
- Verification gotcha: campath XML parsed with a regex of x|y|z|fov|rx|ry|rz has no `t` column — parse all
  attributes into dicts when time matters.
- **Look/quality tests (2026-09-27):**
  - **Supersampling via a larger window does NOT work:** 3840x2160 in the CS:DM config rendered 2560x1440 (the game
    clamps the window to the 1440p monitor). The only route is NVIDIA DSR/DLDSR (a driver/display change → ask first).
  - **Motion blur WORKS** (`"motionBlur": {"inputFps": 240, "shutter": 0.5}` on a clip or spec). CS:DM's CS2+HLAE
    path (read from app.asar): `mirv_streams record screen settings csdmPreset<N>` + `mirv_streams record fps <fps>`,
    then the sequence cfg. So: add sampler csdvBlur<N> → settings csdmPreset<N>, fps 60, exposure 0.5 (180°),
    strength 1 → `record screen settings csdvBlur<N>` → `record fps 240` (HLAE sets host_framerate from it; my
    first try with host_framerate + afxDefault did nothing). Sampler options from HLAE source
    shared/RecordingSettings.cpp: settings, fps, method rectangle|trapezoid, exposure 0..1, strength 0..1.
    Verified: limbs visibly smeared; sharpness moving/static drops. ~4x render time at 240.
  - `con_logfile` in a sequence cfg writes nothing (CS:DM runs cfg through its server plugin) → read CS:DM's code instead.
  - Post looks: `tools/csdv/post.py <clip> --look downscale|cinematic|camcorder [--letterbox] [--stamp ...]`
    (FFmpeg; cinematic = gentle contrast/warmth, vignette, grain, 2.39:1 bars; camcorder = 4:3 640x480 look,
    wobble, fringing, noise, scanlines, PLAY ▶ + date + counter; Consolas lacks ▶ → Segoe UI Symbol).
  - ReShade_advancedfx (DOF / AO / grading with real depth): not installed yet; loads via HLAE's custom loader as a
    second DLL (never in the game folder); needs MSAA + FSR off; CS:DM launch integration unverified.
- **ReShade WORKS (2026-09-27).** Setup in `%USERPROFILE%\Tools\ReShade\`: ReShade 6.8.0 add-on DLL (extracted
  from the setup archive, never installed) named `dxgi.dll` → its config/log/presets stay in that folder; cache in
  C:\Temp\ReShade. HLAE add-on ReShade_advancedfx 1.4.1, shaders reshade-shaders (slim) + qUINT. CS:DM
  Settings > Video > HLAE parameters = `-hookDllPath "C:\Users\Liam's PC\Tools\ReShade\dxgi.dll"` (HLAE accepts
  -hookDllPath repeatedly and injects in order: hlae/Program.cs; CS:DM appends this string raw). Recording-only
  cs2_video.txt (~/.csdm/cfg/cfg): MSAA off, CMAA2 on (backup `.before-reshade-2026-09-27`). Log confirms: loads
  from Tools, add-on registers, shaders compile. DisplayDepth test: normals/depth correct → depth works.
  **Opt-in per render:** spec `"reshade": "look"` → csdv writes `videos/configs/<name>.reshade`; render.ps1
  `Set-ReShadePreset` switches PresetPath to `csdv-<name>.ini` and back to `csdv-off.ini` afterwards (default off,
  so watching demos and other renders are unaffected). Preset copies are in `videos/reshade/`.
  Look preset = MXAO (ambient occlusion: wall/ceiling/floor creases darken, the flat room reads 3D), ADOF
  (autofocus at the frame centre: subtle unless near subject + far background), Lightroom (mild).
  Gotchas: `sed 's/...\csdv/'` mangles `\c` → edit ReShade.ini with Python/regex. PowerShell
  ChangeExtension($f, $null) passes "" (not null). "Wider" look in comparisons was AO revealing room edges — the
  framing was pixel-identical.
