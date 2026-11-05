# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import math
import random
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from kingfisher import ICON_DIR  # noqa: E402
from kingfisher.start_icon import FlightPlan, FollowFlight, IconParts, _flap  # noqa: E402
from kingfisher.scene import HORIZON  # noqa: E402

W, H, HOME = 1280, 760, (640, 150)


def plans(n=60):
    return [FlightPlan(HOME, W, H, random.Random(seed)) for seed in range(n)]


class FlightTests(unittest.TestCase):
    def test_trips_vary(self):
        trips = plans()
        self.assertEqual({p.side for p in trips}, {1, -1})
        self.assertEqual({p.style for p in trips}, {"dive", "skim"})
        self.assertGreater(len({p.letter for p in trips}), 1)

    def test_motion_is_continuous(self):
        for plan in plans(20):
            steps = int(plan.total * 60)
            prev = plan.at(0.0)
            for i in range(1, steps + 1):
                pose = plan.at(i / 60)
                if pose.visible and prev.visible:
                    self.assertLess(math.hypot(pose.x - prev.x, pose.y - prev.y), 60, (plan.style, i))
                prev = pose

    def test_splashes_are_on_the_water(self):
        for plan in plans(20):
            for _t, (x, y), _small in plan.splashes:
                self.assertGreater(y, H * HORIZON - 6)
                self.assertTrue(0.12 * W < x < 0.88 * W)

    def test_ends_home_facing_left(self):
        for plan in plans(20):
            end = plan.at(plan.total + 0.1)
            self.assertEqual((end.x, end.y, end.flip, end.wings), (*HOME, 1.0, 1.0))
            near_end = plan.at(plan.total - 1e-3)
            self.assertAlmostEqual(near_end.x, HOME[0], delta=1)
            self.assertAlmostEqual(near_end.y, HOME[1], delta=2)

    def test_wings_beat_between_up_and_down(self):
        values = [_flap(i / 200) for i in range(200)]
        self.assertAlmostEqual(max(values), 1.0, places=2)
        self.assertLess(min(values), -0.5)


class AvoidTests(unittest.TestCase):
    # The recent results on a 1080 x 653 start page: 600 px wide, from just above the horizon.
    CARDS = (240, 388, 840, 600)

    def inside(self, x, y, margin):
        x0, y0, x1, y1 = self.CARDS
        return x0 - margin < x < x1 + margin and y0 - margin < y < y1 + margin

    def test_flights_go_around_the_cards(self):
        for seed in range(80):
            plan = FlightPlan((540, 110), 1080, 653, random.Random(seed), avoid=self.CARDS)
            for i in range(int(plan.total * 120)):
                pose = plan.at(i / 120)
                if pose.visible:
                    self.assertFalse(self.inside(pose.x, pose.y, 12), (seed, plan.style, plan.side, i / 120))

    def test_no_room_beside_the_cards_keeps_flying(self):
        plan = FlightPlan((300, 110), 600, 653, random.Random(3), avoid=(0, 388, 600, 600))
        self.assertGreater(plan.total, 3)


class FollowTests(unittest.TestCase):
    def run_for(self, bird, seconds, dt=1 / 60):
        top = 0.0
        for _ in range(int(seconds / dt)):
            bird.step(dt)
            top = max(top, math.hypot(bird.vx, bird.vy))
            if bird.done:
                break
        return top

    def test_settles_beside_the_pointer_with_the_beak_towards_it(self):
        bird = FollowFlight(HOME, 63, 46, (300, 400))
        bird.moved(300, 400)
        top = self.run_for(bird, 3.0)
        self.assertLessEqual(top, FollowFlight.MAX_SPEED + 1)
        pose = bird.pose()
        self.assertAlmostEqual(pose.x, 300 + bird.half_w + 8, delta=4)     # to the right of it...
        self.assertGreater(pose.flip, 0.9)                                  # ...facing left, at it
        self.assertLess(math.hypot(bird.vx, bird.vy), 40)

    def test_follows_to_the_other_side(self):
        bird = FollowFlight(HOME, 63, 46, (1000, 300))
        bird.moved(1000, 300)
        self.run_for(bird, 3.0)
        self.assertLess(bird.pose().x, 1000)
        self.assertLess(bird.pose().flip, -0.9)

    def test_early_clicks_do_not_end_it(self):
        bird = FollowFlight(HOME, 63, 46, HOME)
        self.run_for(bird, 0.5)
        self.assertFalse(bird.engaged)                 # still the clicks that started it
        bird.moved(HOME[0] + 20, HOME[1] + 10)
        self.run_for(bird, 1.0)
        self.assertFalse(bird.engaged)                 # hardly led anywhere yet
        bird.moved(HOME[0] + 200, HOME[1] + 150)
        self.run_for(bird, 0.2)
        self.assertTrue(bird.engaged)

    def test_clicking_in_place_counts_after_a_while(self):
        bird = FollowFlight(HOME, 63, 46, HOME)
        self.run_for(bird, FollowFlight.ENGAGE_TIME + 0.2)
        self.assertTrue(bird.engaged)

    def test_a_click_sends_it_home_to_land(self):
        bird = FollowFlight(HOME, 63, 46, (300, 400))
        bird.moved(300, 400)
        self.run_for(bird, 1.0)
        bird.go_home()
        self.run_for(bird, 5.0)
        self.assertTrue(bird.done)
        pose = bird.pose()
        self.assertAlmostEqual(pose.x, HOME[0], delta=1)
        self.assertAlmostEqual(pose.y, HOME[1], delta=1)
        self.assertAlmostEqual(pose.flip, 1.0, places=3)

    def test_goes_home_by_itself_when_left_alone(self):
        bird = FollowFlight(HOME, 63, 46, (300, 400))
        bird.moved(300, 400)
        self.run_for(bird, FollowFlight.IDLE_RETURN + 5)
        self.assertTrue(bird.done)


class IconPartsTests(unittest.TestCase):
    def test_the_shipped_icon_splits_into_its_parts(self):
        parts = IconParts(str(ICON_DIR / "hicolor" / "scalable" / "apps" / "kingfisher.svg"))
        self.assertGreater(parts.bird.get_width(), 100)
        self.assertEqual(len(parts.bird_rect), 4)
        self.assertTrue(any(cmd == "C" for cmd, _ in parts.sel_path))
        self.assertNotIn('id="bird"', parts._tile_svg)
        self.assertNotIn('id="selection"', parts._tile_svg)


if __name__ == "__main__":
    unittest.main()
