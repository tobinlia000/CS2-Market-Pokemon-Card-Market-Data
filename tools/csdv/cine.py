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
from scipy.ndimage import gaussian_filter1d, maximum_filter1d, minimum_filter1d

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
    exposed: float = 0.0         # fraction of keys where a "hide" zone is on screen (must be 0 for a valid shot)


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
                if not geometry.clear(centroid, cam) or sees_zone(geometry, cam, params):
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


# --- handheld ---------------------------------------------------------------------------------------------------

HANDHELD_KEY_STEP_TICKS = 2   # 32 keys/s so the fast jitter survives HLAE's interpolation
STEP_HZ = 1.9                 # operator footsteps while the camera moves


def apply_handheld(amount, t, kps, cam, pitch, yaw, roll, seed: int = 7):
    """Handheld camera: slow operator drift + fast jitter on the angles, a small positional wander, and a footstep
    bob/sway while the camera itself travels. amount: True/1 = documentary, 0.5 = subtle, 2 = frantic."""
    amount = 1.0 if amount is True else float(amount)
    rng = np.random.default_rng(seed)
    n = len(t)

    def noise(sigma_seconds: float, amplitude: float) -> np.ndarray:
        raw = rng.standard_normal(n + 400)
        smooth = gaussian_filter1d(raw, sigma=max(sigma_seconds * kps, 0.6), mode="wrap")[200:200 + n]
        return smooth / max(float(smooth.std()), 1e-9) * amplitude

    pitch = pitch + amount * (noise(0.45, 0.7) + noise(0.07, 0.18))
    yaw = yaw + amount * (noise(0.5, 0.9) + noise(0.07, 0.22))
    roll = roll + amount * (noise(0.6, 0.6) + noise(0.1, 0.12))

    speed = np.linalg.norm(np.gradient(cam, axis=0), axis=1) * kps
    walking = gaussian_filter1d(np.clip(speed / 120.0, 0.0, 1.0), sigma=0.3 * kps, mode="nearest")
    phase = 2 * np.pi * np.cumsum(STEP_HZ * walking / kps)
    heading = np.gradient(cam[:, :2], axis=0)
    side = np.c_[-heading[:, 1], heading[:, 0]] / np.maximum(np.linalg.norm(heading, axis=1), 1e-9)[:, None]
    cam = cam + amount * np.c_[noise(0.8, 1.5), noise(0.8, 1.5), noise(0.8, 1.0)]
    cam[:, 2] += amount * walking * 1.4 * np.sin(phase)
    cam[:, :2] += side * (amount * walking * 0.8 * np.sin(phase / 2))[:, None]
    yaw = yaw + amount * walking * 0.3 * np.sin(phase / 2)
    return cam, pitch, yaw, roll


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


def _in_frame(cam, pitch, yaw, h43, points, margin: float = 0.92) -> np.ndarray:
    """Which points are inside a 16:9 frame. margin < 1 = well inside (for subjects); use margin > 1 when checking
    that something is OUT of frame, since the points are body centrelines and a shoulder can poke in first."""
    forward, right, up = _basis(pitch, yaw)
    rel = points - cam
    z = rel @ forward
    tan_h = math.tan(math.radians(h43) / 2) / 0.75 * margin
    tan_v = tan_h * 9 / 16
    zs = np.maximum(z, 1e-6)
    return (z > 1) & (np.abs(rel @ right) / zs < tan_h) & (np.abs(rel @ up) / zs < tan_v)


# --- hidden zones: a place that must never be on screen (e.g. the nook a player hides in) --------------------------

def nook_zone(geometry, spot, enclosed_max: float = 200.0, rays: int = 72) -> dict:
    """The hidden place around a spot, measured from the geometry: its walls where there are walls, and its mouth
    where it opens. Rays at chest height; the pocket depth = the farthest nearby wall (< enclosed_max), and open
    directions are cut there, so the zone is the pocket up to its mouth line. Returns a JSON-able zone."""
    spot = np.asarray(spot, float)
    eye = spot + [0, 0, 40.0]
    dists = []
    for k in range(rays):
        a = 2 * math.pi * k / rays
        ray = np.array([math.cos(a), math.sin(a), 0.0]) * 600.0
        hit = geometry.first_hit(eye, eye + ray, see_through_blocks=True) if geometry is not None else None
        dists.append(600.0 if hit is None else hit * 600.0)
    dists = np.array(dists)
    walls = dists[dists < enclosed_max]
    depth = float(np.clip(walls.max() if len(walls) else 96.0, 60.0, enclosed_max))
    reach = np.minimum(dists, depth)
    angles = 2 * np.pi * np.arange(rays) / rays
    polygon = [[round(float(spot[0] + r * math.cos(a)), 1), round(float(spot[1] + r * math.sin(a)), 1)]
               for a, r in zip(angles, reach)]
    ceiling = geometry.first_hit(eye, eye + [0, 0, 400.0], see_through_blocks=True) if geometry is not None else None
    height = float(min(400.0 * ceiling + 40.0 if ceiling is not None else 180.0, 180.0))
    return {"polygon": polygon, "floor": float(spot[2]), "height": round(height, 1), "center": spot.round(1).tolist(),
            "radius": round(depth, 1)}


