"""Tests for scene analysis and shot suggestions (scene.py)."""

import unittest

import numpy as np

import scene
from positions import Track

TICKRATE = 64


def track(sid, name, xy_fn, seconds=8.0, yaw_fn=None):
    ticks = np.arange(0, int(seconds * TICKRATE))
    t = ticks / TICKRATE
    xy = np.array([xy_fn(x) for x in t], float)
    pos = np.c_[xy, np.zeros(len(t))]
    if yaw_fn is None:
        vel = np.gradient(xy, axis=0)
        yaw = np.degrees(np.arctan2(vel[:, 1], vel[:, 0]))
    else:
        yaw = np.array([yaw_fn(x) for x in t], float)
    n = len(t)
    return Track(sid, name, ticks, pos, yaw, np.zeros(n), np.ones(n, bool), np.zeros(n))


class SceneTest(unittest.TestCase):
    def labels(self, tracks, subject="1"):
        windows = scene.analyze(tracks, subject, 32, 7 * TICKRATE)
        return [scene.window_label(w, None)[0] for w in windows]

    def test_motion_labels(self):
        still = track("1", "A", lambda t: (0.0, 0.0), yaw_fn=lambda t: 0.0)
        walk = track("1", "A", lambda t: (100.0 * t, 0.0))
        run = track("1", "A", lambda t: (240.0 * t, 0.0))
        self.assertEqual(set(self.labels({"1": still})), {"still"})
        self.assertEqual(set(self.labels({"1": walk})), {"walk"})
        self.assertEqual(set(self.labels({"1": run})), {"run"})

    def test_watched_and_chase(self):
        victim = track("1", "Victim", lambda t: (100.0 * t, 0.0))
        watcher = track("2", "Watcher", lambda t: (100.0 * t - 600, 0.0))           # walking behind, same way
        self.assertIn("watched", self.labels({"1": victim, "2": watcher}))
        runner = track("1", "Victim", lambda t: (250.0 * t, 0.0))
        chaser = track("2", "Chaser", lambda t: (250.0 * t - 300, 0.0))
        self.assertIn("chase", self.labels({"1": runner, "2": chaser}))

    def test_searching(self):
        looker = track("1", "A", lambda t: (0.0, 0.0), yaw_fn=lambda t: 80 * np.sin(4 * t))
        self.assertIn("searching", self.labels({"1": looker}))

    def test_horror_shots_only_with_horror_mood_and_tension(self):
        victim = track("1", "Victim", lambda t: (100.0 * t, 0.0))
        watcher = track("2", "Watcher", lambda t: (100.0 * t - 600, 0.0))
        tracks = {"1": victim, "2": watcher}
        horror = scene.plan(tracks, "1", 32, 7 * TICKRATE, mood="horror")
        neutral = scene.plan(tracks, "1", 32, 7 * TICKRATE, mood="neutral")
        watched = [options for beat, options in horror if beat.label == "watched"]
        self.assertTrue(watched and watched[0][0].recipe.horror)             # tense beat -> genre shot first
        self.assertTrue(all(not o.recipe.horror for _, options in neutral for o in options))
        calm = scene.plan({"1": track("1", "A", lambda t: (100.0 * t, 0.0))}, "1", 32, 7 * TICKRATE, mood="horror")
        self.assertTrue(all(not options[0].recipe.horror for _, options in calm if options))  # calm walk: normal

    def test_pacing_rules(self):
        self.assertGreater(scene.pacing([])["anchor"], 1.0)                      # open on a still frame
        after_special = scene.pacing(["anchor", "special"])
        self.assertLess(after_special["special"], 0.2)                           # never two specials in a row
        self.assertGreater(after_special["anchor"], 1.0)                         # breather after a special
        self.assertLess(scene.pacing(["anchor", "move", "move"])["move"], 1.0)  # max two moves in a row
        self.assertGreater(scene.pacing(["move", "move", "special", "move"])["anchor"], 1.0)  # anchors < half

    def test_categories(self):
        by_name = {r.name: r for r in scene.RECIPES}
        self.assertEqual(scene.category(by_name["locked-off wide"]), "anchor")
        self.assertEqual(scene.category(by_name["steadicam follow"]), "move")
        self.assertEqual(scene.category(by_name["vertigo"]), "special")
        self.assertEqual(scene.category(by_name["dutch lock-off"]), "special")
        self.assertEqual(scene.category(by_name["chase handheld"]), "special")
        self.assertFalse(scene.allowed(by_name["camcorder pov"], "horror"))
        self.assertTrue(scene.allowed(by_name["vertigo"], "backrooms"))
        self.assertFalse(scene.allowed(by_name["drone flyover"], "backrooms"))

    def test_plan_keeps_specials_rare_and_apart(self):
        # A long tense stretch (being watched the whole time) must still alternate with calm shots.
        victim = track("1", "Victim", lambda t: (100.0 * t, 0.0), seconds=40)
        watcher = track("2", "Watcher", lambda t: (100.0 * t - 600, 0.0), seconds=40)
        planned = scene.plan({"1": victim, "2": watcher}, "1", 32, 38 * TICKRATE, mood="horror")
        classes = [scene.category(o[0].recipe) for _, o in planned if o]
        self.assertGreaterEqual(len(classes), 5)
        self.assertFalse(any(a == b == "special" for a, b in zip(classes, classes[1:])))
        self.assertLessEqual(classes.count("special") / len(classes), 0.5)

    def test_beats_cover_range_without_overlap(self):
        tr = {"1": track("1", "A", lambda t: (100.0 * t if t < 4 else 400.0, 0.0))}
        beats = scene.segment(scene.analyze(tr, "1", 32, 7 * TICKRATE))
        self.assertGreaterEqual(len(beats), 2)
        for a, b in zip(beats, beats[1:]):
            self.assertLessEqual(a.end, b.start)


if __name__ == "__main__":
    unittest.main()
