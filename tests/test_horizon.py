# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from kingfisher.horizon import MIN_GAP, OPTICAL, PAD, SHORE, place  # noqa: E402
from kingfisher.scene import HORIZON  # noqa: E402

SKY, WATER, LIST_OFFSET = 300, 210, 38


def gap(height):
    sky_y, water_y = place(height, SKY, WATER, LIST_OFFSET)
    return water_y - (sky_y + SKY)


class PlaceTests(unittest.TestCase):
    def test_groups_never_closer_than_the_minimum_gap(self):
        for height in range(420, 1600, 20):
            self.assertGreaterEqual(gap(height) + 1e-6, MIN_GAP, height)

    def test_spacing_grows_with_the_window(self):
        self.assertLess(gap(653), gap(850))
        self.assertLess(gap(850), gap(1033))

    def test_list_starts_just_below_the_waterline(self):
        for height in (700, 850, 1033, 1400):
            _, water_y = place(height, SKY, WATER, LIST_OFFSET)
            self.assertAlmostEqual(water_y + LIST_OFFSET, height * HORIZON + SHORE)

    def test_sky_group_is_centred_in_the_sky(self):
        height = 1033
        sky_y, _ = place(height, SKY, WATER, LIST_OFFSET)
        above, below = sky_y, height * HORIZON - (sky_y + SKY)
        self.assertAlmostEqual(above, (above + below) * OPTICAL)
        self.assertLess(above, below)          # a little above the middle

    def test_sky_group_does_not_move_when_history_appears(self):
        for height in (850, 1033):
            self.assertEqual(place(height, SKY)[0], place(height, SKY, WATER, LIST_OFFSET)[0])

    def test_short_pages_keep_their_margins(self):
        for height in (480, 560, 653):
            sky_y, water_y = place(height, SKY, WATER, LIST_OFFSET)
            self.assertGreaterEqual(sky_y, PAD)
            fits = height >= 2 * PAD + SKY + MIN_GAP + WATER
            if fits:
                self.assertLessEqual(water_y + WATER, height - PAD + 1e-6)


if __name__ == "__main__":
    unittest.main()
