"""Cinematic camera shots driven by per-tick player positions (film-style, not game-style cameras).

Every shot is built in four steps:
1. Subject motion: the player's per-tick path is smoothed like a camera operator would follow it (no strafe jitter),
   and a travel heading is derived from the smoothed velocity (view yaw when standing still).
2. Rig: the camera is placed relative to the subject (follow/lead/side/arc/crane/push) or on a tripod.
3. Collision: with map geometry, the camera is pulled in toward the subject when a wall is between them (pull in
   early, release slowly, like a spring arm), then the path is smoothed again.
4. Aim: the camera looks at the subject's chest with lead room in the direction of travel; optional handheld shake.

Angles around the subject ("angle", degrees) are measured from the subject's travel direction:
0 = in front (camera looks back at the subject), 180 = behind, 90 = subject's left, -90 = subject's right.
FOV is HLAE/Source horizontal FOV for a 4:3 frame (the game widens it for 16:9). UNVERIFIED on renders: tune by eye.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import gaussian_filter1d, minimum_filter1d

import campath
from positions import Track

KEY_STEP_TICKS = 4           # one campath keyframe every 4 ticks (16/s); HLAE interpolates cubically in between
PAD_SECONDS = 2.0            # extra motion before/after the shot so smoothing has context
PLAYER_HEIGHT = 72.0
CAMERA_MARGIN = 16.0         # keep the lens this far from walls
MIN_DISTANCE = 40.0
FRAME_CLEARANCE = 16.0        # + FRAME_CLEARANCE_SCALE * distance: half-width of the "thick" collision arm
FRAME_CLEARANCE_SCALE = 0.15

PRESET_ANGLES = {"front": 0.0, "back": 180.0, "left": 90.0, "right": -90.0,
                 "front-left": 45.0, "front-right": -45.0, "back-left": 135.0, "back-right": -135.0}

SHOT_DEFAULTS = {
    # kind: angle, distance, height (camera above the aim point), fov, heading smoothing (s), frame, lead
    "follow": dict(angle="back", distance=150.0, height=12.0, fov=70.0, headingSmooth=0.7, frame="heading", lead=0.0),
    "lead":   dict(angle="front", distance=170.0, height=6.0, fov=65.0, headingSmooth=0.7, frame="heading", lead=0.0),
    "side":   dict(angle="auto", distance=230.0, height=4.0, fov=55.0, headingSmooth=1.2, frame="heading", lead=0.18),
    "arc":    dict(angle="auto", distance=180.0, height=10.0, fov=65.0, headingSmooth=1.0, frame="heading", lead=0.0),
    "crane":  dict(angle="auto", distance=220.0, height=[-20.0, 220.0], fov=60.0, headingSmooth=1.0, frame="heading", lead=0.0),
    "push":   dict(angle="auto", distance=[420.0, 150.0], height=10.0, fov=55.0, headingSmooth=1.0, frame="world", lead=0.0),
    "pull":   dict(angle="auto", distance=[140.0, 420.0], height=30.0, fov=60.0, headingSmooth=1.0, frame="world", lead=0.0),
    "tripod": dict(angle="front-left", distance=450.0, height=20.0, fov=None, size="medium", operatorLag=0.35, lead=0.12),
}
SHOTS = set(SHOT_DEFAULTS)
# "angle": "auto" tries these and keeps the one with the clearest view (fewest wall pull-ins).
AUTO_ANGLES = {"follow": [180.0, 155.0, -155.0], "lead": [0.0, 25.0, -25.0], "side": [90.0, -90.0],
               "arc": [[150.0, 60.0], [-150.0, -60.0], [60.0, -30.0], [-60.0, 30.0], [120.0, 30.0], [-120.0, -30.0]], "crane": [45.0, -45.0, 135.0, -135.0],
               "push": [45.0, -45.0, 0.0, 20.0, -20.0], "pull": [0.0, 45.0, -45.0, 20.0, -20.0]}
SIZE_FRACTION = {"wide": 0.18, "full": 0.45, "medium": 0.7, "close": 1.1}  # subject height / frame height


class ShotError(ValueError):
    pass


@dataclass
class ShotResult:
    keys: list
    cam: np.ndarray              # (K, 3) camera positions at key times
    aim: np.ndarray              # (K, 3) look targets
    subject: np.ndarray          # (K, 3) smoothed subject feet
    visible: float               # fraction of keys with clear line of sight to the subject's chest
    pulled: float                # fraction of keys where collision pulled the camera in
    warnings: list = field(default_factory=list)


# --- helpers ------------------------------------------------------------------------------------------------------

def _angle(value) -> float:
    if isinstance(value, str):
        if value not in PRESET_ANGLES:
            raise ShotError(f"Unknown angle '{value}'. Use degrees or one of {sorted(PRESET_ANGLES)}.")
        return PRESET_ANGLES[value]
    return float(value)


def _ramp(value, f: np.ndarray, ease: bool = True) -> np.ndarray:
    """A number, or [start, end] eased over the shot (f = 0..1)."""
    if isinstance(value, (list, tuple)):
        a, b = (_angle(v) if isinstance(v, str) else float(v) for v in value)
        s = f * f * (3 - 2 * f) if ease else f
        return a + (b - a) * s
    return np.full_like(f, _angle(value) if isinstance(value, str) else float(value))


def _rot(vec: np.ndarray, degrees: np.ndarray) -> np.ndarray:
    r = np.radians(degrees)
    c, s = np.cos(r), np.sin(r)
    return np.stack([vec[:, 0] * c - vec[:, 1] * s, vec[:, 0] * s + vec[:, 1] * c], axis=1)


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-9)


def hfov_for_size(distance: float, size: str) -> float:
    """Horizontal 4:3 FOV that makes a standing player fill `size` of a 16:9 frame's height at `distance`."""
    frac = SIZE_FRACTION.get(size, 0.7)
    vfov = 2 * math.atan((PLAYER_HEIGHT / frac) / 2 / max(distance, 1.0))
    return float(np.clip(math.degrees(2 * math.atan(math.tan(vfov / 2) * 4 / 3)), 8.0, 110.0))


