# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from kingfisher import effects  # noqa: E402
from kingfisher.effects import RIPPLE_LIFE, ease_out, scan_progress, splash_rings  # noqa: E402
from kingfisher.scene import CRESCENT_CENTRE, HORIZON, _crescent_bounds_centre, dive_plan, ridge  # noqa: E402


class ScanTests(unittest.TestCase):
    def test_sweeps_down_then_up(self):
        self.assertEqual(scan_progress(0, period=1), (0, True))
        pos, down = scan_progress(0.5, period=1)
        self.assertAlmostEqual(pos, 0.5)
        self.assertTrue(down)
        pos, down = scan_progress(1.5, period=1)
        self.assertAlmostEqual(pos, 0.5)
        self.assertFalse(down)

    def test_stays_in_range(self):
        for i in range(200):
            pos, _ = scan_progress(i * 0.037)
            self.assertGreaterEqual(pos, 0)
            self.assertLessEqual(pos, 1)


class RippleTests(unittest.TestCase):
    def test_a_splash_makes_a_few_rings_then_calms(self):
        self.assertEqual(splash_rings(-0.1), [])
        self.assertEqual(len(splash_rings(1.0)), 3)
        self.assertEqual(splash_rings(RIPPLE_LIFE + 2), [])

    def test_rings_never_restart(self):
        # Each ring's progress only grows: no ring flickers back to the start.
        previous = None
        for step in range(0, 400):
            rings = splash_rings(step * 0.01)
            if previous and len(rings) == len(previous):
                self.assertTrue(all(a >= b for a, b in zip(rings, previous, strict=True)))
            previous = rings

    def test_ease_out(self):
        self.assertEqual(ease_out(0), 0)
        self.assertEqual(ease_out(1), 1)
        self.assertGreater(ease_out(0.5), 0.5)


class IdleTests(unittest.TestCase):
    def test_motion_rests_when_idle_and_wakes_on_input(self):
        from gi.repository import GLib
        effects._last_input = GLib.get_monotonic_time() - effects.IDLE_PAUSE_US - 1
        self.assertTrue(effects.user_idle())
        effects.note_activity()
        self.assertFalse(effects.user_idle())


class SceneTests(unittest.TestCase):
    def test_dives_land_in_the_lake_from_the_sky(self):
        for i in range(50):
            (sx, sy), (ex, ey) = dive_plan(i, 1200, 800)
            self.assertLess(sy, 800 * HORIZON)
            self.assertGreater(ey, 800 * HORIZON)
            self.assertLess(ey, 800)
            self.assertTrue(0 < ex < 1200)

    def test_dive_plan_is_repeatable(self):
        self.assertEqual(dive_plan(7, 900, 600), dive_plan(7, 900, 600))

    def test_stored_crescent_centre_matches_the_geometry(self):
        x, y = _crescent_bounds_centre(samples=120)
        self.assertAlmostEqual(x, CRESCENT_CENTRE[0], places=1)
        self.assertAlmostEqual(y, CRESCENT_CENTRE[1], places=1)

    def test_ridge_is_bounded(self):
        for x in range(0, 1500, 13):
            self.assertLessEqual(abs(ridge(x, 1500, (0.3, 1.1, 2.0), 40)), 40 + 1e-9)


if __name__ == "__main__":
    unittest.main()
