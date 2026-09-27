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

    def window(self, start: int, end: int) -> "Track":
        keep = (self.tick >= start) & (self.tick <= end)
        return Track(self.steam_id, self.name, self.tick[keep], self.pos[keep], self.yaw[keep], self.pitch[keep],
                     self.alive[keep], self.duck[keep])

    def teleports(self) -> np.ndarray:
        """Ticks at which the player jumped (respawn, teleport, noclip burst)."""
        step = np.linalg.norm(np.diff(self.pos, axis=0), axis=1) / np.maximum(np.diff(self.tick), 1) * 64.0
        return self.tick[1:][step > TELEPORT_SPEED]


@lru_cache(maxsize=4)
def load_tracks(demo_path: str) -> dict[str, Track]:
    from demoparser2 import DemoParser  # local dependency (pip install demoparser2); not needed for tests

    frame = DemoParser(demo_path).parse_ticks(["X", "Y", "Z", "yaw", "pitch", "is_alive", "duck_amount"])
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
        )
    return tracks