def _look(cam: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    d = target - cam
    yaw = np.degrees(np.arctan2(d[:, 1], d[:, 0]))
    pitch = -np.degrees(np.arctan2(d[:, 2], np.hypot(d[:, 0], d[:, 1])))
    return pitch, np.degrees(np.unwrap(np.radians(yaw)))


# --- subject motion -------------------------------------------------------------------------------------------------

@dataclass
class Subject:
    tick: np.ndarray
    feet: np.ndarray       # smoothed
    heading: np.ndarray    # (N, 2) unit travel direction (smoothed)
    aim: np.ndarray        # (N, 3) chest point
    speed: np.ndarray


def subject_motion(track: Track, tickrate: float, pos_smooth: float = 0.25, heading_smooth: float = 0.7,
                   aim_height: float = 50.0) -> Subject:
    if len(track.tick) < 8:
        raise ShotError(f"Not enough position samples for {track.name}.")
    feet = gaussian_filter1d(track.pos, sigma=pos_smooth * tickrate, axis=0, mode="nearest")
    vel = np.gradient(feet, axis=0) * tickrate
    speed = np.linalg.norm(vel[:, :2], axis=1)
    view = np.stack([np.cos(np.radians(track.yaw)), np.sin(np.radians(track.yaw))], axis=1)
    move = _unit(vel[:, :2])
    w = np.clip((speed - 30.0) / 90.0, 0.0, 1.0)[:, None]  # standing still -> use where the player looks
    heading = _unit(w * move + (1 - w) * view)
    heading = _unit(gaussian_filter1d(heading, sigma=heading_smooth * tickrate, axis=0, mode="nearest"))
    duck = gaussian_filter1d(track.duck, sigma=0.1 * tickrate, mode="nearest")
    aim = feet + np.stack([np.zeros_like(duck), np.zeros_like(duck), aim_height * (1 - 0.3 * duck)], axis=1)
    return Subject(track.tick, feet, heading, aim, speed)


# --- collision --------------------------------------------------------------------------------------------------

def _resolve(geometry, aim: np.ndarray, cam: np.ndarray, tickrate_keys: float) -> tuple[np.ndarray, np.ndarray]:
    """Pull the camera toward the aim point where geometry blocks it. Returns (camera, pulled mask)."""
    if geometry is None:
        return cam, np.zeros(len(cam), bool)
    offset = cam - aim
    dist = np.linalg.norm(offset, axis=1)
    direction = offset / np.maximum(dist, 1e-9)[:, None]
    allowed = dist.copy()
    up = np.array([0.0, 0.0, 1.0])
    for i in range(len(cam)):
        # A "thick" arm: besides the centre line, test lines to points beside/above the lens. Walls or door frames
        # that would fill the edges of the picture (camera following through a doorway) pull the camera in too.
        side = np.cross(direction[i], up)
        side /= max(np.linalg.norm(side), 1e-9)
        width = FRAME_CLEARANCE + FRAME_CLEARANCE_SCALE * dist[i]
        reach = dist[i] + CAMERA_MARGIN
        for lateral, vertical in ((0, 0), (1, 0), (-1, 0), (0, 0.6), (0, -0.4)):
            end = aim[i] + direction[i] * reach + side * lateral * width + up * vertical * width
            hit = geometry.first_hit(aim[i], end, see_through_blocks=True)
            if hit is not None:
                allowed[i] = min(allowed[i], max(MIN_DISTANCE, hit * reach - CAMERA_MARGIN))
    pulled = allowed < dist - 1.0
    if pulled.any():
        # Pull in ~0.4 s early and let go slowly, then smooth: a spring arm, not a pop.
        window = max(3, int(0.8 * tickrate_keys) | 1)
        smooth = minimum_filter1d(allowed, size=window, mode="nearest")
        smooth = gaussian_filter1d(smooth, sigma=0.25 * tickrate_keys, mode="nearest")
        smooth = np.minimum(smooth, allowed + 4.0)
        cam = aim + direction * smooth[:, None]
    return cam, pulled


# --- tripod placement -------------------------------------------------------------------------------------------

def place_tripod(geometry, sub: Subject, params: dict) -> np.ndarray:
    """Choose a fixed camera position that sees the subject for the whole shot."""
    aim = sub.aim
    centroid = aim.mean(axis=0)
    # The centroid of a path that turns a corner can be inside a wall: use the path point nearest to it.
    centroid = aim[np.argmin(np.linalg.norm(aim - centroid, axis=1))]
    travel = aim[-1, :2] - aim[0, :2]
    base = _unit(travel[None])[0] if np.linalg.norm(travel) > 64 else sub.heading[len(sub.heading) // 2]
    want_angle = _angle(params.get("angle", "front-left"))
    want_dist = float(params.get("distance", 450.0))
    height = float(params.get("height", 20.0))
    samples = aim[:: max(1, len(aim) // 40)]
    best, best_score = None, -1e9
    for d_angle in (0, 15, -15, 30, -30, 45, -45, 60, -60, 90, -90, 120, -120, 180):
        angle = want_angle + d_angle
        direction = _rot(base[None], np.array([angle]))[0]
        for scale in (1.0, 0.75, 1.3, 0.55, 1.7):
            for dz in (height, height + 60.0, height + 140.0):
                cam = np.r_[centroid[:2] + direction * want_dist * scale, centroid[2] + dz]
                if geometry is None:
                    return cam
                floor = geometry.floor_z(*cam, depth=2000.0)
                if floor is None or cam[2] - floor < 24.0:
                    continue
                if not geometry.clear(centroid, cam):
                    continue
                visible = np.mean([geometry.clear(cam, p) for p in samples])
                score = visible * 10 - abs(d_angle) / 90.0 - abs(math.log(scale)) - (dz - height) / 200.0
                if score > best_score:
                    best, best_score = cam, score
                if visible == 1.0 and d_angle == 0 and scale == 1.0:
                    return cam
    if best is None:
        raise ShotError("No tripod position with a view of the subject was found; give \"pos\" explicitly.")
    return best


# --- placed shots: the camera has its own position/motion, not glued to the subject ------------------------------

def _basis(pitch: float, yaw: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """forward, right, up of a camera (Source angles: pitch + = down, yaw 0 = +x)."""
    p, y = math.radians(pitch), math.radians(yaw)
    forward = np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p)])
    right = np.array([math.sin(y), -math.cos(y), 0.0])
    up = np.cross(right, forward)
    return forward, right, up


def h43_from_h169(h169: float) -> float:
    return math.degrees(2 * math.atan(math.tan(math.radians(h169) / 2) * 0.75))


def fit_fov(cam: np.ndarray, pitch: float, yaw: float, points: np.ndarray, margin: float = 1.15) -> float:
    """4:3 horizontal FOV that keeps every point inside a 16:9 frame from a fixed camera."""
    forward, right, up = _basis(pitch, yaw)
    rel = points - cam
    z = np.maximum(rel @ forward, 1.0)
    tan_h = np.abs(rel @ right / z).max()
    tan_v = np.abs(rel @ up / z).max()
    need = max(tan_h, tan_v * 16 / 9) * margin
    return h43_from_h169(math.degrees(2 * math.atan(need)))


def _travel(sub: Subject) -> np.ndarray:
    travel = sub.feet[-1, :2] - sub.feet[0, :2]
    return _unit(travel[None])[0] if np.linalg.norm(travel) > 64 else _unit(sub.heading.mean(axis=0)[None])[0]


def _clear_fraction(geometry, cams: np.ndarray, targets: np.ndarray) -> float:
    if geometry is None:
        return 1.0
    step = max(1, len(cams) // 30)
    return float(np.mean([geometry.clear(cams[i], targets[i]) for i in range(0, len(cams), step)]))


def _in_frame(cam, pitch, yaw, h43, points) -> np.ndarray:
    """Which points are inside a 16:9 frame (with a small safety margin)."""
    forward, right, up = _basis(pitch, yaw)
    rel = points - cam
    z = rel @ forward
    tan_h = math.tan(math.radians(h43) / 2) / 0.75 * 0.92
    tan_v = tan_h * 9 / 16
    zs = np.maximum(z, 1e-6)
    return (z > 1) & (np.abs(rel @ right) / zs < tan_h) & (np.abs(rel @ up) / zs < tan_v)


def place_static(geometry, sub, p) -> np.ndarray:
    """A locked-off spot that sees all of the action with the narrowest lens: back off far enough and high enough."""
    lo, hi = sub.aim.min(axis=0), sub.aim.max(axis=0)
    centre = (lo + hi) / 2
    extent = max(float(np.linalg.norm(hi[:2] - lo[:2])), 200.0)
    base = _travel(sub)
    want = _angle(p.get("angle", "front-left"))
    samples = sub.aim[:: max(1, len(sub.aim) // 30)]
    pts = np.vstack([sub.feet, sub.feet + [0, 0, PLAYER_HEIGHT]])
    anchor = sub.aim[np.argmin(np.linalg.norm(sub.aim - centre, axis=1))]
    best, best_score = None, -1e9
    for d_angle in (0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180):
        direction = _rot(base[None], np.array([want + d_angle]))[0]
        for scale in (1.0, 1.5, 2.2, 3.0):
            for dz in (60.0, 180.0, 360.0):
                dist = max(float(p.get("distance", 650.0)) * 0.6, extent * 0.6 * scale)
                cam = np.r_[centre[:2] + direction * dist, centre[2] + dz]
                if geometry is not None:
                    floor = geometry.floor_z(*cam, depth=3000.0)
                    if floor is None or cam[2] - floor < 24.0 or not geometry.clear(anchor, cam):
                        continue
                    visible = np.mean([geometry.clear(cam, s) for s in samples])
                else:
                    visible = 1.0
                pitch, yaw = campath.look_at(tuple(cam), tuple(centre))
                fov = fit_fov(cam, pitch, yaw, pts, float(p.get("margin", 1.15)))
                score = visible * 10 - max(fov - 60.0, 0) / 15 - abs(d_angle) / 180 - scale / 10
                if score > best_score:
                    best, best_score = cam, score
    if best is None:
        raise ShotError("No locked-off position sees the action; give \"pos\" explicitly.")
    return best


def shot_static(geometry, sub, p, f, kps):
    """Locked-off camera: no pan, no zoom. The lens is chosen so the whole action stays in frame."""
    cam = np.asarray(p["pos"], float) if p.get("pos") else place_static(geometry, sub, p)
    lo, hi = sub.aim.min(axis=0), sub.aim.max(axis=0)
    centre = (lo + hi) / 2
    pitch, yaw = (float(v) for v in campath.look_at(tuple(cam), tuple(centre)))
    pts = np.vstack([sub.feet, sub.feet + [0, 0, PLAYER_HEIGHT]])
    fov = fit_fov(cam, pitch, yaw, pts, float(p.get("margin", 1.15)))
    warnings = []
    if fov > 100:
        warnings.append(f"the action is too wide for one locked-off frame (needs FOV {fov:.0f}); the subject leaves "
                        "the frame. Use a shorter clip or a pan (tripod).")
        fov = 100.0
    n = len(f)
    return np.tile(cam, (n, 1)), np.full(n, pitch), np.full(n, yaw), np.full(n, max(fov, 15.0)), warnings


def shot_overhead(geometry, sub, p, f, kps):
    """Bird's-eye view straight down. The subject travels 'up' the frame. Optional slow track and spin."""
    n = len(f)
    travel_yaw = math.degrees(math.atan2(*_travel(sub)[::-1]))
    h169 = float(p.get("fov169", 60.0))
    vfov = 2 * math.atan(math.tan(math.radians(h169) / 2) * 9 / 16)
    if p.get("track"):
        xy = gaussian_filter1d(sub.feet[:, :2], sigma=1.5 * kps, axis=0, mode="nearest")
        radius = 160.0
    else:
        centre = (sub.feet[:, :2].min(axis=0) + sub.feet[:, :2].max(axis=0)) / 2
        xy = np.tile(centre, (n, 1))
        radius = float(np.linalg.norm(sub.feet[:, :2] - centre, axis=1).max()) + 64.0
    height = float(p["height"]) if p.get("height") else radius / math.tan(vfov / 2) * float(p.get("margin", 1.2))
    height = float(np.clip(height, 250.0, float(p.get("maxHeight", 2200.0))))
    top = sub.feet[:, 2].max()
    warnings = []
    if geometry is not None:  # a roof or ceiling above the action lowers the camera
        probe = np.r_[xy[n // 2], top + 64]
        hit = geometry.first_hit(probe, probe + [0, 0, height], see_through_blocks=True)
        if hit is not None:
            height = max(120.0, hit * height + 64 - CAMERA_MARGIN)
            warnings.append(f"ceiling above the action: overhead camera lowered to {height:.0f} units")
    cam = np.c_[xy, np.full(n, top + height)]
    yaw = travel_yaw + _ramp(p.get("spin", 0.0), f)
    return cam, np.full(n, 89.0), yaw, np.full(n, h43_from_h169(h169)), warnings


def shot_drone(geometry, sub, p, f, kps):
    """Sweeping aerial move with its own path (flyover / orbit / rise). The camera only loosely keeps the subject
    in view (heavy lag), like a drone pilot, not a camera glued to the player."""
    d = _travel(sub)
    side = np.array([-d[1], d[0]])
    move = p.get("move", "flyover")
    start, end = sub.feet[0], sub.feet[-1]
    centre = sub.feet.mean(axis=0)
    base_z = sub.feet[:, 2].max()
    lag_target = gaussian_filter1d(sub.aim, sigma=float(p.get("lag", 1.0)) * kps, axis=0, mode="nearest")
    ease = f * f * (3 - 2 * f)

    def path(altitude, sign):
        offset = float(p.get("offset", 250.0)) * sign
        dist = float(p.get("distance", 900.0))
        if move == "flyover":
            a = np.r_[start[:2] - d * dist * 0.5 + side * offset, base_z + altitude]
            b = np.r_[end[:2] + d * dist * 0.35 + side * offset * 0.4, base_z + altitude * 0.85]
            return a + (b - a) * ease[:, None]
        if move == "orbit":
            radius = dist * 0.6
            start_angle = math.degrees(math.atan2(-d[1], -d[0])) + (30.0 * sign)
            angles = np.radians(start_angle + sign * float(p.get("degrees", 90.0)) * ease)
            return np.c_[centre[0] + radius * np.cos(angles), centre[1] + radius * np.sin(angles),
                         np.full(len(f), base_z + altitude)]
        if move == "rise":
            a = np.r_[start[:2] - d * 220 + side * offset * 0.3, start[2] + 40]
            b = np.r_[start[:2] - d * 320 + side * offset * 0.5, base_z + altitude]
            return a + (b - a) * ease[:, None]
        raise ShotError(f"Unknown drone move '{move}'. Use flyover, orbit or rise.")

    best, best_score = None, -1e9
    for altitude in (float(p.get("altitude", 380.0)), float(p.get("altitude", 380.0)) + 200, float(p.get("altitude", 380.0)) + 450):
        for sign in (1.0, -1.0):
            cams = path(altitude, sign)
            score = _clear_fraction(geometry, cams, lag_target) - altitude / 5000
            if geometry is not None:  # the flight path itself must not pass through geometry
                hits = sum(not geometry.clear(cams[i], cams[i + 4]) for i in range(0, len(cams) - 4, 4))
                score -= hits * 0.2
            if score > best_score:
                best, best_score = cams, score
    cam = gaussian_filter1d(best, sigma=0.2 * kps, axis=0, mode="nearest")
    pitch, yaw = _look(cam, lag_target)
    fov = _ramp(p.get("fov", 70.0), f)
    seen = _clear_fraction(geometry, cam, lag_target)
    warnings = [] if seen > 0.85 else [f"drone loses sight of the subject for {100 * (1 - seen):.0f}% of the shot"]
    return cam, pitch, yaw, fov, warnings


def shot_ground(geometry, sub, p, f, kps):
    """Camera resting on the floor, locked off. 'away': the subject runs away from it; 'toward': runs at it."""
    n = len(f)
    facing = p.get("facing", "away")
    fov = float(p.get("fov", 80.0))
    warnings = []

    def setup(anchor_index: int, side_offset: float):
        anchor = sub.feet[anchor_index]
        if facing == "away":  # look down the route the subject is about to run
            look_index = anchor_index + int((n - 1 - anchor_index) * float(p.get("lookAhead", 0.45)))
        else:                 # camera waits ahead; the subject runs at it
            look_index = int(anchor_index * (1 - float(p.get("lookAhead", 0.45))))
        direction = sub.feet[look_index, :2] - anchor[:2]
        if np.linalg.norm(direction) < 32:
            direction = _travel(sub) * (1 if facing == "away" else -1)
        direction = _unit(direction[None])[0]
        side = np.array([-direction[1], direction[0]])
        xy = anchor[:2] - direction * float(p.get("back", 48.0)) + side * side_offset
        floor = anchor[2]
        if geometry is not None:
            found = geometry.floor_z(xy[0], xy[1], anchor[2] + 64, depth=400)
            if found is None:
                return None
            floor = found
        cam = np.r_[xy, floor + float(p.get("lens", 6.0))]
        target = sub.feet[look_index] + [0, 0, float(p.get("targetHeight", 36.0))]
        pitch, yaw = campath.look_at(tuple(cam), tuple(target))
        return cam, float(pitch), float(yaw)

    # Where along the route to put the camera: the spot from which the subject stays visible and in frame longest
    # ('away': after passing the camera; 'toward': while approaching it).
    fractions = (0.0, 0.1, 0.2, 0.3, 0.4) if facing == "away" else (1.0, 0.9, 0.8, 0.7, 0.6)
    best, best_score = None, -1e9
    for frac in fractions:
        anchor_index = int((n - 1) * frac)
        for side_offset in (float(p.get("side", 14.0)), -float(p.get("side", 14.0)), 48.0, -48.0):
            placed = setup(anchor_index, side_offset)
            if placed is None:
                continue
            cam, pitch, yaw = placed
            framed = _in_frame(cam, pitch, yaw, fov, sub.aim)
            if geometry is not None:
                step = max(1, n // 40)
                seen = np.zeros(n, bool)
                seen[::step] = [framed[i] and geometry.clear(cam, sub.aim[i]) for i in range(0, n, step)]
                score = seen[::step].mean()
            else:
                score = framed.mean()
            score -= abs(frac - fractions[0]) * 0.3  # prefer the start (or end) of the clip
            if score > best_score:
                best, best_score = placed, score
    if best is None:
        raise ShotError("No floor found for a ground camera along the route.")
    cam, pitch, yaw = best
    if best_score < 0.6:
        warnings.append("the subject is in view for under 60% of the ground shot (route turns away); "
                        "use a straighter stretch or a shorter clip")
    return np.tile(cam, (n, 1)), np.full(n, pitch), np.full(n, yaw), np.full(n, fov), warnings


PLACED_SHOTS = {"static": shot_static, "overhead": shot_overhead, "drone": shot_drone, "ground": shot_ground}
SHOT_DEFAULTS.update({
    "static": dict(angle="front-left", distance=650.0, height=60.0, margin=1.15),
    "overhead": dict(fov169=60.0, margin=1.2, spin=0.0),
    "drone": dict(move="flyover", altitude=380.0, distance=900.0, offset=250.0, lag=1.0, fov=70.0),
    "ground": dict(facing="away", back=48.0, side=14.0, lens=6.0, fov=80.0, lookAhead=0.45),
})
SHOTS.update(PLACED_SHOTS)


# --- shot builder ------------------------------------------------------------------------------------------------

def build(kind: str, track: Track, start_tick: int, end_tick: int, tickrate: float, params: dict | None = None,
          geometry=None) -> ShotResult:
    if kind not in SHOT_DEFAULTS:
        raise ShotError(f"Unknown cinematic shot '{kind}'. Use one of {sorted(SHOTS)}.")
    p = {**SHOT_DEFAULTS[kind], **(params or {})}
    pad = int(PAD_SECONDS * tickrate)
    window = track.window(start_tick - pad, end_tick + pad)
    if len(window.tick) == 0 or window.tick[0] > start_tick or window.tick[-1] < end_tick:
        raise ShotError(f"{track.name} has no positions for the whole tick range {start_tick}-{end_tick}.")
    warnings = []
    jumps = [int(t) for t in window.teleports() if start_tick <= t <= end_tick]
    if jumps:
        warnings.append(f"{track.name} teleports at ticks {jumps}: split the shot there (hard cut).")
    if not window.window(start_tick, end_tick).alive.all():
        warnings.append(f"{track.name} is dead during part of the shot.")

    sub_full = subject_motion(window, tickrate, heading_smooth=p.get("headingSmooth", 0.7),
                              aim_height=float(p.get("aimHeight", 50.0)))
    key_ticks = np.arange(start_tick, end_tick + 1, KEY_STEP_TICKS)
    if key_ticks[-1] != end_tick:
        key_ticks = np.r_[key_ticks, end_tick]
    idx = np.searchsorted(sub_full.tick, key_ticks).clip(0, len(sub_full.tick) - 1)
    sub = Subject(key_ticks, sub_full.feet[idx], sub_full.heading[idx], sub_full.aim[idx], sub_full.speed[idx])
    t = (key_ticks - start_tick) / tickrate
    f = t / max(t[-1], 1e-9)
    keys_per_second = tickrate / KEY_STEP_TICKS

    if kind in PLACED_SHOTS:
        cam, pitch, yaw, fov, extra = PLACED_SHOTS[kind](geometry, sub, p, f, keys_per_second)
        warnings += extra
        roll = _ramp(p.get("roll", 0.0), f)
        keys = [campath.Key(float(t[i]), *map(float, cam[i]), float(pitch[i]), float(yaw[i]), float(roll[i]),
                            float(fov[i])) for i in range(len(t))]
        visible = 1.0 if geometry is None else float(np.mean([geometry.clear(cam[i], sub.aim[i]) for i in range(len(t))]))
        if visible < 0.9:
            warnings.append(f"subject hidden behind geometry in {100 * (1 - visible):.0f}% of the shot")
        return ShotResult(keys, cam, sub.aim, sub.feet, visible, 0.0, warnings)

    if kind == "tripod":
        cam = np.tile(np.asarray(p["pos"], float) if p.get("pos") else place_tripod(geometry, sub, p), (len(t), 1))
        pulled = np.zeros(len(t), bool)
        lag = float(p.get("operatorLag", 0.35)) * keys_per_second
        aim = gaussian_filter1d(sub.aim, sigma=max(lag, 0.1), axis=0, mode="nearest")
        heading = sub.heading
    else:
        distance = _ramp(p["distance"], f)
        height = _ramp(p["height"], f)
        heading = sub.heading
        if p.get("frame") == "world":
            # Push-in / pull-out: one fixed direction for the whole move, the subject's overall travel direction
            # (the heading at the first frame can still be mid-turn and point into a wall).
            travel = sub.feet[-1, :2] - sub.feet[0, :2]
            overall = _unit(travel[None])[0] if np.linalg.norm(travel) > 64 else _unit(sub.heading.mean(axis=0)[None])[0]
            heading = np.tile(overall, (len(t), 1))

        moving_distance = isinstance(p["distance"], (list, tuple))  # push-in / pull-out / custom dolly

        def rig(angle_value):
            direction = _rot(heading, _ramp(angle_value, f))
            scaled = distance
            wanted = np.c_[sub.aim[:, :2] + direction * scaled[:, None], sub.aim[:, 2] + height]
            placed, pulled_mask = _resolve(geometry, sub.aim, wanted, keys_per_second)
            if moving_distance and pulled_mask.any():
                # A wall would cut the move short (a push-in that starts pulled in reads as pull-out + push-in).
                # Shrink the whole move to fit instead, keeping its shape, then keep it one-directional.
                got = np.linalg.norm(placed - sub.aim, axis=1)
                scale = float(np.clip((got / np.maximum(distance, 1.0)).min(), 0.35, 1.0))
                scaled = distance * scale
                wanted = np.c_[sub.aim[:, :2] + direction * scaled[:, None], sub.aim[:, 2] + height]
                placed, pulled_mask = _resolve(geometry, sub.aim, wanted, keys_per_second)
                got = np.linalg.norm(placed - sub.aim, axis=1)
                mono = np.minimum.accumulate(got) if distance[-1] < distance[0] else np.minimum.accumulate(got[::-1])[::-1]
                placed = sub.aim + (placed - sub.aim) / np.maximum(got, 1e-9)[:, None] * mono[:, None]
            placed = gaussian_filter1d(placed, sigma=0.12 * keys_per_second, axis=0, mode="nearest")
            lost = np.linalg.norm(wanted - placed, axis=1).mean() / max(float(distance.mean()), 1.0)
            return placed, pulled_mask, lost

        if p["angle"] == "auto":
            options = [(rig(a), a) for a in AUTO_ANGLES.get(kind, [180.0])]
            (cam, pulled, _), chosen = min(options, key=lambda o: o[0][2])
            p["angle"] = chosen
        else:
            cam, pulled, _ = rig(p["angle"])
        aim = sub.aim

    # Lead room: aim a little ahead of the subject so they sit off-center, with space in front of them.
    dist_now = np.linalg.norm(aim - cam, axis=1)
    lead = float(p.get("lead", 0.0))
    look_at = aim + np.c_[heading * (lead * dist_now)[:, None], np.zeros(len(t))]
    pitch, yaw = _look(cam, look_at)
    pitch = gaussian_filter1d(pitch, sigma=0.08 * keys_per_second, mode="nearest")
    yaw = gaussian_filter1d(yaw, sigma=0.08 * keys_per_second, mode="nearest")
    roll = _ramp(p.get("roll", 0.0), f)

    if p.get("handheld"):
        amount = 1.0 if p["handheld"] is True else float(p["handheld"])
        rng = np.random.default_rng(int(p.get("seed", 7)))
        for arr, amp in ((pitch, 0.35), (yaw, 0.5), (roll, 0.25)):
            for freq in (0.23, 0.61, 1.3):
                arr += amount * amp / (1 + freq) * np.sin(2 * np.pi * (freq * t + rng.random()))

    if p.get("fov") is None:
        fov = np.full(len(t), hfov_for_size(float(np.median(dist_now)), p.get("size", "medium")))
    else:
        fov = _ramp(p["fov"], f)

    keys = [campath.Key(float(t[i]), *map(float, cam[i]), float(pitch[i]), float(yaw[i]), float(roll[i]), float(fov[i]))
            for i in range(len(t))]

    visible = 1.0
    if geometry is not None:
        visible = float(np.mean([geometry.clear(cam[i], aim[i]) for i in range(len(t))]))
        if visible < 0.9:
            warnings.append(f"subject hidden behind geometry in {100 * (1 - visible):.0f}% of the shot")
    if pulled.mean() > 0.3:
        warnings.append(f"walls pulled the camera in for {100 * pulled.mean():.0f}% of the shot; "
                        "try another angle or a shorter distance")
    return ShotResult(keys, cam, aim, sub.feet, visible, float(pulled.mean()), warnings)


def preview(result: ShotResult, png_path, geometry=None, title: str = "") -> None:
    """Top-down plot: walls (grey), subject path (blue), camera path (red), sight lines every second."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = np.vstack([result.cam[:, :2], result.subject[:, :2]])
    lo, hi = pts.min(axis=0) - 400, pts.max(axis=0) + 400
    fig, ax = plt.subplots(figsize=(8, 8 * (hi[1] - lo[1]) / max(hi[0] - lo[0], 1)), dpi=110)
    if geometry is not None:
        walls = geometry.outline(bounds=(lo, hi))
        ax.scatter(walls[:, 0], walls[:, 1], s=0.3, c="0.6", linewidths=0)
    ax.plot(result.subject[:, 0], result.subject[:, 1], c="tab:blue", lw=2, label="subject")
    ax.plot(result.cam[:, 0], result.cam[:, 1], c="tab:red", lw=2, label="camera")
    step = max(1, len(result.keys) // max(1, int(result.keys[-1].t)))
    for i in range(0, len(result.keys), step):
        ax.plot([result.cam[i, 0], result.aim[i, 0]], [result.cam[i, 1], result.aim[i, 1]], c="tab:red", lw=0.5, alpha=0.5)
    ax.scatter(*result.subject[0, :2], c="tab:blue", marker="o", zorder=5)
    ax.scatter(*result.cam[0, :2], c="tab:red", marker="o", zorder=5)
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_aspect("equal")
    ax.set_title(f"{title}  visible {100 * result.visible:.0f}%  pulled {100 * result.pulled:.0f}%", fontsize=9)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(png_path)
    plt.close(fig)
