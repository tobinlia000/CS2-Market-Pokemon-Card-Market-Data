# Demo videos (CS Demo Manager + HLAE)

Claude plans and programs the videos; you render them on Windows.

| Folder | What | Who writes it |
|---|---|---|
| `demos/` | `<name>.summary.json` — compact demo data (players, rounds, kills, multi-kills) | you, via `scripts/export-demo.ps1` |
| `specs/` | `<name>.json` — which clips to record (human-readable) | Claude |
| `configs/` | `<name>.csdm.json` — ready-to-run `csdm video --config-file` configs | Claude, via `tools/csdv/csdv.py build` |
| `campaths/` | HLAE camera paths: generated ones + `scout/` paths you record in-game | Claude / you |
| `logs/` | render logs (commit them when something fails) | `scripts/render.ps1` |
| `profile.json` | default render settings: HLAE, FFmpeg, 2560x1440 @ 60 fps, x264 CRF 18, mp4/aac | edit to change defaults |

## One-time setup (Windows)
1. Install CS Demo Manager (cs-demo-manager.com/download). Open it once; in Settings > Video let it install HLAE and FFmpeg.
2. Install Python 3: `winget install Python.Python.3.12`, then open a new terminal.
3. Clone this repo, and allow local scripts once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Loop
1. `.\scripts\export-demo.ps1 -Demo "C:\path\match.dem"` → commit + push the summary.
2. Tell Claude what you want ("all of X's 3k+ rounds", "round 14 from Y's POV", ...). Claude writes a spec + config and pushes.
3. `git pull; .\scripts\render.ps1 -Config videos\configs\<name>.csdm.json` (Steam running, hands off the game window).
4. Tell Claude what to change; repeat.
5. **Before playing CS2 online:** close CS2, run `.\scripts\safety-check.ps1` (add `-Fix` if it reports problems),
   then start CS2 from Steam. Never play online from a game started by CS Demo Manager or HLAE.
