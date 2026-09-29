## ▶ CURRENT (2026-09-29, before the user's PC restart)
- A1 v4 rendered (`A1-v4-final*.mp4`) and reviewed by the user. Their shot notes: `videos/briefs/A1-direction.md`.
  Maps: `videos/briefs/A1-map-1-trapped.png`, `A1-map-2-run.png`. I'm now Cameraman + Creative Director (no Drive
  loop). Usage rules: memory `usage-budget`.
- **Waiting on the user's B-roll demos** (solo, recorded as demos): (1) spawn corner, backing into it facing N while
  acting out the prank/fly/stupid command/relief lines; (2) the #31 hiding corner (map 2, about -5186,2415), back to
  the wall, talking to himself, then bolting S/E toward #33/#34; (3) the "246, 247" pit opening.
- **Agreed plan (v5):**
  - #1 from C's right side at 0:25.9, held.
  - #2 behind C in the spawn corner, panning north toward LL's spot.
  - #3 over the shoulder, not POV.
  - #4–#6 keep.
  - Reveal: one camera behind LL, 1:16–1:23 (he steps in) + 2:22–2:33 (C enters and notices); tighter, smiley
    (-2636,1076) out of frame.
  - Back to the #15 angle sooner; #16 ends before the wall glitch; #19 scrapped.
  - The run 4:00–4:27 right→left as one continuous movement, plus a behind-LL's-head shot (LL at T14 -3472,3921,
    3:26–4:25) where C never looks his way.
  - The hiding corner (B-roll), a footstep, a whip east (empty, around 4:26), a whip back: C gone, running toward
    #33/#34 (demo 5:41–5:52 or the B-roll).
  - Old #20–#22, the charge, and #32 are dropped.
- Don't start rebuilding until the user says so ("let me record them first before doing anything").

# Handoff: CS Demo Manager + HLAE project → local Claude session on the user's PC

Written 2026-09-27 by the cloud session "CS DEMO MANAGER and HLAE skill". Everything below is on branch
**`claude/serene-volta-273jwq`** of `github.com/tobinlia000/CS2-Market-Pokemon-Card-Market-Data`.

---

## ⛔ 0. Safety — read first, obey always
HLAE is a cheat as far as VAC is concerned. A VAC ban is permanent. The full binding rules are in `CLAUDE.md`
(SAFETY section). Summary:
- **Never** let a game started with HLAE or CS Demo Manager's plugin reach a real game. Never write, run, suggest or
  schedule `connect`, `retry`, `redirect`, `map`/`changelevel`/`map_workshop`/`host_workshop_*`, `playdemo`,
  `sv_lan`, `rcon*`, `exec`, `bind`, `mirv_loadlibrary`, `mirv_script*`, or `steam://` links.
- Never remove `-insecure`. Never launch CS2 with HLAE yourself; only CS Demo Manager starts the game.
- Never disable Windows Defender. Security changes such as a folder exclusion need the user's explicit "yes".
- Before the user plays online: close CS2, run `scripts\safety-check.ps1`, then start CS2 from Steam.

## 1. Who and what
- User: Liam (Windows PC, CS2). Goal: Claude programs and generates CS2 demo videos with the user's help, using
  **CS Demo Manager (CS:DM) + HLAE**. The main use is **stories and camerawork**, not just highlight reels, mostly on
  **workshop maps**.
- Output target: **2560x1440 @ 60 fps** (`videos/profile.json`: HLAE → FFmpeg pipe, mp4, x264 CRF 18, aac 256k).
- The user prefers action over explanations of limits. You run locally, so do the PC-side work yourself (with
  permission prompts) instead of handing them instructions.

## 2. Why this handoff exists
The previous session ran in Anthropic's cloud (Linux) and could not touch the PC. You run on the PC, so you can
install and verify HLAE, inspect Defender, run the PowerShell scripts, and read CS2/CS:DM logs.

## 3. Immediate task: fix "CS2 won't start from CS:DM"
Facts so far:
- CS2 updated ~2026-09-23 (build 14182) and ~09-26 (1.41.8.5). **HLAE 2.192.6 (2026-09-26)** supports it. CS:DM's
  latest release is **v3.20.1 (2026-07-30)**; its CS2 plugin has no changes for the September CS2 updates, so it
  may also be affected.
