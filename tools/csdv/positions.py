"""Per-tick player tracks from a CS2 demo (demoparser2), for cinematic cameras.

Works on any demo (official or workshop map, with or without rounds): demoparser2 reads every tick. CS:DM's own
analyzer only samples positions inside rounds, which free-roam demos don't have.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

TELEPORT_SPEED = 1500.0  # units/s between two ticks; above this is a respawn/teleport/noclip jump -> cut


@dataclass
class Track:
    steam_id: str
    name: str
    tick: np.ndarray       # (N,) int
    pos: np.ndarray        # (N, 3) feet position
    yaw: np.ndarray        # (N,) view yaw, degrees
    pitch: np.ndarray      # (N,) view pitch, degrees (+ = down)
    alive: np.ndarray      # (N,) bool
    duck: np.ndarray       # (N,) 0..1
    team: int = 0          # 2 = T, 3 = CT (most common value in the demo)

    def window(self, start: int, end: int) -> "Track":
        keep = (self.tick >= start) & (self.tick <= end)
        return Track(self.steam_id, self.name, self.tick[keep], self.pos[keep], self.yaw[keep], self.pitch[keep],
                     self.alive[keep], self.duck[keep], self.team)

    def teleports(self) -> np.ndarray:
        """Ticks at which the player jumped (respawn, teleport, noclip burst)."""
        step = np.linalg.norm(np.diff(self.pos, axis=0), axis=1) / np.maximum(np.diff(self.tick), 1) * 64.0
        return self.tick[1:][step > TELEPORT_SPEED]


@lru_cache(maxsize=4)
def load_tracks(demo_path: str) -> dict[str, Track]:
    from demoparser2 import DemoParser  # local dependency (pip install demoparser2); not needed for tests

    frame = DemoParser(demo_path).parse_ticks(["X", "Y", "Z", "yaw", "pitch", "is_alive", "duck_amount", "team_num"])
    tracks = {}
    for steam_id, group in frame.groupby("steamid"):
        group = group.sort_values("tick")
        tracks[str(steam_id)] = Track(
            steam_id=str(steam_id),
            name=str(group["name"].iloc[-1]),
            tick=group["tick"].to_numpy(np.int64),
            pos=group[["X", "Y", "Z"]].to_numpy(np.float64),
            yaw=group["yaw"].to_numpy(np.float64),
            pitch=group["pitch"].to_numpy(np.float64),
            alive=group["is_alive"].to_numpy(bool),
            duck=group["duck_amount"].fillna(0).to_numpy(np.float64),
            team=int(group["team_num"].mode().iloc[0]) if "team_num" in group else 0,
        )
    return tracks


def find_player(tracks: dict[str, Track], ref: str) -> Track:
    """A player by SteamID64, name (case-insensitive, exact then substring) or side ("CT" / "T")."""
    if ref in tracks:
        return tracks[ref]
    low = ref.lower()
    side = {"ct": 3, "t": 2}.get(low)
    if side:
        found = [t for t in tracks.values() if t.team == side]
        if len(found) == 1:
            return found[0]
    exact = [t for t in tracks.values() if t.name.lower() == low]
    partial = [t for t in tracks.values() if low in t.name.lower()]
    found = exact or partial
    if len(found) != 1:
        raise KeyError(f"Player '{ref}' matches {len(found)} players: {[t.name for t in tracks.values()]}")
    return found[0]


def still_spots(track: Track, start: int, end: int, min_seconds: float = 4.0, speed: float = 20.0) -> list[dict]:
    """Periods where the player stands still (>= min_seconds): [{start, end, pos}], longest first."""
    w = track.window(start, end)
    if len(w.tick) < 2:
        return []
    step = np.r_[0.0, np.linalg.norm(np.diff(w.pos[:, :2], axis=0), axis=1) * 64.0]
    still = np.convolve(step < speed, np.ones(64) / 64, "same") > 0.9
    spots, s = [], None
    for i, v in enumerate(still):
        if v and s is None:
            s = i
        if (not v or i == len(still) - 1) and s is not None:
            if (w.tick[i - 1] - w.tick[s]) / 64.0 >= min_seconds:
                spots.append({"start": int(w.tick[s]), "end": int(w.tick[i - 1]), "pos": w.pos[s:i].mean(axis=0)})
            s = None
    return sorted(spots, key=lambda x: x["start"] - x["end"])
