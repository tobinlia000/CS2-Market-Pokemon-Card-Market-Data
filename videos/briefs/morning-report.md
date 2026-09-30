# Morning report: Part 1 B-roll (overnight 2026-09-29/30)

**Everything is in `Videos\CS2 Renders\Part 1 B-roll\`**, one folder per demo. Each folder has the shots, an `index.md`
(every file with its demo time) and a `contact-sheet.jpg` (one frame per shot, to choose from quickly).
Files are graded, with no black bars, in 8-bit. Naming: `<demo> NN<letter> - <scene> - <angle>.mp4`. **Variant "a" is
the still / static / slow-zoom option**; b, c, d are the cinematic ones (tripod pans, side tracking, crane, drone,
arcs, push-ins, ground level, leading/following). Motion blur is on everywhere.

| Demo | Scene | Files | What's there |
|---|---|---|---|
| A2 | Poolrooms (9) | 38 | Crossing the Poolrooms, the pool passes, on to the edge, the edge/duck spots, crouched at the edge, the run-up and the jump (4 angles) |
| C1 | School (11) | 54 | First-person arrival (bodycam, knife hand, no crosshair), the lamp poles, the school, the flags, the entrance, the stopwatch, the blown door (aftermath only), classrooms, halls, the painted hallway, the locked door, upstairs, the courtyard, **the third-floor window from behind LL's head**, walking to the gap |
| D1 | Insertion2 (13) | 63 | Following Caillou from the station through the foggy town, then the long road under the bridge (LL is at most a speck) |
| G1 | Jungle temple (17) | 77 | Arrival and exploring the ruins, lots of angles on the courtyard stretch, **37a–c: backing away from the still-life, seen from behind its head**, **36a: the finale orbit from behind the still-life around to its face** (blank it in editing) |

All checks passed on every shot: no noclip on screen (except the arrival flight in C1 01 and the jump fall in A2 14,
both allowed), LL's face never readable except the G1 finale, and the camera-gap rule. The safety check passed after
every render; CS2 is closed.

## Needs you (manual shots or a decision)
1. **A2: the still-life under the water.** None of the shots shows it clearly. My "looking down through the water"
   camera came out murky grey or clipped into walls (deleted). The pool-pass shots show Caillou; the figures in the water
   need a manual shot.
2. **A2 01–03 (the dark tunnel at the start)** are very dark even ungraded; they're exported without the Backrooms tone
   curve so they stay readable. Brighten them in CapCut if you use them.
3. **G1: the fall.** There's no recorded "backs off the edge and falls". The backing-away (3:07–3:12.5, facing the
   still-life) is in 37a–c, then he turns and runs off. The only fall is the 3:51.8 death you doubted. **Reshoot the fall.**
4. **G1: chickens.** The demo data only has players, so the shots don't account for the chickens. Check that none
   block a shot you like.
5. **D1:** three late moments (5:46–6:18) had no angle that keeps LL (on the bridge, facing Caillou) face-safe; skipped.
6. **A2 #10** ends at 3:32.8 because LL steps in right next to Caillou at 3:33 (you wanted him out of the edge scene).

## Things I changed on my own (best guesses)
- **Grading:** the tone curve I restore is the Backrooms map's own. It crushed A2's dark areas and doesn't belong on
  the other maps, so it's now used only for Backrooms demos. **C1, D1, G1 are graded without it** (the cinematic look only).
- **Map post-processing was switched off in C1/D1/G1 renders** (the setting made for the Backrooms vignette). The
  school looks natural; D1's fog and G1's ruins look fine. If you want those maps' own look back, it's one setting
  and a re-render.
- **G1 is outdoors:** my camera-safety check rejected open sky, so it was relaxed to a wall-clearance-only check there.
- **D1 is a flash scene,** so I thinned it: every third moment before 4:30, and all of the road/bridge ending.
- Deleted as broken: A2 05b + 07b (murky/clipped water views), C1 13a (a dark wall).

## Housekeeping
- C: has **90 GB free** (was 112 GB). The intermediate render files (`CS2 Renders\BROLL-*.mp4` and `BROLL-*-shots\`,
  about 11 GB) can be deleted once you're happy with the B-roll. I left them in case anything needs re-exporting.
- Usage overnight: weekly 4%, the 5-hour window 29%.
- Everything is committed: the builder is `videos/specs/a1v2/broll.py` (build / fix / export), the log is
  `videos/briefs/overnight-log.md`.
