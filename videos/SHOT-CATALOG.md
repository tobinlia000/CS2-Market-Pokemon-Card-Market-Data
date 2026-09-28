# Shot catalogue — CS2 demo cinematography (CS Demo Manager + HLAE)

Everything I can shoot, sorted by how often it's expected to be used. **Built** = implemented and render-tested;
**built (untested)** = implemented, not yet seen in a render; **possible** = not built yet, but doable with the
same tools (effort noted). Direct shots by name + subject + time + options, e.g.
"F7, 4:28–4:32, slow zoom on the CT, keep N1 hidden".

## Default finish (every render unless you say otherwise)
The "final" version: **ReShade look** (ambient occlusion + depth of field + mild grade + deband, in-game) →
**motion blur** (240 fps blended to 60, 180° shutter) → **cinematic grade** (16-bit, deband, gentle contrast, warm
highlights, vignette, fine grain) → **2.39:1 letterbox** → **10-bit** export. Output: `<name>-final.mp4`, plus the
raw `<name>.mp4` and the separate shots.

## 1. Very frequent — still and slowly moving frames
| # | Shot | What it is | Key options | Status |
|---|---|---|---|---|
| 1 | Locked-off static (wide / medium) | Still camera placed so the whole action stays in frame | framing wide/medium/close, height, lens, position (or auto), dutch roll | Built |
| 2 | Slow zoom | Still camera, the lens creeps in (or out) over the shot | zoom amount (e.g. 100→80%), framing | Built |
| 3 | Eye-level lock-off (arrival) | Still frame at eye height down the space they walk into | lens, how far ahead | Built |
| 4 | Ground-level lock-off | Lens on the floor; they run away from it or toward it | away/toward, lens height, lens | Built |
| 5 | Liminal lock-off | Wide, still, eye-level frame down an empty hall they slowly enter | lens (wide), look-ahead | Built |
| 6 | Empty-room / establishing wide | Wide still frame where the space dwarfs them | framing margin | Built |
| 7 | Landmark shot | Frame or slow-zoom on an object (EXIT sign, door, painting…) | object (map legend L#), start/end lens, duration | Built (by coordinates); by landmark name: possible (small) |
| 8 | Tripod pan | Fixed camera that pans to follow them | framing, operator lag | Built |
| 9 | Stalker / voyeur | Hidden watcher far away, long lens, tucked behind a wall edge, slight shake | distance, position (or auto / at a real watcher) | Built |
| 10 | Hold / linger | Frame stays after they leave (or before they enter) | which side, duration | Built |

## 2. Frequent — coverage and relationships
| # | Shot | What it is | Key options | Status |
|---|---|---|---|---|
| 11 | Over-the-shoulder | Behind one player's head/shoulder, framing the other | whose shoulder, left/right, distance | Built |
| 12 | Overhead (fixed) | Bird's-eye, straight down, fits the action | height, rotation | Built |
| 13 | Push-in | Camera moves slowly toward them | start/end distance, angle | Built |
| 14 | Pull-out / reveal | Camera moves away, revealing the space | start/end distance | Built |
| 15 | First-person POV | Their own view (CS:DM default spectate) | player | Built |
| 16 | Low angle (menace/hero) | From below, looking up at a player | height, distance | Built |
| 17 | High angle (vulnerable) | From above, looking down at a player | height | Built (overhead/static) |
| 18 | Negative space framing | Subject off to one side, empty space where a threat could be | side, amount | Built |
| 19 | Two-shot | Both players in one still frame | framing | Built (static fits both); dedicated two-subject framing: possible (small) |
| 20 | Shot / reverse-shot | Alternating over-the-shoulders for a face-off | cut points | Possible (small: pairs of OTS) |

## 3. Occasional — camera moves with the action
| # | Shot | What it is | Key options | Status |
|---|---|---|---|---|
| 21 | Follow (Steadicam) | Behind them, smooth | distance, height, lens | Built |
| 22 | Lead | In front, walking backwards facing them | distance | Built |
| 23 | Side tracking (dolly) | Parallel to them | side, distance | Built |
| 24 | Overhead tracking | Bird's-eye drifting with them | height | Built |
| 25 | Arc | Swings partway around them | start/end angle | Built |
| 26 | Crane | Rises or drops while framing them | start/end height | Built |
| 27 | Camcorder POV (found footage) | Their eyes + camcorder inertia + wide lens + shake | shake amount, lens | Built |
| 28 | Camcorder follow | Second person filming right behind them | distance, shake | Built |
| 29 | Chase cam (3rd person) | CS2's spectator chase camera | — | Built |
| 30 | Floating low Steadicam | Low, too-steady corridor follow (The Shining) | height, distance | Built |

## 4. Special — reserve for intense moments
| # | Shot | What it is | Key options | Status |
|---|---|---|---|---|
| 31 | Dolly zoom (Vertigo) | Camera moves in, lens widens: background warps, subject same size | direction, amount | Built |
| 32 | Dutch angle | Tilted horizon on any shot | roll degrees | Built |
| 33 | Handheld (any shot) | Operator drift, jitter, footsteps | 0.5 subtle / 1 documentary / 2 frantic | Built |
| 34 | Handheld chase | Tight, shaky, right on their heels | shake, distance | Built |
| 35 | Drone flyover / orbit / rise | Sweeping aerial move with its own path | altitude, move type | Built |
| 36 | Dropped camera | Camcorder lying tilted on the floor, still recording | tilt | Built (untested) |
| 37 | Crash zoom / snap zoom | Very fast lens change onto a subject or object | target, speed | Possible (small: keyed lens) |
| 38 | Whip pan | Fast pan between two subjects or places | from/to | Possible (small) |
| 39 | Rack focus | Focus shifts between a near and far subject | from/to distance | Possible (medium: animate ReShade focus) |
| 40 | Slow motion | Record at 120/240 fps and play at 60 (2×/4×) | speed | Possible (small) |
| 41 | Speed ramp / freeze frame | Speed changes or a held frame mid-shot | where, how long | Possible (small, in post) |
| 42 | Follow a grenade / object | Camera tracks a projectile | object | Possible (medium: projectile positions) |

## Look and quality options (per scene or per shot)
| Option | What it does | Status |
|---|---|---|
| Final look (default) | ReShade look + motion blur + cinematic 10-bit grade + letterbox | Built |
| ReShade presets | `look` (AO + DOF + grade + deband), `clean` (deband only), `off` | Built |
| Ambient occlusion strength / depth-of-field focus | Stronger/softer contact shadows; focus distance or autofocus point | Built (preset values); per-shot tuning: possible (small) |
| Motion blur | On/off, shutter (0.25–1.0), input fps | Built |
| Grade | Cinematic (default), camcorder/VHS, none; letterbox on/off; grain/vignette strength | Built |
| Lens / field of view | Any focal length per shot; animated zooms | Built |
| HUD | Hidden in cinematic shots; kill feed or full HUD if wanted | Built |
| Hidden place | A spot/pocket that must never be on screen in a scene | Built |
| Subject switching | Follow another player while one is hidden | Built |
| Skybox colour / texture | `mirv_sky` (outdoor maps) | Possible (small, untested) |
| Remove glow / name tags / change smoke colours | `mirv_glow`, `mirv_colors` | Possible (small, untested) |
| Depth / matte passes | For compositing in an editor (fog, focus, masks) | Possible (medium) |
| Editing-grade output | ProRes / image sequences for DaVinci Resolve | Possible (small) |
| Supersampling (4K → 1440p) | Cleaner edges | Needs NVIDIA DSR (a driver setting, your call) |

## Workflow
1. You give me a demo → I make **high-res floor maps** (one per floor: spaces with IDs, grid squares, numbered
   landmarks, both players' routes with times and direction, still spots) plus a **movement timeline** (every 5 s:
   who is where, doing what). Command: `python tools/csdv/csdv.py maps videos/demos/<demo>.summary.json`.
2. You check the maps; we fix anything wrong (layout, names, what counts as a room).
3. You write the shot list (shots, lengths, times, places by space ID / grid square / landmark number). Optionally
   send a draft first and I'll identify every referenced object/place on the maps.
4. I build it (checking visibility, hidden places, framing) and render the final version.