def in_zone(zone: dict, xy) -> np.ndarray:
    """Which xy points are inside the zone (polygon zones; circle zones use center + radius)."""
    xy = np.atleast_2d(np.asarray(xy, float))[:, :2]
    if zone.get("polygon"):
        from matplotlib.path import Path as _Path
        return _Path(np.asarray(zone["polygon"])).contains_points(xy)
    c = np.asarray(zone["center"], float)
    return np.linalg.norm(xy - c[:2], axis=1) < float(zone.get("radius", 32.0))


def hide_points(zone: dict) -> np.ndarray:
    """Sample points filling a hidden zone: for polygon zones a 12-unit grid over the whole pocket plus points just
    in front of its walls, from floor to near the ceiling; for circle zones the spot and a ring around it."""
    if zone.get("polygon"):
        poly = np.asarray(zone["polygon"], float)
        floor = float(zone["floor"])
        heights = np.linspace(4.0, max(float(zone.get("height", 150.0)) - 10.0, 20.0), 4)
        lo, hi = poly.min(axis=0), poly.max(axis=0)
        gx, gy = np.meshgrid(np.arange(lo[0], hi[0] + 1, 16.0), np.arange(lo[1], hi[1] + 1, 16.0))
        grid = np.c_[gx.ravel(), gy.ravel()]
        grid = grid[in_zone(zone, grid)]
        centre = np.asarray(zone["center"], float)[:2]
        edge = poly + (centre - poly) / np.maximum(np.linalg.norm(centre - poly, axis=1), 1e-6)[:, None] * 4.0
        flat = np.vstack([grid, edge[::2]])
        return np.array([[x, y, floor + h] for x, y in flat for h in heights])
    c = np.asarray(zone["center"], float)
    r = float(zone.get("radius", 32.0))
    ring = [(0.0, 0.0)] + [(r * math.cos(a), r * math.sin(a)) for a in np.radians(np.arange(0, 360, 45))]
    return np.array([[c[0] + dx, c[1] + dy, c[2] + h] for dx, dy in ring for h in (10.0, 38.0, 66.0)])


def sees_zone(geometry, cam: np.ndarray, p: dict) -> bool:
    """True if a camera position has a clear line of sight to any point of the hidden zone (conservative: a
    placement that can see into it at all is rejected, whatever it is aimed at)."""
    pts = p.get("_hide_pts")
    if pts is None or geometry is None:
        return False
    return any(geometry.clear(cam, q) for q in pts)


_LOS_CACHE: dict = {}


def _visible_points(geometry, cam, pts, candidates) -> bool:
    """Is any of pts[candidates] in clear line of sight from cam? Cached per (camera spot, zone)."""
    key = (round(float(cam[0])), round(float(cam[1])), round(float(cam[2])), id(pts), len(pts))
    cache = _LOS_CACHE.setdefault(key, {})
    for j in np.flatnonzero(candidates):
        if j not in cache:
            cache[j] = geometry.clear(cam, pts[j])
        if cache[j]:
            return True
    return False


def zone_exposure(geometry, cam, pitch, yaw, fov, pts) -> np.ndarray:
    """Per key: is any hidden-zone point inside the frame AND not blocked by geometry?"""
    exposed = np.zeros(len(cam), bool)
    for i in range(len(cam)):
        framed = _in_frame(cam[i], float(pitch[i]), float(yaw[i]), float(fov[i]) * 1.08, pts)
        if framed.any():
            exposed[i] = geometry is None or _visible_points(geometry, cam[i], pts, framed)
    return exposed


