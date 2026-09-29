# Shared file — Cameraman ⇄ Creative Director

**Roles (set by the user, 2026-09-28)**
- **Cameraman** (Claude Code, on the PC): owns the shooting. Has the demos, map geometry and renderer. Decides
  cameras and framing, reads the demos, and reports what's really in them: takes, repeats, improvised additions,
  dead space. Keeps this file.
- **Creative Director** (claude.ai chat): owns lore and story. Checks the cameraman's reading of each demo for
  story and lore consistency, decides which takes and scenes are canon, and writes scene intent.
- **The user** relays this file between the two chats and gives feedback after each full render.

**How to use this file**
- The user uploads the latest version to the CD chat. The CD replies in its own section (or as a pasted block);
  the user pastes the reply back to the cameraman, who merges it here and bumps the version.
- Newest entries go at the top of each log. Use shot numbers (`#12`), demo times (`m:ss.s`) and map places
  (space ID / grid square / L#).
- The cameraman may change cameras freely. Story changes (dropping or adding a beat, reordering) need the CD's OK.

**Version:** v1 · 2026-09-28 · by Cameraman

---

## 1. Status board
| Demo | Map | State |
|---|---|---|
| A1 | ze_backrooms_insomnia | **Rendering the full v2 shot list** (57 shots + a clean plate). Awaiting the user's feedback after it |
| A2 | ze_backrooms_insomnia (upper floors) | Maps done; not planned yet |
| C1 | de_02school | Maps done; not planned |
| D1 | cs_insertion2 | Maps done; not planned |
| G1 | de_lord | Maps done; not planned |

## 2. Cameraman → CD: what's actually in A1 (please check against the lore)
Read from the demo data (positions, view angles, events). There's no voice track in the demo, so dialogue timing
comes from your notes. ✔ = used in the v2 cut; ? = your call.

| # | Demo time | What happens | My read | In v2? |
|---|---|---|---|---|
| A | 0:00–0:22 | Both in place; LL swings his knife 5× (0:00–0:03); C shuffles around W28 | **Pre-roll / setup**, dead space | – |
| B | 0:22–2:47 | "Trapped": wall knifing, first glimpse (1:24–1:31, LL a silhouette in the doorway), backing away, console, relief, second glimpse (2:26–2:33), bolt at 2:47 | **Clean take** | ✔ #1–#17 |
| C | 2:47–3:10 | The run; LL silently follows (W25→S23) | Clean | ✔ #18–#21 |
| D | 3:11–3:19 | LL noclips to **right behind C** (50–150 u); C never turns | Looks **improvised on the fly**. Strong "it's right behind you" beat | ✔ #22 — **? keep** |
| E | 3:19–4:00 | LL noclips into the dark pillar hall and stares; C stares back into the darkness and bolts at 3:59.3 | Clean | ✔ #23–#28 |
| F | 4:00–4:40 | Run; second sighting: **LL charges to point-blank range at 4:30.5**, backs off, C flees | Clean (charge, not a standing figure) | ✔ #29–#35 |
| G | 4:40–5:40 | C loiters around M20; LL slowly walks toward him (Q19→O20→N20) | **Probably a retake** of the second sighting (take 2), or a reset | ✗ **? take 2 worth using** |
| H | 5:41–6:41 | Run loop, then stop-start backwards steps past the small pillars (6:26–6:41) | "Pillars" scene, clean | ✔ #36–#40 |
| I | 6:41–7:03 | C moves at noclip speed to H20; chat "> 40 <" | Reposition / dead space | – |
| J | 7:03–7:45 | LL noclips **30 u from C** and stands there; then darts around D21–G20 with footsteps | **Added idea or goofing?** Looks like another "right behind you" variant | ✗ **? your call** |
| K | 7:45–9:34 | C travels west, teleports north (W4), walks to the corner room; chat "> 10 <" at 8:57 | Travel / setup | – |
| L | 9:34–10:00 | C pops up to floor E and back; **both fire Glocks** (9:48, 9:53–9:58) | Out-of-character **goofing / tests** | – |
| M | 10:00.2 | **Map restart**, both respawn; chat "Backrooms Insomnia Demo v1.0 / Map by @Seth" | Technical | – |
| N | 10:00–13:00 | Both noclip back; smokes equipped (10:54, 12:32); positioning in the corner room | **Rehearsal / blocking** for the corner scene | – |
| O | 13:01–15:42 | Corner scene: 5 smokes (13:17.7, 13:45.8, 14:25.9, 15:03.0, 15:25.9); **two test drops into the pit** (13:55, 14:36); close encounter (14:52); **walks backwards** into the pit at 15:40.5 | The final take; the pit drops look like **rehearsals mixed into the take** | ✔ #41–#57 (drops cut) |
| P | 15:42–16:01 | Teleported, flies, lands on floor E | Post-scene | – |

**Not in A1** (and not in A2 either: A2 happens on the upper floors): the pit-scene opening ("246, 247", staring
back, walking to the pit, backing into the corner). It needs a new recording.

## 3. Cameraman → CD: open questions
1. **D (3:11–3:19), LL right behind C:** canon? It's in v2 as #22.
2. **G (4:40–5:40), a slow second approach:** a take 2 we should use or ignore?
3. **J (7:03–7:45), LL 30 u from C, then darting with footsteps:** a scene, or goofing?
4. **O, the pit test drops at 13:55 and 14:36:** in-story (he tests the pit), or rehearsal to cut? v2 cuts before
   each drop.
5. **C walks backwards into the pit, facing LL**, not away from him. v2 keeps it as it happened. Does the story
   still work?
6. Dialogue: there's no voice track in the demo. Please keep giving line timings, so I can cut on them.

## 4. CD → Cameraman (Creative Director writes here)
_(empty)_

## 5. Decisions log
- 2026-09-28 · CD: LL's front is never readable; face-safe angles only (exceptions #7 and #9, the tiny silhouette).
- 2026-09-28 · CD: specials limited to the whip pan (#27–28), the handheld surge (#34) and the slow-mo fall (#57).
- 2026-09-28 · Cameraman: #49 changed from an OTS to a shot from the pit side (he walks backwards, so an OTS would
  show his face); #50's POV faces the room, not the pit.

## 6. Render log
- 2026-09-28 · **A1 v2** · 57 shots + clean plate (#35p at 11:00–11:04), about 5.5 min of footage,
  final look. In progress.
- 2026-09-28 · A1 visibility test (6 POV clips): LL is a silhouette at 1:26 and 2:29, invisible at 3:36 and 3:57,
  clear at 4:30 and 15:24.
