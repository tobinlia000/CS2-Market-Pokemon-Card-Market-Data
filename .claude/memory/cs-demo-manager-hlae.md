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
  csdm.log is at `C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\gamein\win64\csdm.log`.

## 15. Render log (append: date, demo, what worked / broke)
- (none yet)
