# Working memory: CS Demo Manager + HLAE

Research notes for building a CS Demo Manager (CS:DM) + HLAE video skill. Facts come from reading the
source code, not only the docs, so they are precise but tied to the versions below. Re-verify after upgrades.

- **CS Demo Manager** v3.20.1 — github.com/akiver/cs-demo-manager (commit 10fc2a9, 2026-09-25). Docs: cs-demo-manager.com/docs (guides/video, cli).
- **HLAE** (Half-Life Advanced Effects, "advancedfx") — github.com/advancedfx/advancedfx (commit 96e13a0, 2026-09-27). Manual: github.com/advancedfx/advancedfx/wiki.
- Last updated: 2026-09-27.

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
- Not yet verified on the user's machine: the PowerShell scripts (no PowerShell in the cloud container) and a first
  real render. Record results of the first run here.

## 11. Render log (append: date, demo, what worked / broke)
- (none yet)
