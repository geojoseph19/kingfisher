# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Map between monitor coordinates and screenshot pixels.

A screenshot covers the bounding box of every monitor. Monitors report
logical (scaled) coordinates while the image is in device pixels, so each
axis has its own scale factor. Kept free of GTK so it can be unit-tested.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    x: int
    y: int
    width: int
    height: int


class ScreenLayout:
    def __init__(self, monitors, image_size):
        img_w, img_h = image_size
        self.image_size = (img_w, img_h)
        self.monitors = list(monitors) or [Monitor(0, 0, img_w, img_h)]
        self.origin_x = min(m.x for m in self.monitors)
        self.origin_y = min(m.y for m in self.monitors)
        self.width = max(m.x + m.width for m in self.monitors) - self.origin_x
        self.height = max(m.y + m.height for m in self.monitors) - self.origin_y
        self.scale_x = img_w / max(self.width, 1)
        self.scale_y = img_h / max(self.height, 1)

    def offset(self, index):
        """Top-left of a monitor in screenshot pixels."""
        m = self.monitors[index]
        return (m.x - self.origin_x) * self.scale_x, (m.y - self.origin_y) * self.scale_y

    def to_image_point(self, index, x, y):
        ox, oy = self.offset(index)
        return ox + x * self.scale_x, oy + y * self.scale_y

    def to_image_box(self, index, rect):
        """Monitor-local (x, y, w, h) → clamped (left, top, right, bottom) in pixels."""
        x, y, w, h = rect
        left, top = self.to_image_point(index, x, y)
        right, bottom = self.to_image_point(index, x + w, y + h)
        img_w, img_h = self.image_size
        return (max(0, math.floor(left)), max(0, math.floor(top)),
                min(img_w, math.ceil(right)), min(img_h, math.ceil(bottom)))

    def to_layout(self, index, rect):
        """Monitor-local rect → rect relative to the layout origin."""
        m = self.monitors[index]
        x, y, w, h = rect
        return (x + m.x - self.origin_x, y + m.y - self.origin_y, w, h)

    def from_layout(self, rect):
        """Layout rect → (monitor index, local rect) for the monitor that
        fully contains it, or None."""
        gx, gy, w, h = rect
        for index, m in enumerate(self.monitors):
            x = gx - (m.x - self.origin_x)
            y = gy - (m.y - self.origin_y)
            if x >= 0 and y >= 0 and x + w <= m.width and y + h <= m.height:
                return index, (x, y, w, h)
        return None
