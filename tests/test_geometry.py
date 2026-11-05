# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from kingfisher.geometry import Monitor, ScreenLayout


class SingleMonitorTests(unittest.TestCase):
    def test_identity(self):
        layout = ScreenLayout([Monitor(0, 0, 1920, 1080)], (1920, 1080))
        self.assertEqual(layout.to_image_box(0, (100, 50, 200, 100)), (100, 50, 300, 150))

    def test_hidpi_scale_2(self):
        # Logical 1920x1080 monitor captured at 3840x2160.
        layout = ScreenLayout([Monitor(0, 0, 1920, 1080)], (3840, 2160))
        self.assertEqual(layout.to_image_box(0, (100, 50, 200, 100)), (200, 100, 600, 300))

    def test_fractional_scale(self):
        layout = ScreenLayout([Monitor(0, 0, 1536, 864)], (1920, 1080))  # 125 %
        self.assertEqual(layout.to_image_box(0, (0, 0, 1536, 864)), (0, 0, 1920, 1080))

    def test_clamped_to_image(self):
        layout = ScreenLayout([Monitor(0, 0, 100, 100)], (100, 100))
        self.assertEqual(layout.to_image_box(0, (90, 90, 50, 50)), (90, 90, 100, 100))

    def test_no_monitors_falls_back_to_image(self):
        layout = ScreenLayout([], (800, 600))
        self.assertEqual(layout.to_image_box(0, (0, 0, 800, 600)), (0, 0, 800, 600))


class MultiMonitorTests(unittest.TestCase):
    def test_side_by_side(self):
        layout = ScreenLayout([Monitor(0, 0, 1920, 1080), Monitor(1920, 0, 1920, 1080)], (3840, 1080))
        self.assertEqual(layout.to_image_box(1, (10, 20, 100, 50)), (1930, 20, 2030, 70))

    def test_stacked_different_sizes(self):
        layout = ScreenLayout([Monitor(0, 0, 2560, 1440), Monitor(320, 1440, 1920, 1080)], (2560, 2520))
        self.assertEqual(layout.to_image_box(1, (0, 0, 1920, 1080)), (320, 1440, 2240, 2520))

    def test_secondary_left_of_primary(self):
        # The layout origin is the left-most / top-most monitor, not monitor 0.
        layout = ScreenLayout([Monitor(1920, 200, 1920, 1080), Monitor(0, 0, 1920, 1080)], (3840, 1280))
        self.assertEqual(layout.offset(0), (1920, 200))
        self.assertEqual(layout.to_image_box(0, (0, 0, 10, 10)), (1920, 200, 1930, 210))

    def test_negative_coordinates(self):
        layout = ScreenLayout([Monitor(-1280, 0, 1280, 1024), Monitor(0, 0, 1920, 1080)], (3200, 1080))
        self.assertEqual(layout.to_image_box(0, (0, 0, 1280, 1024)), (0, 0, 1280, 1024))
        self.assertEqual(layout.to_image_box(1, (0, 0, 100, 100)), (1280, 0, 1380, 100))

    def test_hidpi_dual(self):
        layout = ScreenLayout([Monitor(0, 0, 1920, 1080), Monitor(1920, 0, 1920, 1080)], (7680, 2160))
        self.assertEqual(layout.to_image_box(1, (0, 0, 100, 100)), (3840, 0, 4040, 200))


class LayoutCoordinateTests(unittest.TestCase):
    def setUp(self):
        self.layout = ScreenLayout([Monitor(0, 0, 1920, 1080), Monitor(1920, 0, 1280, 1024)], (3200, 1080))

    def test_round_trip(self):
        g = self.layout.to_layout(1, (100, 100, 300, 200))
        self.assertEqual(g, (2020, 100, 300, 200))
        self.assertEqual(self.layout.from_layout(g), (1, (100, 100, 300, 200)))

    def test_selection_spanning_monitors_is_not_restored(self):
        self.assertIsNone(self.layout.from_layout((1800, 100, 300, 100)))

    def test_selection_on_unplugged_monitor_is_not_restored(self):
        self.assertIsNone(self.layout.from_layout((5000, 100, 100, 100)))


if __name__ == "__main__":
    unittest.main()
