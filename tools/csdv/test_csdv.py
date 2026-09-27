"""Tests for csdv. Run: python -m unittest discover -s tools/csdv"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import campath
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
            ],
            view="first",
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

    def test_safety_blocks_server_and_map_commands(self):
        for line in ["connect 1.2.3.4:27015", "+connect 1.2.3.4", "mirv_fov 90; retry", "map de_dust2",
                     "map_workshop 123 x", "mirv_cmd addAtTick 500 connect 1.2.3.4", "exec autoexec",
                     "mirv_loadlibrary x.dll", "echo steam://connect/1.2.3.4"]:
            self.assertTrue(csdv.unsafe_command_problems(line), line)
        for line in ["mirv_fov 90", "spec_mode 1", "mirv_cmd addAtTick 500 mirv_campath offset current", "r_drawviewmodel 0"]:
            self.assertEqual(csdv.unsafe_command_problems(line), [], line)
        config = self.build([{"type": "ticks", "start": 500, "end": 900, "cfg": "connect 1.2.3.4"}])
        errors = [p for p in csdv.validate_config(config) if "SAFETY" in p]
        self.assertTrue(errors)

    def test_look_at_conventions(self):
        self.assertEqual(campath.look_at((0, 0, 0), (100, 0, 0)), (0.0, 0.0))
        self.assertEqual(campath.look_at((0, 0, 0), (0, 100, 0))[1], 90.0)
        pitch, _ = campath.look_at((0, 0, 100), (100, 0, 0))
        self.assertAlmostEqual(pitch, 45.0)  # positive pitch = looking down

    def test_campath_xml_and_min_keys(self):
        keys = campath.static((1, 2, 3), 5.0, target=(10, 2, 3))
        self.assertGreaterEqual(len(keys), 4)
        xml = campath.to_xml(campath.orbit((0, 0, 0), 200, 50, 4.0, degrees=180))
        self.assertIn('<campath positionInterp="cubic" rotationInterp="sCubic"', xml)
        self.assertGreaterEqual(xml.count("<p "), 4)
        yaws = [k.yaw for k in campath.orbit((0, 0, 0), 200, 0, 4.0, start_deg=170, degrees=40)]
        self.assertTrue(all(abs(b - a) < 180 for a, b in zip(yaws, yaws[1:])))
        pos, ang = campath.parse_getpos("setpos -2028.00 1043.50 125.03;setang 5.40 63.40 0.00")
        self.assertEqual(pos, (-2028.0, 1043.5, 125.03))
        self.assertEqual(ang[1], 63.4)

    def test_camera_clip_is_synced_to_ticks(self):
        spec = {"name": "t", "clips": [
            {"type": "ticks", "start": 6400, "end": 6720, "camera": {"shot": "orbit", "center": [0, 0, 64], "degrees": 90}}]}
        config = csdv.build_config(spec, self.profile, self.summary, write_files=False)
        cfg = config["sequences"][0]["cfg"].split("\n")
        self.assertIn('mirv_campath load "{REPO}/videos/campaths/t-clip1.xml"', cfg)
        self.assertIn("mirv_cmd addAtTick 6400 mirv_campath offset current", cfg)
        self.assertIn("mirv_cmd addAtTick 6720 mirv_campath enabled 0", cfg)
        self.assertEqual([p for p in csdv.validate_config(config) if p.startswith("ERROR")], [])
        with self.assertRaises(csdv.CsdvError):
            self.build([{"type": "kills", "player": A, "camera": {"shot": "static", "pos": [0, 0, 0]}}])

    def test_third_person_starts_before_recording(self):
        config = self.build([{"type": "time", "start": "0:10", "end": "0:20", "pov": B, "view": "third",
                              "povSwitches": [{"time": 15, "player": C}]}])
        seq = config["sequences"][0]
        self.assertEqual([c["tick"] for c in seq["playerCameras"]], [640 - 32, 960])
        cfg = seq["cfg"].split("\n")
        self.assertEqual(cfg[0], "mirv_cmd clear")
        self.assertIn("mirv_cmd addAtTick 610 spec_mode 3", cfg)
        self.assertIn("mirv_cmd addAtTick 962 spec_mode 3", cfg)
        self.assertEqual(csdv.validate_config(config), [])

    def test_first_person_and_campath_clips_get_no_view_switch(self):
        config = self.build([{"type": "ticks", "start": 6400, "end": 6720, "pov": A, "view": "first"}])
        self.assertEqual(config["sequences"][0]["cfg"], "mirv_cmd clear")
        self.assertEqual(config["sequences"][0]["playerCameras"][0]["tick"], 6400)
        config = self.build([{"type": "ticks", "start": 6400, "end": 6720,
                              "camera": {"shot": "static", "pos": [0, 0, 64], "lookAt": [100, 0, 64]}}])
        self.assertNotIn("spec_mode", config["sequences"][0]["cfg"])
        with self.assertRaises(csdv.CsdvError):
            self.build([{"type": "ticks", "start": 6400, "end": 6720, "pov": A, "view": "side"}])

    def test_first_person_is_default(self):
        config = self.build([{"type": "ticks", "start": 6400, "end": 6720, "pov": A}])
        self.assertEqual(config["sequences"][0]["playerCameras"][0]["tick"], 6400)
        self.assertNotIn("spec_mode", config["sequences"][0]["cfg"])

    def test_cinematic_clip_uses_positions_and_chase_spectating(self):
        import positions

        ticks = np.arange(6000, 7200)
        track = positions.Track(A, "Liam #1", ticks, np.c_[(ticks - 6000) * 3.0, np.zeros(len(ticks)), np.zeros(len(ticks))],
                                np.zeros(len(ticks)), np.zeros(len(ticks)), np.ones(len(ticks), bool), np.zeros(len(ticks)))
        original = positions.load_tracks
        positions.load_tracks = lambda demo: {A: track}
        try:
            config = self.build([{"type": "ticks", "start": 6400, "end": 6720,
                                  "camera": {"shot": "follow", "subject": "liam", "collision": False}}])
        finally:
            positions.load_tracks = original
        seq = config["sequences"][0]
        cfg = seq["cfg"].split("\n")
        self.assertEqual(seq["playerCameras"][0]["playerSteamId"], A)          # spectate the subject...
        self.assertIn(f"mirv_cmd addAtTick {6400 - 32 + 2} spec_mode 3", cfg)   # ...in chase mode (model visible)
        self.assertIn("mirv_cmd addAtTick 6400 mirv_campath offset current", cfg)
        self.assertIn("cl_drawhud 0", cfg)
        self.assertEqual(cfg.count("mirv_cmd clear"), 1)                       # view lines must not wipe the campath sync
        self.assertEqual([p for p in csdv.validate_config(config) if p.startswith("ERROR")], [])

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
