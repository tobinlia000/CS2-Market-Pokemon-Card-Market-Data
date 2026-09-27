"""Tests for csdv. Run: python -m unittest discover -s tools/csdv"""

import json
import tempfile
import unittest
from pathlib import Path

import csdv

A = "76561198000000001"  # "Liam #1"
B = "76561198000000002"  # 'Bob "the" Builder'
C = "76561198000000003"  # "Carl"


def kill(tick, round_number, killer, victim, weapon="ak47", headshot=False):
    names = {A: "Liam #1", B: 'Bob "the" Builder', C: "Carl"}
    return {
        "tick": tick,
        "roundNumber": round_number,
        "killerSteamId": killer,
        "killerName": names[killer],
        "victimSteamId": victim,
        "victimName": names[victim],
        "weaponName": weapon,
        "isHeadshot": headshot,
        "penetratedObjects": 0,
        "distance": 10.0,
    }


EXPORT = {
    "name": "match",
    "checksum": "abc",
    "demoFilePath": "C:\\demos\\match.dem",
    "game": "CS2",
    "mapName": "de_mirage",
    "tickrate": 64,
    "tickCount": 200_000,
    "duration": 3000,
    "teamA": {"name": "Team A", "score": 13},
    "teamB": {"name": "Team B", "score": 7},
    "players": [
        {"steamId": A, "name": "Liam #1", "teamName": "Team A", "killCount": 4},
        {"steamId": B, "name": 'Bob "the" Builder', "teamName": "Team B", "killCount": 1},
        {"steamId": C, "name": "Carl", "teamName": "Team B", "killCount": 0},
    ],
    "rounds": [
        {"number": 1, "startTick": 100, "freezetimeEndTick": 1000, "endTick": 9000, "winnerSide": 3},
        {"number": 2, "startTick": 9500, "freezetimeEndTick": 10500, "endTick": 20000, "winnerSide": 2},
    ],
    "kills": [
        kill(2000, 1, A, B, headshot=True),
        kill(2300, 1, A, C),  # 4.7 s later -> merged into the same sequence
        kill(15000, 2, B, A),
        kill(12000, 2, A, C, weapon="awp"),
    ],
    "clutches": [],
}


class CsdvTest(unittest.TestCase):
    def setUp(self):
        self.summary = csdv.summarize_match(EXPORT)
        self.profile = json.loads((csdv.REPO_ROOT / "videos" / "profile.json").read_text())

    def build(self, clips, **spec):
        return csdv.build_config({"name": "t", "clips": clips, **spec}, self.profile, self.summary)

    def test_summary_multi_kills_and_sorting(self):
        self.assertEqual([k["tick"] for k in self.summary["kills"]], [2000, 2300, 12000, 15000])
        self.assertEqual(len(self.summary["multiKills"]), 1)
        self.assertEqual(self.summary["multiKills"][0]["label"], "2k")
        self.assertEqual(self.summary["multiKills"][0]["round"], 1)
        self.assertEqual(self.summary["rounds"][0]["winnerSide"], "CT")

    def test_profile_is_1440p60(self):
        config = self.build([{"type": "kills", "player": "liam"}])
        self.assertEqual((config["width"], config["height"], config["framerate"]), (2560, 1440, 60))
        self.assertEqual(config["recordingSystem"], "HLAE")
        self.assertEqual(config["ffmpegSettings"]["videoContainer"], "mp4")

    def test_kills_are_merged_like_csdm(self):
        config = self.build([{"type": "kills", "player": A}])
        seqs = config["sequences"]
        self.assertEqual(len(seqs), 2)
        self.assertEqual(seqs[0]["startTick"], 2000 - 3 * 64)
        self.assertEqual(seqs[0]["endTick"], 2300 + 2 * 64)
        self.assertEqual([s["number"] for s in seqs], [1, 2])
        self.assertEqual(seqs[0]["playerCameras"][0]["playerSteamId"], A)

    def test_filters(self):
        config = self.build([{"type": "kills", "player": A, "weapons": ["AWP"]}])
        self.assertEqual(len(config["sequences"]), 1)
        config = self.build([{"type": "kills", "player": A, "minKillsInRound": 2}])
        self.assertEqual(len(config["sequences"]), 1)

    def test_enemy_perspective_switches_pov(self):
        config = self.build([{"type": "kills", "player": A, "rounds": [1], "perspective": "enemy"}])
        cams = [c["playerSteamId"] for c in config["sequences"][0]["playerCameras"]]
        self.assertEqual(cams, [B, C])

    def test_round_and_time_clips(self):
        config = self.build(
            [
                {"type": "round", "round": 2, "player": "carl"},
                {"type": "time", "start": "0:10", "end": "0:20", "pov": B, "povSwitches": [{"time": 15, "player": C}]},
            ]
        )
        first, second = config["sequences"]
        self.assertEqual((first["startTick"], first["endTick"]), (640, 1280))
        self.assertEqual([c["tick"] for c in first["playerCameras"]], [640, 960])
        self.assertEqual(second["startTick"], 10500 - 128)

    def test_config_strings_are_safe_for_csdm_parser(self):
        config = self.build([{"type": "kills", "player": A}])
        self.assertEqual([p for p in csdv.validate_config(config) if p.startswith("ERROR")], [])
        names = [o["playerName"] for o in config["sequences"][0]["playersOptions"]]
        self.assertIn("Liam \uff031", names)
        self.assertIn("Bob 'the' Builder", names)
        highlighted = [o["steamId"] for o in config["sequences"][0]["playersOptions"] if o["highlightKill"]]
        self.assertEqual(highlighted, [A])

    def test_validate_catches_problems(self):
        config = self.build([{"type": "ticks", "start": 500, "end": 900, "cfg": "mirv_fov 90 // wide"}])
        errors = [p for p in csdv.validate_config(config) if p.startswith("ERROR")]
        self.assertTrue(any("'//'" in e for e in errors))
        del config["sequences"][0]["cameras"]
        errors = [p for p in csdv.validate_config(config) if p.startswith("ERROR")]
        self.assertTrue(any("'cameras'" in e for e in errors))

    def test_ambiguous_player_errors(self):
        with self.assertRaises(csdv.CsdvError):
            self.build([{"type": "kills", "player": "l"}])  # Liam, Bob ... Builder and Carl all contain "l"

    def test_cli_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "match.json"
            export.write_text(json.dumps(EXPORT))
            self.assertEqual(csdv.main(["summarize", str(export)]), 0)
            summary_path = Path(tmp) / "match.summary.json"
            spec = Path(tmp) / "spec.json"
            spec.write_text(json.dumps({"name": "t", "summary": str(summary_path), "clips": [{"type": "kills", "player": A}]}))
            out = Path(tmp) / "out.csdm.json"
            self.assertEqual(csdv.main(["build", str(spec), "-o", str(out)]), 0)
            self.assertEqual(csdv.main(["validate", str(out)]), 0)
            self.assertEqual(json.loads(out.read_text())["demoPath"], "C:\\demos\\match.dem")


if __name__ == "__main__":
    unittest.main()
