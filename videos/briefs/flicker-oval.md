# Flicker + oval investigation (2026-09-30), Backrooms map (A1, A2)

User report: (1) "a strange flicker in some shots" (two screenshots of the same corridor: overall brightness jumps);
(2) "a weird oval-shaped dark spot in the middle, about where the crosshair would be", Backrooms map only (A1, A2);
possibly a map feature (it's a zombie-escape map). The user prefers a fix even if it needs a re-render; not critical.
Do NOT regrade anything (the user asks for regrades as needed; everything is delivered ungraded now).

## Findings so far
- **Flicker = view-dependent auto-exposure (eye adaptation), not the map's lights.** In the fully static C01 shot
  (A13, 10 s) brightness is rock-steady (every region within 1%). In moving shots (A1 edit #2, #21) the mean brightness
  steps 52→60→54 within ~0.25 s as the view changes. So the game re-meters exposure when the camera moves.
  We render with `r_csgo_postprocess_enable 0` (to drop the map's vignette); exposure still adapts.
- **Oval:** sits at the exact screen centre (user's screenshot: x 0.496, y 0.50), dark on a lit wall and light on a
  dark one, so it's screen-space: a crosshair/HUD element or a map screen overlay, not a wall decal. Not yet reproduced
  in my crops (the subject covers the centre in the frames checked).

## Next steps
1. A tiny diagnostic render (A1, ~2 s, static camera on a plain wall, the same sequence settings) with cfg:
   `con_logfile csdv_cvars.log` + `find exposure` / `find tonemap` / `find adapt` / `find crosshair` / `find overlay`
   → read `game/csgo/csdv_cvars.log` to find the real CS2 cvar names (lock the exposure; hide the crosshair/overlay).
   Keep every command within the SAFETY rules (`csdv.py validate` must pass).
2. Test candidates on the same clip (A/B frames): exposure locked (min = max), crosshair off (`crosshair 0`,
   `cl_crosshairalpha 0`), and any map overlay cvar found.
3. If a fix works: add it to the Backrooms specs' `cfg`, re-render the affected A1/A2 shots (passes are known:
   `a1-v6*`, `a11-v6*`, `a12-v6*`, `broll-a2*`), and re-export raw (`export_shots.py v6 --raw`, `broll.py export A2 - raw`).