def view_shows_zone(geometry, cam, pitch, yaw, fov, p: dict) -> bool:
    """Exact check for a locked-off view: is any hidden-zone point on screen?"""
    pts = p.get("_hide_pts")
    if pts is None:
        return False
    return bool(zone_exposure(geometry, np.atleast_2d(cam), [pitch], [yaw], [fov], pts)[0])


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
                if geometry is not None and view_shows_zone(geometry, cam, pitch, yaw, min(fov, 100.0), p):
                    continue  # the locked-off frame would show the hidden place
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
    fov_track = max(fov, 15.0) * _ramp(p.get("zoom", 1.0), f)   # "zoom": [1.0, 0.8] = slow creep in, camera still
    return np.tile(cam, (n, 1)), np.full(n, pitch), np.full(n, yaw), fov_track, warnings


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
                if p.get("_hide_pts") is not None:
                    score -= 5.0 * np.mean([sees_zone(geometry, cams[i], p) for i in range(0, len(cams), 4)])
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
        if view_shows_zone(geometry, cam, float(pitch), float(yaw), fov, p):
            return None
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
    return np.tile(cam, (n, 1)), np.full(n, pitch), np.full(n, yaw), fov * _ramp(p.get("zoom", 1.0), f), warnings


def shot_dolly_zoom(geometry, sub, p, f, kps):
    """Vertigo / dolly zoom: the camera moves along a fixed line while the lens zooms the other way, so the subject
    keeps its size and the background stretches (dolly in + zoom out) or compresses (dolly out + zoom in)."""
    travel = _travel(sub)
    d0, d1 = (float(v) for v in p.get("distance", [320.0, 110.0]))
    distance = d0 + (d1 - d0) * (f * f * (3 - 2 * f))
    height = float(p.get("height", 4.0))

    def attempt(angle):
        direction = _rot(travel[None], np.array([_angle(angle)]))[0]
        wanted = np.c_[sub.aim[:, :2] + direction * distance[:, None], sub.aim[:, 2] + height]
        placed, pulled_mask = _resolve(geometry, sub.aim, wanted, kps)
        placed = gaussian_filter1d(placed, sigma=0.12 * kps, axis=0, mode="nearest")
        dist = np.linalg.norm(placed - sub.aim, axis=1)
        return placed, pulled_mask, dist

    if p.get("angle", "auto") == "auto":  # the angle that keeps most of the move (walls shorten it)
        options = [attempt(a) for a in ("front", "front-right", "front-left", "right", "left", "back")]
        cam, pulled, real = max(options, key=lambda o: abs(o[2][0] - o[2][-1]))
    else:
        cam, pulled, real = attempt(p["angle"])
    fov0 = math.radians(float(p.get("fov", 28.0)))  # 4:3 horizontal FOV at the start distance
    fov = np.degrees(2 * np.arctan(np.tan(fov0 / 2) * real[0] / np.maximum(real, 1.0)))
    pitch, yaw = _look(cam, sub.aim)
    warnings = ["walls shortened the dolly zoom"] if pulled.mean() > 0.3 else []
    return cam, pitch, yaw, np.clip(fov, 8.0, 120.0), warnings