- CS:DM **Settings > Video** (from the user's screenshot): HLAE custom location **`C:\Program Files (x86)\HLAE\HLAE.exe`**,
  config folder off, parameters empty. FFmpeg custom location
  **`C:\Program Files (x86)\HLAE FFMPEG\ffmpeg\bin\ffmpeg.exe`**. Defaults are still 1280x720, X-ray on, voices on
  (our configs override these).
- Program Files isn't user-writable. CS:DM's HLAE "Update" writes into the custom folder, and it writes
  `<hlaeDir>\ffmpeg\ffmpeg.ini` when recording with HLAE. So install HLAE under the user profile instead.
- The browser blocked `hlae_2_192_6.zip` as "Virus detected". This is the usual false positive for `injector.exe`
  and the hook DLLs. The cloud session downloaded the same official asset and verified it:
  - size **8,989,218** bytes
  - SHA256 **`B3ACAE70BABB536E3B4A34FBBBE4CA8E55A1028068EAAF5FC98817775B72F4FA`**
  - HLAE.exe version `2.192.6.0+a9683e59c02c…`, which is the official v2.192.6 tag commit
  - normal layout: HLAE.exe, injector.exe (x86 and x64), x64\AfxHookSource2.dll, MS runtimes, changelogs

Steps:
1. **Defender history (read-only):** check `Get-MpThreatDetection` and `Get-MpThreat` for HLAE, injector or
   AfxHook entries. Also check whether anything was quarantined from `C:\Program Files (x86)\HLAE`; that alone
   could explain the launch errors. Report what you find.
2. Ask the user whether to add a Defender **folder exclusion for `%USERPROFILE%\HLAE` only**. Don't add it without
   their yes. It needs admin rights, and they may prefer to click it themselves.
3. Download
   `https://github.com/advancedfx/advancedfx/releases/download/v2.192.6/hlae_2_192_6.zip` to `%USERPROFILE%\HLAE\`.
   Run `Get-FileHash`. Continue only if it matches the SHA256 above.
4. Extract to `%USERPROFILE%\HLAE\2.192.6`. Leave the old Program Files install alone as a fallback.
5. The user sets **CS:DM Settings > Video > HLAE > Change** to the new `HLAE.exe`. Don't edit CS:DM's settings
   files. Verify on a match's **Video** tab with Recording system = HLAE: the HLAE box shows the version and has
   Install/Update/Browse buttons.
6. **Test:** the user starts a demo from CS:DM.
   - If it fails, capture the exact error text. Collect `<CS2>\game\bin\win64\csdm.log` and the CS:DM logs.
   - Then retry once with **Settings > Playback > "Use HLAE"** off. If it fails there too, the CS:DM plugin is the
     problem.
7. Run `scripts\safety-check.ps1` at the end.
8. Write the results into `.claude/memory/cs-demo-manager-hlae.md` sections 14–15, then commit and push.

## 4. Repo map
| Path | What |
|---|---|
| `CLAUDE.md` | Safety rules (top priority), pointers |
| `.claude/memory/cs-demo-manager-hlae.md` | **Working memory.** How CS:DM drives HLAE, settings, CLI, `mirv_*` commands, safety mechanisms, cameras, versions, UI map, the user's machine, render log. Read before any CS:DM/HLAE work; update it and its "Last updated" line |
| `.claude/skills/cs-demo-video/SKILL.md` | The workflow skill: export → spec → build → render, spec format, camera shots, troubleshooting |
| `tools/csdv/csdv.py` | `summarize` a `csdm json` export; `build` a clip spec into a `csdm video --config-file` config; `validate` it (includes the safety blocklist). Tests: `python -m unittest discover -s tools/csdv` (14 passing) |
| `tools/csdv/campath.py` | Generates HLAE campath XML: static, dolly, orbit, keys, track; `look_at`; `parse_getpos` |
| `scripts/export-demo.ps1` | `csdm json` export → `videos/demos/<name>.summary.json` |
| `scripts/render.ps1` | Validates a config, substitutes `{REPO}`, runs `csdm video --config-file`, logs to `videos/logs/`. Refuses to run if CS2 is already open |
| `scripts/safety-check.ps1` | Pre-online-play check: no cs2/hlae process, no CS:DM plugin leftovers. `-Fix` removes them |
| `videos/` | `profile.json` (1440p60 defaults), `specs/`, `configs/`, `demos/`, `campaths/` (+ `scout/`), `README.md` |

## 5. Key technical facts (details in memory)
- CS:DM writes `<demo>.json` (tick → command actions), installs its CS2 server plugin
  (`game\csgo\csdm\`, adding a `Game csgo/csdm` line to `gameinfo.gi`), and launches `hlae.exe -customLoader …
  -cmdLine "-insecure -novid +playdemo …"`. HLAE records with `mirv_streams`, and FFmpeg encodes.
- **Config-file gotchas:** CS:DM strips `#`, `//` and `/*` even inside strings, and does not default any sequence
  fields. csdv handles both.
- **Cameras:** world coordinates work on any map, including workshop maps. csdv loads a campath at the sequence
  setup tick and syncs it with `mirv_cmd addAtTick <startTick> mirv_campath offset current`. Coordinates come
  from `getpos` or `mirv_campath add/save` while watching the demo, kill positions in the summary, or per-tick
  positions via CS:DM's analyzer `csda -positions` (npm `@akiver/cs-demo-analyzer`).
- **UI:** HLAE install/update buttons are on **Match > Video** (Recording system = HLAE), not in Settings.
  **Always check CS:DM's UI source (github.com/akiver/cs-demo-manager `src/ui`) or ask for a screenshot before
  giving click paths.**

## 6. Not yet verified on the real PC
- The PowerShell scripts: `export-demo`, `render`, `safety-check`. No PowerShell was available in the cloud.
- A full render, including 1440p windowed recording.
- Campath sync via `mirv_cmd`, and how the campath interacts with the spectator view.
- Workshop-map demo playback (the map must be installed or subscribed) and the map name in the summary.
- The sampling rate of `csda` positions.

## 7. After the launch works (roadmap)
1. **First real test:** export a short demo, then a 10-second, 1440p60 clip. Record the result in the memory
   render log.
2. **Workshop-map camerawork:** scout coordinates in the demo, then test one static shot and one orbit shot.
3. **Story workflow:** shot list → spec with `order: "spec"` → cameras per scene.
4. Optional: generate campaths that follow players using `csda` positions.

---

### Paste this as the first message of the new local session
> Clone https://github.com/tobinlia000/CS2-Market-Pokemon-Card-Market-Data into this folder if it isn't already,
> check out branch `claude/serene-volta-273jwq`, then read `HANDOFF.md`, `CLAUDE.md`,
> `.claude/memory/cs-demo-manager-hlae.md` and `.claude/skills/cs-demo-video/SKILL.md`. Follow the safety rules at
> all times. Then start on HANDOFF.md section 3 (fix the CS2 launch by installing HLAE 2.192.6), step by step.
