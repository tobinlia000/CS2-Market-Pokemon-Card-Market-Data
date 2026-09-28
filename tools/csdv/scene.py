"""Scene analysis + automatic shot suggestions.

Reads per-tick demo positions (and map geometry when available), splits a time range into beats (2-8 s) of
similar context, tags each beat (motion, space, other players, events) and ranks shot recipes for it.

- Horror/thriller techniques (see videos/HORROR-CAMERA.md) are only offered with mood "horror", and they are weighted
  by the beat's tension: calm beats still get ordinary shots, tense beats get the genre shots.
- Every top candidate is test-built with cine.build against the map; shots that can't see the subject are dropped.
- Consecutive beats avoid repeating the same shot type.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import gaussian_filter1d

import cine
from positions import Track

WINDOW_TICKS = 32            # analysis step (0.5 s at 64 tick)
MIN_BEAT_SECONDS = 3.0       # user: still shots, held; fewer cuts
MAX_BEAT_SECONDS = 9.0
EYE = 60.0

WALK_SPEED = 40.0
RUN_SPEED = 180.0


# --- analysis ---------------------------------------------------------------------------------------------------

@dataclass
class Window:
    tick: int
    speed: float
    duck: float
    yaw_rate: float          # deg/s the player's view turns (nervous looking around)
    turn: float              # deg the travel heading changed in this window
    openness: float | None   # mean free distance around the player (units), None without geometry
    covered: bool | None     # roof above
    others: list = field(default_factory=list)   # dicts: name, steamId, dist, los, behind, closing, watching, alive
    alive: bool = True
    events: list = field(default_factory=list)   # ("death" | "kill", tick)
    teleport: bool = False
    inside: bool = False          # the subject is inside the hidden zone


@dataclass
class Beat:
    start: int
    end: int
    label: str
    tags: set
    tension: float
    windows: list
    other: dict | None = None     # the most relevant other player (watcher / chaser / opponent)
    subject: str | None = None    # who the shots follow in this beat (None: the scene's subject)

    @property
    def motion(self) -> str:
        """still / walk / run from the average speed (the base for everyday shots on any beat)."""
        speed = float(np.mean([x.speed for x in self.windows]))
        return "still" if speed < WALK_SPEED else "run" if speed > RUN_SPEED else "walk"

    @property
    def seconds(self) -> float:
        return (self.end - self.start) / 64.0

    def describe(self, subject_name: str) -> str:
        w = self.windows
        speed = np.mean([x.speed for x in w])
        parts = [f"{subject_name} {MOTION_TEXT.get(self.label, self.label)}"]
        if "tight" in self.tags:
            parts.append("in a tight space")
        elif "open" in self.tags:
            parts.append("in open space")
        if "indoor" in self.tags:
            parts.append("indoors")
        if self.other and self.label in ("watched", "chase", "approach", "face_off"):
            parts.append(f"({self.other['name']} {OTHER_TEXT[self.label]}, {self.other['dist']:.0f} u away)")
        parts.append(f"~{speed:.0f} u/s")
        return " ".join(parts)


MOTION_TEXT = {"still": "is standing still", "walk": "is walking", "run": "is running", "searching": "is looking around",
               "corner": "turns a corner", "startle": "stops suddenly", "watched": "is being watched",
               "chase": "is being chased", "approach": "is being approached", "face_off": "faces someone",
               "death": "dies", "kill": "gets a kill", "hidden": "is hidden"}
OTHER_TEXT = {"watched": "behind them with line of sight", "chase": "running after them",
              "approach": "closing in", "face_off": "in front of them"}
LABEL_PRIORITY = ["hidden", "death", "kill", "chase", "watched", "face_off", "approach", "startle", "searching",
                  "corner", "run", "walk", "still"]
TENSION = {"hidden": 2.2, "death": 3.0, "kill": 2.0, "chase": 3.0, "watched": 2.6, "face_off": 2.2, "approach": 2.0, "startle": 2.0,
           "searching": 1.5, "corner": 1.0, "run": 1.0, "walk": 0.4, "still": 0.6}


def _at(track: Track, ticks: np.ndarray):
    i = np.searchsorted(track.tick, ticks).clip(0, len(track.tick) - 1)
    return i


def analyze(tracks: dict[str, Track], subject_id: str, start: int, end: int, geometry=None,
            events: list[dict] | None = None, tickrate: float = 64.0) -> list[Window]:
    sub = tracks[subject_id]
    ticks = np.arange(start, end, WINDOW_TICKS)
    pos = gaussian_filter1d(sub.pos, sigma=0.2 * tickrate, axis=0, mode="nearest")
    yaw = np.degrees(np.unwrap(np.radians(sub.yaw)))
    teleports = set(int(t) for t in sub.teleports())
    windows = []
    prev_heading = None
    for t in ticks:
        a, b = _at(sub, np.array([t, t + WINDOW_TICKS]))
        if b <= a:
            b = min(a + 1, len(sub.tick) - 1)
        seg = pos[a:b + 1]
        dt = max((sub.tick[b] - sub.tick[a]) / tickrate, 1e-3)
        travel = seg[-1, :2] - seg[0, :2]
        speed = float(np.linalg.norm(travel) / dt)
        heading = math.degrees(math.atan2(travel[1], travel[0])) if speed > WALK_SPEED else prev_heading
        turn = 0.0
        if heading is not None and prev_heading is not None:
            turn = abs((heading - prev_heading + 180) % 360 - 180)
        prev_heading = heading if heading is not None else prev_heading
        yaw_rate = float(np.abs(np.diff(yaw[a:b + 1])).sum() / dt) if b > a else 0.0
        feet = pos[a]
        eye = feet + [0, 0, EYE]
        openness = covered = None
        if geometry is not None:
            free = []
            for k in range(12):
                ang = math.radians(k * 30)
                ray = np.array([math.cos(ang), math.sin(ang), 0.0]) * 1500.0
                hit = geometry.first_hit(eye, eye + ray, see_through_blocks=True)
                free.append(1500.0 if hit is None else hit * 1500.0)
            openness = float(np.mean(sorted(free)[:8]))  # ignore the 4 longest sight lines (streets that run on)
            covered = geometry.first_hit(eye, eye + [0, 0, 700.0], see_through_blocks=True) is not None
        view = np.array([math.cos(math.radians(sub.yaw[a])), math.sin(math.radians(sub.yaw[a]))])
        others = []
        for sid, other in tracks.items():
            if sid == subject_id:
                continue
            oa, ob = _at(other, np.array([t, t + WINDOW_TICKS]))
            if not other.alive[oa]:
                continue
            opos = other.pos[oa]
            delta = opos[:2] - feet[:2]
            dist = float(np.linalg.norm(np.r_[delta, opos[2] - feet[2]]))
            if dist > 3000:
                continue
            los = geometry.clear(eye, opos + [0, 0, EYE]) if geometry is not None else dist < 1500
            to_other = delta / max(np.linalg.norm(delta), 1e-6)
            angle_from_view = math.degrees(math.acos(float(np.clip(view @ to_other, -1, 1))))
            other_view = np.array([math.cos(math.radians(other.yaw[oa])), math.sin(math.radians(other.yaw[oa]))])
            watching = math.degrees(math.acos(float(np.clip(other_view @ -to_other, -1, 1)))) < 35
            dist_next = float(np.linalg.norm(other.pos[ob] - sub.pos[b]))
            closing = (dist - dist_next) / dt
            ospeed = float(np.linalg.norm(other.pos[ob, :2] - other.pos[oa, :2]) / dt)
            others.append(dict(name=other.name, steamId=sid, dist=dist, los=bool(los), behind=angle_from_view > 110,
                               in_front=angle_from_view < 45, watching=watching, closing=closing, speed=ospeed))
        evs = [(e["type"], e["tick"]) for e in (events or []) if t <= e["tick"] < t + WINDOW_TICKS]
        windows.append(Window(int(t), speed, float(sub.duck[a]), yaw_rate, turn, openness, covered, others,
                              bool(sub.alive[a]), evs, any(t <= x < t + WINDOW_TICKS for x in teleports)))
    return windows


def window_label(w: Window, prev: Window | None) -> tuple[str, set, dict | None]:
    if w.inside:
        return "hidden", {"hidden"}, None
    tags = set()
    if w.openness is not None:
        tags.add("tight" if w.openness < 260 else "open" if w.openness > 650 else "medium")
    if w.covered:
        tags.add("indoor")
    if w.duck > 0.5:
        tags.add("crouch")
    other = None
    label = "still" if w.speed < WALK_SPEED else "run" if w.speed > RUN_SPEED else "walk"
    candidates = []
    for ev, _ in w.events:
        candidates.append(ev)
    if prev is not None and prev.speed > 150 and w.speed < WALK_SPEED:
        candidates.append("startle")
    if w.speed < RUN_SPEED and w.yaw_rate > 110:
        candidates.append("searching")
    if w.turn > 55:
        candidates.append("corner")
    for o in sorted(w.others, key=lambda o: o["dist"]):
        if o["los"] and w.speed > RUN_SPEED * 0.8 and o["speed"] > RUN_SPEED * 0.8 and o["behind"] and o["dist"] < 900:
            candidates.append("chase"); other = other or o
        elif o["los"] and o["behind"] and o["dist"] < 1400 and not o["in_front"]:
            candidates.append("watched"); other = other or o
        elif o["los"] and o["in_front"] and o["dist"] < 800:
            candidates.append("face_off"); other = other or o
        elif o["los"] and o["closing"] > 80 and o["dist"] < 1200:
            candidates.append("approach"); other = other or o
    if not any(o["los"] and o["dist"] < 1500 for o in w.others):
        tags.add("alone")
    candidates.append(label)
    label = min(candidates, key=LABEL_PRIORITY.index)
    return label, tags, other


def segment(windows: list[Window], min_seconds: float = MIN_BEAT_SECONDS,
            max_seconds: float = MAX_BEAT_SECONDS) -> list[Beat]:
    """Merge half-second windows into beats of one context. Teleports and death split beats; dead time is skipped."""
    labelled = []
    prev = None
    for w in windows:
        if not w.alive:
            prev = None
            labelled.append(None)
            continue
        labelled.append(window_label(w, prev) + (w,))
        prev = w
    beats: list[Beat] = []
    current: list = []

    def close():
        if not current:
            return
        labels = [c[0] for c in current]
        label = min(set(labels), key=lambda lab: (-labels.count(lab) * (1 + 2 * (LABEL_PRIORITY.index(lab) < 7)),
                                                 LABEL_PRIORITY.index(lab)))
        tags = set().union(*[c[1] for c in current])
        # space tag: the majority, not the union
        for group in (("tight", "medium", "open"),):
            counts = {g: sum(g in c[1] for c in current) for g in group}
            tags -= set(group)
            if any(counts.values()):
                tags.add(max(counts, key=counts.get))
        others = [c[2] for c in current if c[2]]
        ws = [c[3] for c in current]
        tension = float(np.mean([TENSION[c[0]] for c in current]))
        tension = max(tension, TENSION[label] * 0.8)
        if "alone" in tags and ("tight" in tags or "indoor" in tags):
            tension += 0.4
        beats.append(Beat(ws[0].tick, ws[-1].tick + WINDOW_TICKS, label, tags, round(tension, 2), ws,
                          others[len(others) // 2] if others else None))
        current.clear()

    for item in labelled:
        if item is None:
            close()
            continue
        window = item[3]
        if window.teleport:
            close()
            continue
        if current:
            same_group = _group(item[0]) == _group(current[-1][0])
            too_long = (len(current) + 1) * WINDOW_TICKS / 64.0 > max_seconds
            if (not same_group and len(current) * WINDOW_TICKS / 64.0 >= min_seconds) or too_long:
                close()
        current.append(item)
    close()
    # fold beats that are too short into a neighbour
    merged: list[Beat] = []
    for beat in beats:
        if merged and beat.seconds < min_seconds and merged[-1].end == beat.start \
                and merged[-1].seconds + beat.seconds <= max_seconds:
            last = merged[-1]
            merged[-1] = Beat(last.start, beat.end, last.label if last.tension >= beat.tension else beat.label,
                              last.tags | beat.tags, max(last.tension, beat.tension), last.windows + beat.windows,
                              last.other or beat.other)
        elif beat.seconds >= 1.0:
            merged.append(beat)
    return merged


def _group(label: str) -> str:
    return {"walk": "move", "run": "move", "corner": "move", "still": "still", "searching": "still"}.get(label, label)


# --- recipes ----------------------------------------------------------------------------------------------------

@dataclass
class Recipe:
    name: str
    technique: str
    shot: str
    params: dict
    weights: dict                     # beat label -> base weight
    horror: bool = False
    needs: set = field(default_factory=set)    # tags the beat must have
    avoid: set = field(default_factory=set)    # tags that rule the recipe out
    subject_is_other: bool = False    # frame the other player (the threat) instead of the subject
    over_other: bool = False          # over the other player's shoulder
    requires_other: bool = False      # only when another player is part of the beat
    why: str = ""
    moods: tuple | None = None        # moods the recipe is offered in (None: all; horror recipes: horror + backrooms)
    tense: bool | None = None         # weight by beat tension (default: horror recipes)
    style: str = ""                   # "backrooms": found-footage recipes, favoured in that mood
    special: bool = False             # force the "special" pacing class


def category(recipe: "Recipe") -> str:
    """Pacing class. anchor = calm, static-feeling coverage; move = camera travels; special = attention-grabbing.
    Specials only keep their effect when they are rare and surrounded by anchors."""
    p = recipe.params
    if (recipe.special or recipe.shot == "dolly_zoom" or p.get("roll") or float(p.get("handheld", 0)) >= 1.0
            or recipe.over_other or recipe.subject_is_other):
        return "special"
    if recipe.shot in ("static", "tripod", "ground", "stalker") or (recipe.shot == "overhead" and not p.get("track")):
        return "anchor"
    return "move"


def allowed(recipe: "Recipe", mood: str) -> bool:
    moods = recipe.moods or (("horror", "backrooms") if recipe.horror else None)
    return moods is None or mood in moods


MOVE = ("walk", "run", "corner")
RECIPES = [
    # Everyday coverage (any mood).
    Recipe("steadicam follow", "Steadicam follow", "follow", {}, {"walk": 0.9, "run": 0.8, "corner": 0.8},
           why="stays with them through the space"),
    Recipe("side tracking", "Dolly track", "side", {}, {"walk": 0.8, "run": 0.9}, avoid={"tight"},
           why="shows speed and the space they move through"),
    Recipe("leading shot", "Lead / walk-and-talk", "lead", {}, {"walk": 0.7, "run": 0.6},
           why="shows their face and where they come from"),
    Recipe("tripod pan", "Tripod pan", "tripod", {"size": "full"}, {"walk": 0.45, "run": 0.5, "corner": 0.5},
           why="a fixed observer panning with the action"),
    Recipe("locked-off wide", "Static wide", "static", {}, {"still": 0.9, "walk": 0.6, "searching": 0.6},
           why="lets the action play inside a still frame"),
    Recipe("overhead", "Bird's-eye", "overhead", {"track": True}, {"walk": 0.5, "run": 0.6, "still": 0.5},
           why="shows the geography from above"),
    Recipe("drone flyover", "Aerial flyover", "drone", {}, {"run": 0.8, "walk": 0.6},
           needs={"open"}, avoid={"indoor"}, why="a sweeping establishing move over the area",
           moods=("neutral", "horror")),
    Recipe("drone orbit", "Aerial orbit", "drone", {"move": "orbit"}, {"still": 0.7, "searching": 0.5},
           needs={"open"}, avoid={"indoor"}, why="circles the scene to set it up", moods=("neutral", "horror")),
    Recipe("slow zoom", "Slow zoom (camera still)", "static", {"zoom": [1.0, 0.8]},
           {"still": 1.1, "walk": 1.0, "run": 0.8, "searching": 1.1, "corner": 0.9},
           why="a still camera whose lens creeps in: attention builds without any camera movement"),
    Recipe("slow zoom medium", "Slow zoom, medium (camera still)", "static",
           {"margin": 1.1, "distance": 450.0, "zoom": [1.0, 0.85]}, {"still": 1.0, "walk": 0.8, "searching": 1.0},
           why="a closer still frame, gently tightening on them"),
    Recipe("locked-off medium", "Static medium", "static", {"margin": 1.05, "distance": 450.0},
           {"still": 0.8, "walk": 0.7, "searching": 0.7, "run": 0.4},
           why="a plain, still frame: the calm between the bigger moments"),
    Recipe("eye-level arrival", "Eye-level lock-off (arrival)", "ground", {"facing": "toward", "lens": 58.0, "fov": 85.0},
           {"walk": 0.8, "run": 0.7, "corner": 0.6},
           why="a still frame down the space they walk into, held at eye height"),
    Recipe("ground runner", "Ground-level lock-off", "ground", {}, {"run": 0.9, "walk": 0.5},
           why="the lens rests on the floor while they run away from it"),
    Recipe("ground arrival", "Ground-level lock-off (arrival)", "ground", {"facing": "toward"},
           {"run": 0.6, "walk": 0.6}, why="they run straight at a camera on the floor"),
    Recipe("slow arc", "Arc", "arc", {}, {"walk": 0.5, "still": 0.5}, why="a gentle reveal of the space around them"),
    Recipe("push-in", "Push-in", "push", {}, {"still": 0.6, "walk": 0.4}, why="draws attention to them"),

    # Horror / thriller (mood "horror" only, weighted by tension).
    Recipe("negative space", "Negative space / isolation", "static", {"margin": 2.4, "distance": 1000.0},
           {"still": 1.0, "walk": 0.8, "searching": 0.9}, horror=True, needs={"alone"},
           why="a tiny figure in a big empty frame feels exposed; the empty space is where a threat could appear"),
    Recipe("god's eye isolation", "High angle (vulnerable)", "overhead", {"margin": 2.2},
           {"still": 0.9, "searching": 0.9, "death": 1.4}, horror=True,
           why="looking straight down from high makes them small and vulnerable"),
    Recipe("creeping push-in", "Slow creeping push-in", "push",
           {"distance": [650.0, 190.0], "height": -10.0, "fov": 40.0}, {"still": 1.1, "searching": 1.3, "walk": 0.6},
           horror=True, why="a slow push toward them builds dread before anything happens"),
    Recipe("shining follow", "Floating low Steadicam (The Shining)", "follow",
           {"distance": 115.0, "height": -18.0, "headingSmooth": 1.6, "fov": 85.0},
           {"walk": 1.1, "run": 0.9, "corner": 1.1}, horror=True, needs={"tight"},
           why="a low, gliding camera that follows too steadily through corridors feels like something is pursuing"),
    Recipe("space behind", "Negative space behind the subject", "lead", {"lead": -0.3, "distance": 210.0},
           {"walk": 1.0, "watched": 1.3, "corner": 0.8}, horror=True,
           why="framing them to one side leaves empty space behind them, where the audience expects the threat"),
    Recipe("stalker vision", "Stalker vision / voyeur", "stalker", {},
           {"walk": 0.9, "still": 0.9, "searching": 1.0, "watched": 1.5, "approach": 1.2}, horror=True,
           why="a hidden watcher's long lens from behind cover: someone is observing them"),
    Recipe("threat over the shoulder", "Over the threat's shoulder", "ots", {},
           {"watched": 1.7, "approach": 1.3, "chase": 1.0, "face_off": 1.1}, horror=True, over_other=True,
           why="the audience sees the threat and the unaware victim together (dramatic irony)"),
    Recipe("dutch lock-off", "Dutch angle", "static", {"roll": 12.0},
           {"searching": 1.1, "startle": 0.9, "still": 0.6}, horror=True,
           why="a tilted horizon makes the world feel wrong"),
    Recipe("vertigo", "Dolly zoom (Vertigo)", "dolly_zoom", {},
           {"startle": 1.6, "face_off": 1.4, "searching": 0.8, "death": 1.0}, horror=True,
           why="the background warps while they stay the same size: the moment of realisation"),
    Recipe("menacing low angle", "Low angle on the threat", "lead", {"height": -45.0, "distance": 170.0, "fov": 70.0},
           {"face_off": 1.3, "approach": 1.2, "chase": 0.9}, horror=True, subject_is_other=True,
           why="shooting the threat from below makes them dominant and menacing"),
    Recipe("chase handheld", "Handheld chase", "follow", {"distance": 110.0, "handheld": 1.6, "fov": 85.0},
           {"chase": 1.6, "run": 1.0}, horror=True, why="raw, shaking panic right on their heels"),
    Recipe("run-and-gun lead", "Handheld lead (chaser in the background)", "lead",
           {"distance": 150.0, "handheld": 1.4, "fov": 80.0}, {"chase": 1.4, "approach": 0.9}, horror=True,
           requires_other=True,
           why="their face and the pursuer behind them in one frame"),
    Recipe("linger", "Linger on the empty frame", "ground", {"lookAhead": 0.3},
           {"run": 0.9, "walk": 0.6}, horror=True, needs={"alone"},
           why="the camera stays behind as they leave; the empty frame that remains is unsettling"),
    Recipe("nervous observer", "Unsteady observer", "tripod", {"handheld": 0.8, "size": "medium"},
           {"searching": 0.8, "still": 0.5, "watched": 0.6}, horror=True,
           why="a slightly shaky, lagging observer makes calm moments feel unsafe"),

    # Backrooms / found footage (mood "backrooms"): camcorder language + restrained, empty, liminal frames.
    Recipe("camcorder pov", "Found-footage camcorder POV", "pov", {"handheld": 0.7, "fov": 95.0},
           {"walk": 1.0, "run": 1.0, "searching": 1.3, "still": 0.6, "corner": 1.0}, style="backrooms",
           moods=("backrooms",),
           why="the character's own camcorder at eye height, wide lens, sweeping when they look around"),
    Recipe("camcorder follow", "Found-footage follow (second camera)", "follow",
           {"distance": 70.0, "height": 6.0, "fov": 95.0, "handheld": 0.8},
           {"walk": 1.0, "run": 0.9, "corner": 1.0}, style="backrooms", moods=("backrooms",),
           why="someone filming right behind them at eye level, like the second person in found footage"),
    Recipe("liminal hallway", "Liminal lock-off", "ground", {"facing": "toward", "lens": 56.0, "fov": 100.0,
                                                              "lookAhead": 0.7},
           {"walk": 1.1, "run": 0.8, "still": 0.7, "corner": 0.8}, style="backrooms", moods=("backrooms",),
           why="a still, wide eye-level frame down an empty space they slowly walk into: dread from the space itself"),
    Recipe("empty room wide", "Empty-room wide", "static", {"margin": 1.6},
           {"still": 1.0, "searching": 1.0, "walk": 0.8}, style="backrooms", moods=("backrooms",),
           why="a wide, still frame where the room dwarfs them: vast and claustrophobic at once"),
    Recipe("dropped camera", "Dropped camera", "ground", {"lens": 4.0, "roll": 18.0, "fov": 95.0, "lookAhead": 0.35},
           {"startle": 1.4, "death": 1.6, "chase": 0.9, "run": 0.4}, style="backrooms", moods=("backrooms",),
           tense=True, special=True,
           why="the camcorder has fallen and lies tilted on the floor, still recording as they leave"),
]


@dataclass
class Suggestion:
    recipe: Recipe
    camera: dict
    score: float
    why: str
    visible: float
    subject_name: str


def suggest(beat: Beat, tracks: dict[str, Track], subject_id: str, mood: str = "neutral", geometry=None,
            history: list[tuple[str, str]] | None = None, top: int = 3, tickrate: float = 64.0,
            pace: dict | None = None, hide: dict | None = None) -> list[Suggestion]:
    history = history or []
    pace = pace or {}
    horror = mood in ("horror", "backrooms")
    scored = []
    for recipe in RECIPES:
        if not allowed(recipe, mood):
            continue
        tense = recipe.horror if recipe.tense is None else recipe.tense
        base = recipe.weights.get(beat.label)
        if base is None and not tense:
            base = recipe.weights.get(beat.motion, 0) * 0.8   # everyday coverage for any beat, by how they move
        if not base:
            continue
        if recipe.needs - beat.tags or recipe.avoid & beat.tags:
            continue
        if (recipe.over_other or recipe.subject_is_other or recipe.requires_other) and not beat.other:
            continue
        if category(recipe) == "special" and beat.tension < SPECIAL_MIN_TENSION:
            continue  # special shots only for intense moments
        if tense:
            # Genre shots are for tense beats: ~0 at tension 1.2, ~0.6 at 2.0, >1 for chases/watchers/deaths.
            score = base * float(np.clip((beat.tension - 1.2) / 1.3, 0.0, 1.3))
            if score <= 0.05:
                continue
        else:
            score = base * ((1.15 - 0.25 * beat.tension) if horror else 1.0)
        if recipe.style and recipe.style == mood:
            score *= 1.25
        score *= pace.get(category(recipe), 1.0)
        if history and history[-1][0] == recipe.shot:
            score *= 0.4
        if recipe.name in [name for _, name in history[-3:]]:
            score *= 0.6
        uses = sum(name == recipe.name for _, name in history)  # signature moves lose impact when repeated
        score *= (0.55 if recipe.horror else 0.9) ** uses
        scored.append((score, recipe))
    scored.sort(key=lambda s: -s[0])

    results = []
    for score, recipe in scored[: max(top * 3, 10)]:
        subject = beat.other["steamId"] if recipe.subject_is_other else subject_id
        params = dict(recipe.params)
        if hide:
            params["hide"] = hide
        if recipe.over_other:
            params["overTrack"] = tracks[beat.other["steamId"]]
        if recipe.shot == "stalker" and beat.label == "watched" and beat.other:
            # the real watcher's position: stalker vision from where the threat actually stands
            other = tracks[beat.other["steamId"]]
            i = _at(other, np.array([(beat.start + beat.end) // 2]))[0]
            params["pos"] = list(other.pos[i] + [0, 0, 58.0])
        try:
            result = cine.build(recipe.shot, tracks[subject], beat.start, beat.end, tickrate, params, geometry)
        except cine.ShotError:
            continue
        if result.visible < 0.9 or result.exposed > 0:  # 0.75 let a pan end on a wall (gap scene v2 shot 5)
            continue
        final = score * (0.5 + 0.5 * result.visible) * (1.0 - 0.4 * result.pulled)
        camera = {"shot": recipe.shot, "subject": tracks[subject].name, "technique": recipe.technique}
        camera.update({k: v for k, v in params.items() if k != "overTrack"})
        if recipe.over_other:
            camera["over"] = beat.other["name"]
        why = f"{recipe.technique}: {recipe.why}."
        results.append(Suggestion(recipe, camera, round(final, 3), why, result.visible, tracks[subject].name))
    results.sort(key=lambda s: -s.score)
    return results[:top]


def plan(tracks: dict[str, Track], subject_id: str, start: int, end: int, mood: str = "neutral", geometry=None,
         events: list[dict] | None = None, top: int = 3, tickrate: float = 64.0, hide: dict | None = None,
         while_hidden: str | None = None):
    """Analyze + segment + suggest. Returns [(beat, [suggestions])].

    hide: {"center": [x, y, z], "radius": r} that must never be on screen in any shot of the scene.
    while_hidden: steamId of the player the shots follow while the subject is inside that zone."""
    windows = analyze(tracks, subject_id, start, end, geometry, events, tickrate)
    if hide:
        sub = tracks[subject_id]
        for w in windows:
            i = _at(sub, np.array([w.tick + WINDOW_TICKS // 2]))[0]
            w.inside = bool(cine.in_zone(hide, sub.pos[i, :2])[0])
    beats = []
    for beat in segment(windows):
        if beat.label == "hidden" and while_hidden:
            # while the subject is in the hidden place, cover the other player instead (re-read from their side)
            for b in segment(analyze(tracks, while_hidden, beat.start, beat.end, geometry, events, tickrate)):
                b.subject = while_hidden
                b.other = None
                beats.append(b)
        else:
            beats.append(beat)
    history: list[tuple[str, str]] = []  # (shot type, recipe name) of each beat's top pick
    classes: list[str] = []               # pacing class of each beat's top pick
    out = []
    for beat in beats:
        options = suggest(beat, tracks, beat.subject or subject_id, mood, geometry, history, top, tickrate,
                          pacing(classes), hide)
        if options:
            history.append((options[0].recipe.shot, options[0].recipe.name))
            classes.append(category(options[0].recipe))
        out.append((beat, options))
    return out


# User preference (2026-09-27): "a very big preference for still, or slightly zooming camera shots, rather than
# shots pinned to the character that move with them. Every special shot is reserved for intense moments."
ANCHOR_SHARE = 0.7     # most beats are still / slowly zooming frames
SPECIAL_SHARE = 0.15   # attention-grabbing shots are rare
MAX_MOVES_IN_A_ROW = 1
MOVE_WEIGHT = 0.35     # camera that travels with the character: strongly discouraged
SPECIAL_MIN_TENSION = 2.0


def pacing(classes: list[str]) -> dict[str, float]:
    """Score multipliers per pacing class so the big shots stay rare and land: open on an anchor, never two
    specials in a row, a breather after every special, and enough anchors overall."""
    pace = {"anchor": 1.0, "move": MOVE_WEIGHT, "special": 1.0}
    n = len(classes)
    if n == 0:
        pace["anchor"] *= 1.4                      # open with an establishing, still frame
        pace["special"] *= 0.2
        return pace
    if classes[-1] == "special":
        pace["special"] *= 0.1                     # never two in a row
        pace["anchor"] *= 1.5                      # let it breathe
    if classes.count("special") / n >= SPECIAL_SHARE:
        pace["special"] *= 0.3
    if n >= 2 and classes.count("anchor") / n < ANCHOR_SHARE:
        pace["anchor"] *= 1.35
    if classes[-MAX_MOVES_IN_A_ROW:] == ["move"] * MAX_MOVES_IN_A_ROW:
        pace["move"] *= 0.55
        pace["anchor"] *= 1.3
    return pace