def place_stalker(geometry, sub, p) -> np.ndarray:
    """A hidden watcher's spot: sees the subject, tucked against a wall or corner (foreground edge in frame),
    preferably behind the subject, at a distance."""
    anchor = sub.aim[np.argmin(np.linalg.norm(sub.aim - sub.aim.mean(axis=0), axis=1))]
    base = _travel(sub)
    want = float(p.get("distance", 650.0))
    samples = sub.aim[:: max(1, len(sub.aim) // 25)]
    best, best_score = None, -1e9
    for angle in range(0, 360, 20):
        direction = _rot(base[None], np.array([float(angle)]))[0]
        for scale in (0.7, 1.0, 1.4):
            for dz in (10.0, 40.0, 110.0):
                cam = np.r_[anchor[:2] + direction * want * scale, anchor[2] + dz]
                if geometry is None:
                    return cam
                floor = geometry.floor_z(*cam, depth=2000.0)
                if floor is None or cam[2] - floor < 20.0 or not geometry.clear(anchor, cam):
                    continue
                if sees_zone(geometry, cam, p):
                    continue
                visible = np.mean([geometry.clear(cam, s) for s in samples])
                # tucked: a wall close beside the lens (within 70 units) in some horizontal direction
                near = []
                for a in range(0, 360, 45):
                    ray = np.array([math.cos(math.radians(a)), math.sin(math.radians(a)), 0.0]) * 70.0
                    hit = geometry.first_hit(cam, cam + ray, see_through_blocks=True)
                    near.append(hit is not None)
                behind = abs(((angle + 180) % 360) - 180) > 110  # camera behind the subject's travel direction
                score = visible * 6 + (1.5 if any(near) else 0) + (1.0 if behind else 0) - abs(math.log(scale)) \
                    - (0.5 if dz > 60 else 0)
                if score > best_score:
                    best, best_score = cam, score
    if best is None:
        raise ShotError("No hidden watcher position found; give \"pos\" explicitly.")
    return best


def shot_stalker(geometry, sub, p, f, kps):
    """Stalker vision: a hidden observer at a distance, telephoto, slow lagging pan, a slight unsteady hold."""
    n = len(f)
    cam = np.asarray(p["pos"], float) if p.get("pos") else place_stalker(geometry, sub, p)
    target = gaussian_filter1d(sub.aim, sigma=float(p.get("lag", 0.7)) * kps, axis=0, mode="nearest")
    pitch, yaw = _look(np.tile(cam, (n, 1)), target)
    dist = float(np.median(np.linalg.norm(sub.aim - cam, axis=1)))
    fov = hfov_for_size(dist, p.get("size", "full"))
    return np.tile(cam, (n, 1)), pitch, yaw, np.full(n, fov), []


def shot_ots(geometry, sub, p, f, kps):
    """Over the shoulder of another player (the threat) looking at the subject (the victim)."""
    over = p.get("_over")
    if over is None:
        raise ShotError("\"ots\" needs \"over\": the player whose shoulder the camera looks over.")
    n = len(f)
    eye = over + [0, 0, 60.0]
    to_subject = sub.aim[:, :2] - eye[:, :2]
    direction = _unit(gaussian_filter1d(_unit(to_subject), sigma=0.3 * kps, axis=0, mode="nearest"))
    side = np.c_[direction[:, 1], -direction[:, 0]] * (1 if p.get("shoulder", "right") == "right" else -1)
    cam = np.c_[eye[:, :2] - direction * float(p.get("back", 55.0)) + side * float(p.get("offset", 20.0)),
                eye[:, 2] + float(p.get("height", 6.0))]
    cam = gaussian_filter1d(cam, sigma=0.15 * kps, axis=0, mode="nearest")
    target = gaussian_filter1d(sub.aim, sigma=0.2 * kps, axis=0, mode="nearest")
    # Composition: the threat's head/shoulder on one third, the subject on the other. Aim between the two and pick
    # a lens wide enough for both (a lens chosen for the far subject alone left the head outside the frame).
    head = eye - [0, 0, 4.0]
    _, yaw_subject = _look(cam, target)
    _, yaw_head = _look(cam, head)
    rel_head = (yaw_head - yaw_subject + 180) % 360 - 180         # degrees; + = head to the left of the subject
    yaw = yaw_subject + rel_head * float(p.get("headWeight", 0.45))
    yaw = gaussian_filter1d(yaw, sigma=0.1 * kps, mode="nearest")
    # Lens from where both actually land after the aim is settled: the farther one sits at ~70% of the half-width.
    off_subject = np.abs((yaw_subject - yaw + 180) % 360 - 180)
    off_head = np.abs((yaw_head - yaw + 180) % 360 - 180)
    half = np.maximum(np.maximum(off_subject, off_head) / 0.7, 6.0)
    fov = np.degrees(2 * np.arctan(np.tan(np.radians(2 * half) / 2) * 0.75))
    if p.get("fov"):
        fov = np.full(n, float(p["fov"]))
    else:  # open early, close slowly: never crop the head or the subject
        fov = gaussian_filter1d(maximum_filter1d(fov, size=max(3, int(0.6 * kps) | 1), mode="nearest"),
                                sigma=0.2 * kps, mode="nearest")
    pitch, _ = _look(cam, target * 0.8 + head * 0.2)
    return cam, pitch, yaw, np.clip(fov, 20.0, 100.0), []


def shot_pov(geometry, sub, p, f, kps):
    """Found-footage camcorder in the subject's own hands: their eye position and view direction with camcorder
    inertia (the lens lags the mouse a little), a wide lens, plus handheld shake. Spectated in first person, so the
    subject's own model is hidden (csdv does that for "pov")."""
    view = p.get("_view")
    if view is None:
        raise ShotError("\"pov\" needs the subject's view angles.")
    view_pitch, view_yaw, duck = view
    n = len(f)
    inertia = float(p.get("inertia", 0.12)) * kps
    pitch = gaussian_filter1d(view_pitch, sigma=max(inertia, 0.5), mode="nearest") * float(p.get("pitchScale", 0.8))
    yaw = gaussian_filter1d(np.degrees(np.unwrap(np.radians(view_yaw))), sigma=max(inertia, 0.5), mode="nearest")
    eye = sub.feet + np.c_[np.zeros(n), np.zeros(n), 64.0 - 18.0 * duck]
    cam = gaussian_filter1d(eye, sigma=0.05 * kps, axis=0, mode="nearest")
    return cam, pitch, yaw, np.full(n, float(p.get("fov", 95.0))), []


PLACED_SHOTS = {"static": shot_static, "overhead": shot_overhead, "drone": shot_drone, "ground": shot_ground,
                "dolly_zoom": shot_dolly_zoom, "stalker": shot_stalker, "ots": shot_ots, "pov": shot_pov}
SHOT_DEFAULTS.update({
    "static": dict(angle="front-left", distance=650.0, height=60.0, margin=1.15),
    "overhead": dict(fov169=60.0, margin=1.2, spin=0.0),
    "drone": dict(move="flyover", altitude=380.0, distance=900.0, offset=250.0, lag=1.0, fov=70.0),
    "ground": dict(facing="away", back=48.0, side=14.0, lens=6.0, fov=80.0, lookAhead=0.45),
    "dolly_zoom": dict(angle="auto", distance=[320.0, 110.0], height=4.0, fov=28.0),
    "stalker": dict(distance=650.0, size="full", lag=0.7, handheld=0.3),
    "ots": dict(back=70.0, offset=16.0, height=8.0, shoulder="right", headWeight=0.45),
    "pov": dict(fov=95.0, handheld=0.7, inertia=0.12, pitchScale=0.8),
})
SHOTS.update(PLACED_SHOTS)


# --- shot builder ------------------------------------------------------------------------------------------------

def build(kind: str, track: Track, start_tick: int, end_tick: int, tickrate: float, params: dict | None = None,
          geometry=None) -> ShotResult:
    if kind not in SHOT_DEFAULTS:
        raise ShotError(f"Unknown cinematic shot '{kind}'. Use one of {sorted(SHOTS)}.")
    p = {**SHOT_DEFAULTS[kind], **(params or {})}
    if p.get("hide"):
        p["_hide_pts"] = hide_points(p["hide"])
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
    step = HANDHELD_KEY_STEP_TICKS if p.get("handheld") else KEY_STEP_TICKS  # denser keys keep the fast jitter
    key_ticks = np.arange(start_tick, end_tick + 1, step)
    if key_ticks[-1] != end_tick:
        key_ticks = np.r_[key_ticks, end_tick]
    idx = np.searchsorted(sub_full.tick, key_ticks).clip(0, len(sub_full.tick) - 1)
    sub = Subject(key_ticks, sub_full.feet[idx], sub_full.heading[idx], sub_full.aim[idx], sub_full.speed[idx])
    t = (key_ticks - start_tick) / tickrate
    f = t / max(t[-1], 1e-9)
    keys_per_second = tickrate / step

    if kind == "pov":  # the subject's own view angles + crouch at the key ticks
        wi = np.searchsorted(window.tick, key_ticks).clip(0, len(window.tick) - 1)
        p["_view"] = (window.pitch[wi], window.yaw[wi], window.duck[wi])
    if isinstance(p.get("overTrack"), Track):  # second player for over-the-shoulder shots
        other = p["overTrack"]
        oi = np.searchsorted(other.tick, key_ticks).clip(0, len(other.tick) - 1)
        p["_over"] = gaussian_filter1d(other.pos[oi], sigma=0.2 * keys_per_second, axis=0, mode="nearest")

    if kind in PLACED_SHOTS:
        cam, pitch, yaw, fov, extra = PLACED_SHOTS[kind](geometry, sub, p, f, keys_per_second)
        warnings += extra
        roll = _ramp(p.get("roll", 0.0), f)
        if p.get("handheld"):
            cam, pitch, yaw, roll = apply_handheld(p["handheld"], t, keys_per_second, cam, pitch, yaw, roll,
                                                   int(p.get("seed", 7)))
        keys = [campath.Key(float(t[i]), *map(float, cam[i]), float(pitch[i]), float(yaw[i]), float(roll[i]),
                            float(fov[i])) for i in range(len(t))]
        # POV shows the subject's own view; ground lock-offs let the subject walk into / out of frame on purpose
        framed_check = None if kind in ("pov", "ground") else (pitch, yaw, fov)
        visible = _visible_outside_zone(geometry, cam, sub, p, view=framed_check)
        if visible < 0.9:
            warnings.append(f"subject hidden behind geometry in {100 * (1 - visible):.0f}% of the shot")
        exposed = _exposed(geometry, cam, pitch, yaw, fov, p, warnings)
        return ShotResult(keys, cam, sub.aim, sub.feet, visible, 0.0, warnings, exposed)

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
            if p.get("_hide_pts") is not None:  # rough check (aim at the subject); the exact one runs at the end
                pch, yw = _look(placed, sub.aim)
                lost += 10.0 * zone_exposure(geometry, placed, pch, yw, np.full(len(placed), 70.0), p["_hide_pts"]).mean()
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
        cam, pitch, yaw, roll = apply_handheld(p["handheld"], t, keys_per_second, cam, pitch, yaw, roll,
                                               int(p.get("seed", 7)))

    if p.get("fov") is None:
        fov = np.full(len(t), hfov_for_size(float(np.median(dist_now)), p.get("size", "medium")))
    else:
        fov = _ramp(p["fov"], f)

    keys = [campath.Key(float(t[i]), *map(float, cam[i]), float(pitch[i]), float(yaw[i]), float(roll[i]), float(fov[i]))
            for i in range(len(t))]

    visible = _visible_outside_zone(geometry, cam, sub, p, aim, view=(pitch, yaw, fov))
    if visible < 0.9:
        warnings.append(f"subject hidden behind geometry in {100 * (1 - visible):.0f}% of the shot")
    exposed = _exposed(geometry, cam, pitch, yaw, fov, p, warnings)
    if pulled.mean() > 0.3:
        warnings.append(f"walls pulled the camera in for {100 * pulled.mean():.0f}% of the shot; "
                        "try another angle or a shorter distance")
    return ShotResult(keys, cam, aim, sub.feet, visible, float(pulled.mean()), warnings, exposed)


def _inside_zone(sub: Subject, p: dict) -> np.ndarray:
    """Subject inside (or stepping into) the hidden zone: there they are supposed to be out of view."""
    zone = p.get("hide")
    if not zone:
        return np.zeros(len(sub.feet), bool)
    c = np.asarray(zone["center"], float)[:2]
    toward = sub.feet[:, :2] + (c - sub.feet[:, :2]) / np.maximum(np.linalg.norm(c - sub.feet[:, :2], axis=1), 1e-6)[:, None] * 24.0
    return in_zone(zone, sub.feet[:, :2]) | in_zone(zone, toward)


def _visible_outside_zone(geometry, cam, sub: Subject, p: dict, aim=None, view=None) -> float:
    """Share of keys where the subject is on screen: a clear line of sight AND, when view=(pitch, yaw, fov) is
    given, inside the frame (a lagging pan once had a clear line to a subject who had walked out of the picture).
    Moments inside a hidden zone are ignored (there they are *supposed* to be out of view)."""
    aim = sub.aim if aim is None else aim
    keys = np.flatnonzero(~_inside_zone(sub, p))
    if len(keys) == 0:
        return 1.0
    ok = np.ones(len(keys), bool)
    if view is not None:
        pitch, yaw, fov = (np.asarray(v, float) for v in view)
        chest = sub.feet + [0.0, 0.0, 44.0]
        ok &= np.array([_in_frame(cam[i], pitch[i], yaw[i], fov[i], chest[i:i + 1])[0] for i in keys])
    if geometry is not None:
        ok &= np.array([geometry.clear(cam[i], aim[i]) for i in keys])
    return float(ok.mean())


def _exposed(geometry, cam, pitch, yaw, fov, p: dict, warnings: list) -> float:
    pts = p.get("_hide_pts")
    if pts is None:
        return 0.0
    exposed = float(zone_exposure(geometry, cam, np.asarray(pitch), np.asarray(yaw), np.asarray(fov), pts).mean())
    if exposed > 0:
        warnings.append(f"the hidden zone is on screen in {100 * exposed:.0f}% of the shot")
    return exposed


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
