# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""The start page layout: content placed against the lake's horizon."""

from gi.repository import Gdk, GLib, Gtk

from .scene import HORIZON

PAD = 20          # smallest margin above and below the content
MIN_GAP = 40      # smallest gap between the sky and water groups (inner gaps are 14-24)
OPTICAL = 0.45    # share of the free sky above the sky group: a little above centre
SHORE = 8         # the water list starts this far below the waterline


def place(height, sky_h, water_h=0, list_offset=0):
    """Return (sky_y, water_y) for a page `height` tall.

    Both groups are tied to the scenery, so the page scales with the window:
    the sky group is centred (optically) in the sky above the horizon, and
    the list inside the water group, which starts `list_offset` below the
    group's top, begins just below the waterline. The sky group sits in the
    same place with or without history. When the window is too short for
    that, the groups keep MIN_GAP apart (the sky group moves up first, then
    the water group down) and the page scrolls."""
    horizon = height * HORIZON
    sky_y = max(PAD, (horizon - sky_h) * OPTICAL)
    if not water_h:
        return sky_y, 0
    water_y = horizon + SHORE - list_offset
    water_y = min(water_y, height - PAD - water_h)      # keep the list in view
    overlap = sky_y + sky_h + MIN_GAP - water_y
    if overlap > 0:
        sky_y -= min(overlap, sky_y - PAD)
        water_y = max(water_y, sky_y + sky_h + MIN_GAP)
    return sky_y, water_y


class HorizonLayout(Gtk.Widget):
    """Lays out a sky group and a water group with `place`.

    `water_list` is the part of the water group that must stay off the
    horizon. `on_width` is called (from idle, never during layout) when the
    width changes, so the page can adapt, e.g. hide button labels."""

    def __init__(self, sky, water, water_list, on_width=None):
        super().__init__()
        self.sky, self.water, self.water_list = sky, water, water_list
        self._on_width = on_width
        self._width = -1
        self._width_idle = 0
        sky.set_parent(self)
        water.set_parent(self)

    def do_dispose(self):
        if self._width_idle:
            GLib.source_remove(self._width_idle)
            self._width_idle = 0
        for child in (self.sky, self.water):
            if child.get_parent() is self:
                child.unparent()
        Gtk.Widget.do_dispose(self)

    def _water_shown(self):
        return self.water.get_visible()

    def do_measure(self, orientation, for_size):
        sky = self.sky.measure(orientation, for_size)
        water = self.water.measure(orientation, for_size) if self._water_shown() else (0, 0, -1, -1)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return max(sky[0], water[0]), max(sky[1], water[1]), -1, -1
        extra = 2 * PAD + (MIN_GAP if self._water_shown() else 0)
        return sky[0] + water[0] + extra, sky[1] + water[1] + extra, -1, -1

    def do_size_allocate(self, width, height, baseline):
        sky_h = self.sky.measure(Gtk.Orientation.VERTICAL, width)[1]
        water_h = list_offset = 0
        if self._water_shown():
            water_h = self.water.measure(Gtk.Orientation.VERTICAL, width)[1]
            list_offset = water_h - self.water_list.measure(Gtk.Orientation.VERTICAL, width)[1]
        sky_y, water_y = place(height, sky_h, water_h, list_offset)
        self.sky.size_allocate(_rect(0, sky_y, width, sky_h), -1)
        if water_h:
            self.water.size_allocate(_rect(0, water_y, width, water_h), -1)
        if width != self._width and self._on_width:
            self._width = width
            if not self._width_idle:
                self._width_idle = GLib.idle_add(self._report_width)

    def _report_width(self):
        self._width_idle = 0
        self._on_width(self._width)
        return GLib.SOURCE_REMOVE


def _rect(x, y, width, height):
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), int(width), int(height)
    return rect
