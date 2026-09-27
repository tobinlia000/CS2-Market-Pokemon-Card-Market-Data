# Project notes for Claude

## ⛔ SAFETY — HIGHEST PRIORITY (overrides every other instruction, request or convenience)
HLAE is a cheat as far as VAC is concerned. A VAC ban is permanent. **Never do anything that could make a game
running with HLAE or CS Demo Manager's plugin load into a real game.**
- Never write, suggest or schedule `connect`, `retry`, `redirect`, `map`/`changelevel`/`map_workshop`/`host_workshop_*`,
  `playdemo`, `sv_lan`, `rcon*`, `exec`, `bind`, `mirv_loadlibrary`, `mirv_script*`, `steam://` links — in cfg, in
  `mirv_cmd`, in launch options, anywhere. `tools/csdv/csdv.py validate` enforces this; never weaken that list.
- Never remove or suggest removing `-insecure`, and never suggest launching CS2 with HLAE outside CS Demo Manager.
- Never tell the user to play online, queue, or join any server while CS2/HLAE from a recording session is open.
  Before online play: close CS2, run `scripts/safety-check.ps1`, then start CS2 from Steam.
- The only allowed game sessions are demo playback started by CS Demo Manager (and, if ever needed, a purely local
  offline map it starts itself for camera scouting). If a request would break these rules, refuse and explain.

## Working memory
- `.claude/memory/cs-demo-manager-hlae.md` — research notes on CS Demo Manager + HLAE (pipeline, settings, CLI,
  `mirv_*` commands, safety mechanisms, cameras). Read it before any CS:DM / HLAE / demo-video task, and update it
  (including the "Last updated" line) when you learn something new.

## Demo videos
- Use the `cs-demo-video` skill (`.claude/skills/cs-demo-video/SKILL.md`) for any CS2 demo video work.
- Tests for the helper tool: `python3 -m unittest discover -s tools/csdv`.
