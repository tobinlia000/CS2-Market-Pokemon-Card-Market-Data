# Horror / thriller camera techniques → CS2 demo shots

Research notes (2026-09-27) behind the horror recipes in `tools/csdv/scene.py`. These techniques are for **tense
beats only**. `csdv suggest --mood horror` weights them by each beat's tension, so calm moments keep ordinary coverage
and signature moves (dolly zoom, stalker vision) lose weight each time they're used.

| Technique | What it does to the audience | When (scene context) | Our recipe (`shot` + key params) |
|---|---|---|---|
| Negative space / isolation | Character feels exposed; the empty frame is where a threat could appear | Alone, still or walking, especially in open space | `static` far + `margin` 2.4 |
| High angle / god's eye | Makes the subject small and vulnerable; "being watched from above" | Alone and still, searching, deaths | `overhead` (not tracking) + `margin` 2.2 |
| Slow creeping push-in | Dread builds before anything happens | Standing still, looking around | `push` 650→190, low, FOV 40 |
| Floating low Steadicam (*The Shining*) | Too smooth, too steady, relentless: something is pursuing | Walking or running through corridors (tight) | `follow` low (-18), close (115), heading smoothing 1.6, wide lens |
| Space behind the subject | Frame weighted forward; the empty space behind them is where the audience looks | Walking alone; being watched | `lead` with `lead` -0.3 |
| Stalker vision / voyeur (*Halloween*, *Rear Window*) | Someone is observing them; the audience is complicit | Being watched / approached, or alone and searching | `stalker`: hidden spot, tucked against a wall, telephoto, lagging pan, slight handheld. When a real watcher exists, the camera sits at the watcher's position |
| Over the threat's shoulder | Dramatic irony: we see the threat and the unaware victim together | Watched, approached, face-off | `ots` with `over: <threat>` |
| Dutch angle | The world feels wrong; psychological unease | Searching, sudden stop | `static` + `roll` 12 |
| Dolly zoom / Vertigo (*Vertigo*, *Jaws*, *Poltergeist*) | Background warps while the subject stays the same size: the moment of realisation | Sudden stop, face-off, death | `dolly_zoom` (dolly in 320→110 while the lens widens; the angle that keeps most of the move is picked automatically) |
| Low angle on the threat | The antagonist looks dominant and menacing | Face-off, approach, chase (framing the *other* player) | `lead` on the threat, height -45 |
| Handheld chase | Raw panic, immediacy | Chases, frantic running | `follow` close + `handheld` 1.6 |
| Handheld lead, chaser behind | Victim's face and pursuer in one frame | Chases (needs a second player) | `lead` + `handheld` 1.4 |
| Linger on the empty frame | Camera stays as the character leaves; the empty frame is unsettling | Running away, alone | `ground` looking along the route |
| Unsteady observer | Calm moments feel unsafe | Searching, still, watched | `tripod` + `handheld` 0.8 |

Pacing rule from the research: alternate **downtime → build-up → payoff**. The build-up (creep, stalker, negative
space) is where the fear lives, and constant genre shots desensitize. That's why the suggester keeps ordinary shots
in calm beats.

## Backrooms (A24 film, 2026, and Kane Parsons' web series) → `--mood backrooms`
What the research says the camera does:
- **Found footage first.** 90s camcorder look, handheld at eye height, with harried, sweeping pans when the
  character scans the space. The film shot handheld on cinema cameras (DP Jeremy Cox, cropped Super 16, RED Komodo)
  to match Parsons' early videos and the feel of first-person games. "Camcorder" shots were a cinema camera
  operated just off-frame, and the footage went through a real VCR for the degraded look.
- **Wide-angle lenses.** Distorted, slightly hallucinatory: the space feels vast and claustrophobic at once.
- **Restrained camera choreography.** Attention stays on the space itself. Mundane, empty, liminal rooms become
  dreadful when something is slightly off; the dread comes from the environment, not camera tricks.
- **Withholding.** Pans and limited visibility suggest danger just past the frame edge rather than showing it.

