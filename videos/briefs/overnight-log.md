# Overnight run log (2026-09-29/30): Part 1 B-roll for A2, C1, D1, G1

**If this conversation was compacted, resume here.** The plan and the user's answers are in `overnight-plan.md`. The user
is asleep: make best guesses, note every guess under "Guesses / flags" for the morning report, and never ask.

Pipeline (per demo):
1. `python videos/specs/a1v2/broll.py build <DEMO>` → `videos/specs/broll-<demo>-pN.json` (render passes) +
   `videos/specs/broll-<demo>-order.json` + `videos/specs/a1v2/report_broll_<demo>.md`
2. `csdv.py build` + `validate` each pass config.
3. `scripts/render.ps1 -Config <all pass configs>` (CS2 must be closed; run in the background), then `safety-check.ps1`.
4. `python videos/specs/a1v2/broll.py export <DEMO>` → graded, no-bar, 8-bit files in
   `Videos\CS2 Renders\Part 1 B-roll\<DEMO>\` + `index.md` + `contact-sheet.jpg`.
5. Review the contact sheet; fix or drop bad shots (note them here).

## Progress
- [x] A2 built (36 shots, 3 passes) + rendered; exporting
- [x] C1 built (55 shots, 6 passes); rendering
- [ ] D1  - [ ] G1  - [ ] review contact sheets  - [ ] morning report

## Guesses / flags
- G1: chickens can't be read from the demo data (players only); shots don't account for them.
- G1: the user doubts the 3:51.8 fall is the real one; searched for another backing-away fall (see G1 notes).
- A2 #14 (the jump, 4:31.5–4:40): no camera passed the checks (the fall trips the noclip detector); retry after the main renders.
- A2 #10 ends at 3:32.8: LL steps in right next to C at 3:33 and must stay out of frame.
- C1: LL is in the window all demo, so C1 scenes allow LL tiny/from behind (not "fully out").
- C1 #18 (third floor, behind LL's head): C is in view only 25–45% of the time (he moves behind the window frame).
- C1 #01 first person: native POV with the viewmodel and `crosshair 0` (the flight arrival 0:40–0:42 is noclip, allowed per the user).
