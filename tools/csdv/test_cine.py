"""Tests for cinematic cameras (cine.py) and map geometry ray tests (mapgeo.py). Run with the csdv tests."""

import unittest

import numpy as np

import cine
import mapgeo
from positions import Track

TICKRATE = 64


def straight_track(speed=200.0, seconds=8.0, direction=(1.0, 0.0)):
    ticks = np.arange(0, int(seconds * TICKRATE))
    d = np.array(direction) / np.linalg.norm(direction)
    xy = np.outer(ticks / TICKRATE * speed, d)
    pos = np.c_[xy, np.zeros(len(ticks))]
    yaw = np.full(len(ticks), np.degrees(np.arctan2(d[1], d[0])))
    n = len(ticks)
    return Track("1", "Runner", ticks, pos, yaw, np.zeros(n), np.ones(n, bool), np.zeros(n))


def wall(x0, y0, x1, y1, z0=-100.0, z1=400.0):
    """A vertical wall as two triangles."""
    a, b, c, d = (x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1)
    tris = np.array([[a, b, c], [a, c, d]], dtype=float)
    return tris


def geometry(*walls, floor=True):
    tris = list(walls)
    if floor:
        tris.append(np.array([[(-5000, -5000, 0), (5000, -5000, 0), (5000, 5000, 0)],
                              [(-5000, -5000, 0), (5000, 5000, 0), (-5000, 5000, 0)]], dtype=float))
    tris = np.concatenate(tris)
    return mapgeo.MapGeometry(tris, np.ones(len(tris), bool), "test")


class GeometryTest(unittest.TestCase):
    def test_ray_hits_wall_and_floor(self):
        g = geometry(wall(100, -500, 100, 500))
        self.assertAlmostEqual(g.first_hit((0, 0, 50), (200, 0, 50)), 0.5, places=6)
        self.assertIsNone(g.first_hit((0, 0, 50), (50, 0, 50)))
        self.assertAlmostEqual(g.floor_z(10, 10, 100), 0.0, places=6)


class EntityTest(unittest.TestCase):
    TEXT = """====0====
classname                      worldspawn
====1====
classname                      prop_dynamic
model                          resource_name:"models/props/crate.vmdl
origin                         [ 100.0, 200.0, 8.0 ]
angles                         [ 0.0, 90.0, 0.0 ]
scales                         [ 2.0, 2.0, 2.0 ]
====2====
classname                      prop_dynamic
model                          resource_name:"models/props/hidden.vmdl
rendermode                     10
====3====
classname                      trigger_multiple
model                          resource_name:"maps/x/entities/unnamed_1.vmdl
"""

    def test_parse_and_filter(self):
        entities = mapgeo.parse_entities(self.TEXT)
        self.assertEqual(len(entities), 4)
        solid = mapgeo.solid_entities(entities)
        self.assertEqual([e["model"] for e in solid], ['resource_name:"models/props/crate.vmdl'])
        self.assertEqual(mapgeo._model_path(solid[0]["model"]), "models/props/crate.vmdl_c")
        self.assertEqual(list(mapgeo._vec(solid[0]["origin"])), [100.0, 200.0, 8.0])

    def test_angle_matrix_matches_source(self):
        m = mapgeo.angle_matrix(0, 90, 0)
        self.assertTrue(np.allclose(m @ [1, 0, 0], [0, 1, 0]))     # yaw 90: forward -> +y
        m = mapgeo.angle_matrix(90, 0, 0)
        self.assertTrue(np.allclose(m @ [1, 0, 0], [0, 0, -1]))    # pitch 90: forward -> straight down


class ShotTest(unittest.TestCase):
    def test_thick_arm_pulls_in_for_walls_beside_the_lens(self):
        # Doorway: two wall stubs at x=-100 leaving a 30-unit gap on the centre line. The centre ray is clear,
        # but the frame edges would be filled by the door frame, so the camera must move in front of it.
        g = geometry(wall(-100, 15, -100, 500), wall(-100, -500, -100, -15))
        track = straight_track(speed=0.0)
        r = cine.build("follow", track, 64, 320, TICKRATE, {"distance": 200.0, "height": 0.0}, g)
        self.assertTrue((r.cam[:, 0] > -100).all())
    def test_follow_stays_behind_and_looks_at_subject(self):
        r = cine.build("follow", straight_track(), 64, 320, TICKRATE, {}, None)
        self.assertGreaterEqual(len(r.keys), 4)
        mid = len(r.keys) // 2
        self.assertLess(r.cam[mid, 0], r.subject[mid, 0] - 100)           # behind (subject runs +x)
        yaw = r.keys[mid].yaw % 360
        self.assertLess(min(yaw, 360 - yaw), 3.0)                           # looking +x
        self.assertAlmostEqual(r.keys[0].t, 0.0)
        self.assertAlmostEqual(r.keys[-1].t, (320 - 64) / TICKRATE)

    def test_side_auto_picks_the_open_side(self):
        # Wall 60 units to the subject's left (+y): the camera must go to the right (-y).
        g = geometry(wall(-1000, 60, 3000, 60))
        r = cine.build("side", straight_track(), 64, 320, TICKRATE, {}, g)
        self.assertLess(r.cam[:, 1].mean(), -150)
        self.assertEqual(r.visible, 1.0)

    def test_wall_pulls_camera_in(self):
        g = geometry(wall(-1000, 60, 3000, 60))
        r = cine.build("side", straight_track(), 64, 320, TICKRATE, {"angle": "left"}, g)
        self.assertTrue((r.cam[:, 1] < 60 - cine.CAMERA_MARGIN + 5).all())    # never through the wall
        self.assertGreater(r.pulled, 0.9)
        self.assertTrue(r.warnings)

    def test_tripod_is_fixed_and_pans(self):
        r = cine.build("tripod", straight_track(), 64, 320, TICKRATE, {"pos": [300, -400, 60]}, None)
        self.assertTrue(np.allclose(r.cam, r.cam[0]))
        self.assertGreater(abs(r.keys[-1].yaw - r.keys[0].yaw), 20)
        self.assertGreater(r.keys[0].fov, 5)

    def test_teleport_is_reported(self):
        track = straight_track()
        track.pos[200:, 0] += 5000
        r = cine.build("follow", track, 64, 320, TICKRATE, {}, None)
        self.assertTrue(any("teleports" in w for w in r.warnings))

    def test_bad_input(self):
        with self.assertRaises(cine.ShotError):
            cine.build("spin", straight_track(), 64, 320, TICKRATE)
        with self.assertRaises(cine.ShotError):
            cine.build("follow", straight_track(seconds=2), 64, 320, TICKRATE)


if __name__ == "__main__":
    unittest.main()
