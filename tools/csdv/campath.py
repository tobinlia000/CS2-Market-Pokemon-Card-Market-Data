"""Generate HLAE camera paths (mirv_campath XML) from world coordinates.

Works on any map, including workshop maps: HLAE and CS2 only need world coordinates (Hammer units).

Conventions (Source engine / HLAE):
- x, y, z in world units; z is up. Player eye height is ~64 units above the feet (demo positions are feet).
- yaw in degrees, 0 = +x, 90 = +y. pitch in degrees, positive = looking DOWN. roll in degrees.
- XML keyframe: <p t= x= y= z= fov= rx=roll ry=pitch rz=yaw/>. HLAE needs >= 4 keyframes to enable a path.
- Times `t` are relative (start at 0). csdv aligns them to the recording start with
  `mirv_cmd addAtTick <startTick> mirv_campath offset current` (HLAE's command system uses real demo ticks).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

EYE_HEIGHT = 64.0
MIN_KEYFRAMES = 4


@dataclass
class Key:
    t: float
    x: float
    y: float
    z: float
    pitch: float
    yaw: float
    roll: float = 0.0
    fov: float = 90.0


def look_at(camera: tuple[float, float, float], target: tuple[float, float, float]) -> tuple[float, float]:
    """Return (pitch, yaw) that points a camera at the target."""
    dx, dy, dz = (target[i] - camera[i] for i in range(3))
    yaw = math.degrees(math.atan2(dy, dx))
    pitch = -math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    return round(pitch, 3), round(yaw, 3)


def _unwrap(keys: list[Key]) -> list[Key]:
    """Avoid 359 -> 0 degree yaw flips between keyframes."""
    for previous, current in zip(keys, keys[1:]):
        while current.yaw - previous.yaw > 180:
            current.yaw -= 360
        while current.yaw - previous.yaw < -180:
            current.yaw += 360
    return keys


def _ensure_min_keys(keys: list[Key]) -> list[Key]:
    """HLAE refuses paths with < 4 keyframes: pad by splitting the time range evenly (same view for static shots)."""
    if len(keys) >= MIN_KEYFRAMES:
        return keys
    if len(keys) == 1:
        k = keys[0]
        return [Key(k.t + i * 0.001, k.x, k.y, k.z, k.pitch, k.yaw, k.roll, k.fov) for i in range(MIN_KEYFRAMES)]
    result = [keys[0]]
    steps = MIN_KEYFRAMES - 1
    first, last = keys[0], keys[-1]
    for i in range(1, steps):
        f = i / steps
        lerp = lambda a, b: a + (b - a) * f  # noqa: E731
        result.append(
            Key(
                lerp(first.t, last.t), lerp(first.x, last.x), lerp(first.y, last.y), lerp(first.z, last.z),
                lerp(first.pitch, last.pitch), lerp(first.yaw, last.yaw), lerp(first.roll, last.roll), lerp(first.fov, last.fov),
            )
        )
    result.append(last)
    return result


def static(pos, duration: float, target=None, pitch=0.0, yaw=0.0, fov=90.0) -> list[Key]:
    if target is not None:
        pitch, yaw = look_at(pos, target)
    return _ensure_min_keys([Key(0, *pos, pitch, yaw, 0, fov), Key(duration, *pos, pitch, yaw, 0, fov)])


def dolly(start, end, duration: float, target=None, start_angles=(0.0, 0.0), end_angles=None, fov=90.0,
          steps: int = 8) -> list[Key]:
    """Straight move from start to end. With a target the camera keeps looking at it."""
    keys = []
    end_angles = end_angles or start_angles
    for i in range(steps + 1):
        f = i / steps
        pos = tuple(start[a] + (end[a] - start[a]) * f for a in range(3))
        if target is not None:
            pitch, yaw = look_at(pos, target)
        else:
            pitch = start_angles[0] + (end_angles[0] - start_angles[0]) * f
            yaw = start_angles[1] + (end_angles[1] - start_angles[1]) * f
        keys.append(Key(duration * f, *pos, pitch, yaw, 0, fov))
    return _unwrap(keys)


def orbit(center, radius: float, height: float, duration: float, start_deg: float = 0.0, degrees: float = 90.0,
          fov=90.0, steps: int | None = None) -> list[Key]:
    """Circle around a point (e.g. a player's position + eye height) while looking at it."""
    steps = steps or max(MIN_KEYFRAMES, int(abs(degrees) / 15) + 1)
    keys = []
    for i in range(steps + 1):
        f = i / steps
        angle = math.radians(start_deg + degrees * f)
        pos = (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle), center[2] + height)
        pitch, yaw = look_at(pos, center)
        keys.append(Key(duration * f, *pos, pitch, yaw, 0, fov))
    return _unwrap(keys)