Our recipes (backrooms mood also includes the horror recipes, tension-weighted, and drops the drone shots):
| Recipe | Shot | Pacing class |
|---|---|---|
| Found-footage camcorder POV: their eye position + view direction, camcorder inertia, wide lens (95), handheld 0.7; spectated in first person so their own body is hidden | `pov` | move |
| Found-footage follow: a second person filming right behind them at eye level | `follow` 70 u, wide, handheld 0.8 | move |
| Liminal lock-off: still, wide, eye-level frame down the space they walk into | `ground` facing toward, lens 56, FOV 100 | anchor |
| Empty-room wide: the room dwarfs them | `static` margin 1.6 | anchor |
| Dropped camera: camcorder lying tilted on the floor, still recording as they leave (shocks, deaths) | `ground` lens 4, roll 18 | special |
Not done yet: the VHS/VCR look in post (4:3 crop, chroma bleed, noise, tape wobble, timestamp) could be an ffmpeg pass
after rendering.

## Pacing: the big shots only work next to plain ones (every mood)
Each shot is classed **anchor** (static, tripod, ground, stalker, fixed overhead), **move** (follow, lead, side,
arc, push, drone, tracking overhead, camcorder) or **special** (dolly zoom, Dutch, handheld ≥ 1, low angle on the
threat, over-the-shoulder, dropped camera). `scene.pacing()` rules:
- Open on an anchor (establishing, still).
- Never two specials in a row; after a special, the next beat favours an anchor (a breather).
- Specials ≤ ~25% of beats; anchors ≥ ~50% (they get a boost when behind).
- At most two moving shots in a row, then an anchor.
- Signature moves lose weight every time they're used (×0.55 per use for horror/special recipes).
Result on Brycetopher (22 beats): horror = 14 anchors / 5 moves / 3 specials; backrooms = 11 anchors / 7 camcorder
moves / 4 specials, never two specials in a row.

## How scene context is detected (per 0.5 s, from demoparser2 positions + map geometry)
- Motion: still (< 40 u/s), walk, run (> 180 u/s); sudden stop (from > 150 to < 40); searching (view turning
  > 110°/s while not running); corner (travel heading turns > 55° in 0.5 s); crouch.
- Space: 12 horizontal rays at eye height → tight (< 260 u), medium, open (> 650 u); roof above → indoor.
- Other players (line of sight via geometry): watched (behind the subject's view, < 1400 u), chase (both running,
  other behind, < 900 u), face-off (in front, < 800 u), approach (closing > 80 u/s); alone otherwise.
- Events: deaths / kills from the summary.
- Beats: windows with the same kind of context, merged into 2–8 s beats. Teleports and death split beats.

## Sources
- No Film School, "Must-Know Camera Angles and Movements From the Horror Genre": https://nofilmschool.com/horror-camera-angles
- No Film School, "How the Camera Builds Perfect Tension in the Horror Genre": https://nofilmschool.com/cinematography-camera-horror
- StudioBinder, "The Dolly Zoom Shot in Film": https://www.studiobinder.com/camera-shots/camera-movements/dolly-zoom-shot/
- Wikipedia, "Dutch angle": https://en.wikipedia.org/wiki/Dutch_angle
- Film Lifestyle, "What Is Stalker Vision in Film": https://filmlifestyle.com/what-is-stalker-vision/
- StudioBinder, "Telephoto Lens Shot": https://www.studiobinder.com/camera-shots/camera-lenses/telephoto-lens-shot/
- American Cinematographer, "The Steadicam and The Shining Revisited": https://theasc.com/article/steadicam-shining-revisited/
- Wikipedia, "Backrooms (film)": https://en.wikipedia.org/wiki/Backrooms_(film)
- Hypercritic, "Backrooms Explained | Kane Parsons and the Horror of Liminal Spaces": https://hypercritic.org/collection/backrooms-explained-review
- CBR, "Backrooms Review": https://www.cbr.com/backrooms-review/
- CinemaBlend, "How Were Backrooms' Found Footage Scenes Shot?": https://www.cinemablend.com/movies/how-were-backrooms-found-footage-scenes-shot-director-kane-parsons-compares-youtube-series-interview
- Medium, "The Backrooms (Found Footage) – 4 Years On": https://medium.com/@thebackrooms.online/the-backrooms-found-footage-4-years-on-from-kane-pixels-first-backrooms-video-2f3b6c2b3dad
