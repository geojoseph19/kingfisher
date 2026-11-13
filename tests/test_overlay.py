# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest import mock

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from kingfisher import overlay  # noqa: E402

WIDTH, HEIGHT = 200, 100
WHOLE = (0, 0, WIDTH, HEIGHT)
OLD_BOX = (10, 10, 50, 40)      # a selection made before switching to Screen


class ReadingAreaTests(unittest.TestCase):
    """While text is read, the overlay marks the area being read, which is
    not always the selection the window still remembers."""

    def draw(self, mode, state, sel, reading):
        """Draw one overlay window over a white screen; returns
        (is this point dimmed?, the rectangle the scan line swept, the framed rectangle)."""
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, WIDTH, HEIGHT)
        cr = cairo.Context(surface)

        def white(context):
            context.set_source_rgb(1, 1, 1)
            context.paint()

        window = mock.Mock(sel=sel, reading=reading, state=state)
        window.selector.mode = mode
        window._paint_backdrop.side_effect = white
        window._loupe_visible.return_value = False
        overlay.OverlayWindow._draw(window, None, cr, WIDTH, HEIGHT)
        surface.flush()
        data, stride = surface.get_data(), surface.get_stride()

        def dimmed(x, y):
            return data[y * stride + x * 4] < 250

        scanned = window._draw_scan.call_args[0][1:] if window._draw_scan.called else None
        framed = window._draw_selection.call_args[0][2] if window._draw_selection.called else None
        return dimmed, scanned, framed

    def test_whole_screen_is_not_shown_as_the_old_selection(self):
        # Select an area, switch to Screen, extract: everything is read.
        dimmed, scanned, framed = self.draw("screen", "busy", sel=OLD_BOX, reading=WHOLE)
        self.assertEqual(scanned, WHOLE)
        self.assertIsNone(framed)                 # no box around the old selection
        self.assertFalse(dimmed(150, 80))         # nothing outside it is dimmed either
        self.assertFalse(dimmed(30, 30))

    def test_selected_area_is_marked_while_it_is_read(self):
        dimmed, _scanned, framed = self.draw("selection", "busy", sel=OLD_BOX, reading=OLD_BOX)
        self.assertEqual(framed, OLD_BOX)
        self.assertFalse(dimmed(30, 30))
        self.assertTrue(dimmed(150, 80))

    def test_screen_that_is_not_read_shows_no_selection(self):
        # A second monitor that still remembers a selection of its own.
        dimmed, scanned, framed = self.draw("selection", "busy", sel=OLD_BOX, reading=None)
        self.assertIsNone(scanned)
        self.assertIsNone(framed)
        self.assertTrue(dimmed(30, 30))
        self.assertTrue(dimmed(150, 80))

    def test_nothing_selected_reads_and_marks_the_whole_screen(self):
        _dimmed, scanned, framed = self.draw("selection", "busy", sel=None, reading=WHOLE)
        self.assertEqual(scanned, WHOLE)
        self.assertIsNone(framed)

    def test_selecting_is_unchanged(self):
        dimmed, scanned, framed = self.draw("selection", "idle", sel=OLD_BOX, reading=None)
        self.assertEqual(framed, OLD_BOX)
        self.assertIsNone(scanned)
        self.assertTrue(dimmed(150, 80))
        dimmed, scanned, framed = self.draw("screen", "idle", sel=OLD_BOX, reading=None)
        self.assertIsNone(framed)
        self.assertFalse(dimmed(150, 80))

    def test_set_busy_remembers_what_is_read(self):
        for active, expected in ((True, WHOLE), (False, None)):
            window = mock.Mock(_scan_tick=1)
            window.busy.get_preferred_size.return_value = (None, mock.Mock(width=80, height=30))
            with mock.patch.object(overlay.Gdk.Cursor, "new_from_name"):
                overlay.OverlayWindow.set_busy(window, active, list(WHOLE))
            self.assertEqual(window.reading, expected)
            self.assertEqual(window.state, "busy")

    def test_covers(self):
        self.assertTrue(overlay.covers(WHOLE, WIDTH, HEIGHT))
        self.assertTrue(overlay.covers((-1, 0, WIDTH + 2, HEIGHT), WIDTH, HEIGHT))
        self.assertFalse(overlay.covers(OLD_BOX, WIDTH, HEIGHT))
        self.assertFalse(overlay.covers((0, 0, WIDTH, HEIGHT - 1), WIDTH, HEIGHT))


if __name__ == "__main__":
    unittest.main()