def track(points: list[tuple[float, tuple[float, float, float]]], offset=(-120.0, 0.0, 40.0), fov=90.0,
          smooth: int = 1) -> list[Key]:
    """Follow a moving subject. points = [(t, (x, y, z)), ...] (feet positions, e.g. from demo positions).
    offset is applied in world space; the camera looks at the subject's eye height."""
    keys = []
    for t, p in points:
        eye = (p[0], p[1], p[2] + EYE_HEIGHT)
        pos = (eye[0] + offset[0], eye[1] + offset[1], eye[2] + offset[2])
        pitch, yaw = look_at(pos, eye)
        keys.append(Key(t, *pos, pitch, yaw, 0, fov))
    if smooth > 1:
        keys = keys[::smooth] + ([keys[-1]] if (len(keys) - 1) % smooth else [])
    return _ensure_min_keys(_unwrap(keys))


def from_keys(raw: list[dict]) -> list[Key]:
    """Explicit keyframes: [{"t":0,"pos":[x,y,z],"lookAt":[x,y,z]} | {"t":..,"pos":..,"pitch":..,"yaw":..}]."""
    keys = []
    for k in raw:
        pos = tuple(k["pos"])
        if "lookAt" in k:
            pitch, yaw = look_at(pos, tuple(k["lookAt"]))
        else:
            pitch, yaw = k.get("pitch", 0.0), k.get("yaw", 0.0)
        keys.append(Key(float(k["t"]), *pos, pitch, yaw, k.get("roll", 0.0), k.get("fov", 90.0)))
    keys.sort(key=lambda k: k.t)
    return _ensure_min_keys(_unwrap(keys))


def parse_getpos(text: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Parse CS2 `getpos` output: 'setpos x y z;setang pitch yaw roll'."""
    import re

    pos = re.search(r"setpos(?:_exact)?\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)", text)
    ang = re.search(r"setang(?:_exact)?\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)", text)
    if not pos:
        raise ValueError(f"No setpos found in {text!r}")
    angles = tuple(float(v) for v in ang.groups()) if ang else (0.0, 0.0, 0.0)
    return tuple(float(v) for v in pos.groups()), angles


def to_xml(keys: list[Key], interp: str = "cubic") -> str:
    if len(keys) < MIN_KEYFRAMES:
        raise ValueError("HLAE needs at least 4 keyframes")
    rot = "sCubic" if interp == "cubic" else "sLinear"
    lines = [
        '<?xml version="1.0"?>',
        f"<campath positionInterp={quoteattr(interp)} rotationInterp={quoteattr(rot)} fovInterp={quoteattr(interp)}>",
        "\t<points>",
    ]
    for k in keys:
        lines.append(
            f'\t\t<p t="{k.t:.4f}" x="{k.x:.3f}" y="{k.y:.3f}" z="{k.z:.3f}" fov="{k.fov:.3f}"'
            f' rx="{k.roll:.3f}" ry="{k.pitch:.3f}" rz="{k.yaw:.3f}"/>'
        )
    lines += ["\t</points>", "</campath>", ""]
    return "\n".join(lines)


def build_shot(shot: dict, duration: float) -> list[Key]:
    """Spec "camera" object -> keyframes. See SKILL.md for the fields."""
    kind = shot.get("shot", "static")
    fov = shot.get("fov", 90.0)
    tup = lambda v: tuple(float(a) for a in v) if v is not None else None  # noqa: E731
    if kind == "static":
        return static(tup(shot["pos"]), duration, tup(shot.get("lookAt")), shot.get("pitch", 0.0), shot.get("yaw", 0.0), fov)
    if kind == "dolly":
        return dolly(tup(shot["from"]), tup(shot["to"]), duration, tup(shot.get("lookAt")),
                     tuple(shot.get("startAngles", (0.0, 0.0))), tup(shot.get("endAngles")), fov)
    if kind == "orbit":
        return orbit(tup(shot["center"]), shot.get("radius", 250.0), shot.get("height", 60.0), duration,
                     shot.get("startDeg", 0.0), shot.get("degrees", 90.0), fov)
    if kind == "keys":
        return from_keys(shot["keys"])
    raise ValueError(f"Unknown camera shot '{kind}'. Use static, dolly, orbit, keys or file.")
