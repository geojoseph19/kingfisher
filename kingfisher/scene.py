# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""A calm lake, drawn with cairo: dawn in light mode, a starry night in dark mode.

The still parts (sky, sun or moon, hills and their reflections) are rendered
once per size into a cached surface. When the size changes, the old surface
is stretched to fit at once (the scene is fully proportional, so everything
stays in place) and the exact one is rendered when the size settles: a
resize or maximise never waits for it. Each frame then adds the living parts:
clouds, birds, the sun's shimmer on the water, glints, swaying reeds, drifting
motes, and now and then a kingfisher diving in with a splash and ripples.
"""

import functools
import math
import random

import cairo
from gi.repository import GLib, Gtk

from .effects import RIPPLE_LIFE, Animated, animations_enabled, ease_out, splash_rings

HORIZON = 0.64           # horizon height as a fraction of the scene
DIVE_TIME = 0.55         # seconds from the top of the dive to the water
SPLASH_TIME = 1.0
RESIZE_SETTLE_MS = 150   # re-render the still layer this long after the last size change
GLOW_DOWNSCALE = 4       # the sun's glow is very soft: render it at a quarter of the resolution

DAWN = {
    "sky": ((0.0, (0.78, 0.88, 0.98)), (0.6, (0.90, 0.94, 0.99)), (1.0, (1.00, 0.90, 0.78))),
    "orb": (1.00, 0.84, 0.52), "orb_glow": (1.00, 0.76, 0.40), "orb_glow_alpha": 0.55, "orb_r": 0.034,
    "hills": ((0.76, 0.83, 0.93), (0.63, 0.74, 0.89), (0.48, 0.62, 0.84)),
    "haze": (0.99, 0.93, 0.85),
    "lake": ((0.0, (0.86, 0.89, 0.94)), (0.25, (0.58, 0.75, 0.94)), (1.0, (0.24, 0.49, 0.86))),
    "shimmer": (1.00, 0.80, 0.42), "shimmer_alpha": 1.0, "path_alpha": 0.30, "path_spread": 1.15,
    "glint": (1.0, 1.0, 1.0), "glint_alpha": 0.32,
    "cloud": (1.0, 1.0, 1.0), "cloud_alpha": 0.45, "birds": (0.20, 0.30, 0.46),
    "reeds": (0.10, 0.22, 0.40), "reed_alpha": 0.82,
    "motes": (1.00, 0.86, 0.55), "mote_alpha": 0.45, "stars": 0, "ripple": (1.0, 1.0, 1.0),
}

NIGHT = {
    "sky": ((0.0, (0.02, 0.05, 0.12)), (0.6, (0.07, 0.13, 0.27)), (1.0, (0.19, 0.28, 0.47))),
    "orb": (0.96, 0.95, 0.86), "orb_glow": (0.62, 0.72, 1.00), "orb_glow_alpha": 0.30, "orb_r": 0.026,
    "hills": ((0.10, 0.16, 0.30), (0.07, 0.12, 0.24), (0.05, 0.09, 0.19)),
    "haze": (0.16, 0.24, 0.40),
    "lake": ((0.0, (0.14, 0.21, 0.36)), (0.3, (0.07, 0.13, 0.26)), (1.0, (0.02, 0.05, 0.12))),
    "shimmer": (0.92, 0.93, 0.86), "shimmer_alpha": 0.62, "path_alpha": 0.085, "path_spread": 1.75,
    "glint": (0.75, 0.84, 1.0), "glint_alpha": 0.16,
    "cloud": (0.55, 0.65, 0.85), "cloud_alpha": 0.07, "birds": (0.40, 0.48, 0.65),
    "reeds": (0.01, 0.03, 0.07), "reed_alpha": 0.95,
    "motes": (1.00, 0.86, 0.35), "mote_alpha": 0.85, "stars": 90, "ripple": (0.75, 0.84, 1.0),
}

KINGFISHER_BLUE = (0.10, 0.55, 0.95)
KINGFISHER_ORANGE = (1.0, 0.50, 0.10)


def ridge(x, width, phases, amplitude, peaks=False):
    """Hill line from a few sines. With ``peaks`` the crests fold into ridges,
    which reads as distant mountains rather than rolling hills."""
    u = x / max(width, 1) * 2 * math.pi
    if peaks:
        return amplitude * (0.62 * (1 - 2 * abs(math.sin(u * 1.6 + phases[0])))
                            + 0.26 * (1 - 2 * abs(math.sin(u * 4.3 + phases[1])))
                            + 0.12 * math.sin(u * 11.0 + phases[2]))
    return amplitude * (0.55 * math.sin(u * 1.3 + phases[0])
                        + 0.30 * math.sin(u * 3.1 + phases[1])
                        + 0.15 * math.sin(u * 7.7 + phases[2]))


@functools.lru_cache(maxsize=32)
def dive_plan(index, width, height, seed=11):
    """Start, entry point and side of the ``index``-th dive (deterministic)."""
    rng = random.Random(index * 7919 + seed)
    horizon = height * HORIZON
    # Land left or right of the page's centre column, where it can be seen.
    side_of_page = -1 if rng.random() < 0.5 else 1
    entry_x = width * (0.5 + side_of_page * (0.24 + 0.2 * rng.random()))
    entry_y = horizon + (height - horizon) * (0.28 + 0.32 * rng.random())
    side = -side_of_page if rng.random() < 0.7 else side_of_page
    start = (entry_x + side * width * (0.10 + 0.08 * rng.random()), horizon - height * (0.30 + 0.12 * rng.random()))
    return start, (entry_x, entry_y)


def _gradient(y0, y1, stops, alpha=1.0):
    grad = cairo.LinearGradient(0, y0, 0, y1)
    for offset, rgb in stops:
        grad.add_color_stop_rgba(offset, *rgb, alpha)
    return grad


def _paint_glow(cr, ox, oy, r, pal, width, horizon, scale):
    """The soft glow around the sun or moon. It is smooth enough to render at
    a fraction of the resolution and scale up, and only where it shows."""
    reach = r * 9
    x0, y0 = max(0.0, ox - reach), max(0.0, oy - reach)
    x1, y1 = min(float(width), ox + reach), min(horizon, oy + reach)
    if x1 <= x0 or y1 <= y0:
        return
    k = scale / GLOW_DOWNSCALE                    # small-surface pixels per unit
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil((x1 - x0) * k) + 2, math.ceil((y1 - y0) * k) + 2)
    sc = cairo.Context(small)
    sc.scale(k, k)
    sc.translate(-x0, -y0)
    glow = cairo.RadialGradient(ox, oy, r * 0.5, ox, oy, reach)
    glow.add_color_stop_rgba(0, *pal["orb_glow"], pal["orb_glow_alpha"])
    glow.add_color_stop_rgba(1, *pal["orb_glow"], 0)
    sc.set_source(glow)
    sc.paint()
    cr.save()
    cr.rectangle(x0, y0, x1 - x0, y1 - y0)
    cr.clip()
    cr.translate(x0, y0)
    cr.scale(1 / k, 1 / k)
    pattern = cairo.SurfacePattern(small)
    pattern.set_filter(cairo.FILTER_BILINEAR)
    pattern.set_extend(cairo.EXTEND_PAD)
    cr.set_source(pattern)
    cr.paint()
    cr.restore()


class LakeScene(Animated):
    def __init__(self, dive_every=8.0):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.frame_ms = 40   # 25 fps: plenty for calm scenery
        self.dive_every = dive_every
        self._static = None
        self._static_key = None
        self._settle = 0         # timer for the exact still layer after a resize
        self._splashes = []      # extra splashes: (scene time, (x, y), size)
        self._glow = self._glow_for = None
        rng = random.Random(5)
        self._clouds = [(rng.random(), 0.07 + 0.22 * rng.random(), 6 + 7 * rng.random(), 0.7 + 0.6 * rng.random())
                        for _ in range(4)]
        self._glints = [(rng.random(), rng.random(), rng.random() * 6.3, 4 + 10 * rng.random()) for _ in range(54)]
        self._sparkles = [(rng.random() ** 1.3, max(-2.4, min(2.4, rng.gauss(0, 0.8))), rng.random() * 6.3,
                            0.9 + 1.4 * rng.random(), 0.6 + 0.8 * rng.random()) for _ in range(110)]
        self._motes = [(rng.random(), rng.random(), rng.random() * 6.3, 6 + 10 * rng.random()) for _ in range(20)]
        self._stars = [(rng.random(), rng.random() ** 1.4, rng.random() * 6.3, 0.6 + rng.random()) for _ in range(90)]
        self._reeds = [self._make_reeds(rng, left=True), self._make_reeds(rng, left=False)]
        self._phases = [[rng.random() * 6.3 for _ in range(3)] for _ in range(3)]
        self.set_draw_func(self._draw)
        # Repaint at once when switching light/dark, even while the scene rests.
        self._style_handler = 0
        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)

    def _on_realize(self, *_):
        from gi.repository import Adw  # see _palette
        self._style_handler = Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.queue_draw())

    def _on_unrealize(self, *_):
        if self._settle:
            GLib.source_remove(self._settle)
            self._settle = 0
        if self._style_handler:
            from gi.repository import Adw  # see _palette
            Adw.StyleManager.get_default().disconnect(self._style_handler)
            self._style_handler = 0

    @staticmethod
    def _make_reeds(rng, left):
        stalks = []
        for _ in range(11):
            u = rng.random() * 0.15
            stalks.append((u if left else 1 - u, 0.10 + 0.20 * rng.random(), rng.random() * 6.3,
                           rng.random() < 0.45, 1.4 + rng.random() * 1.6))
        return stalks

    # ------------------------------------------------------------ helpers
    def _palette(self):
        from gi.repository import Adw  # Adw.init needs a display; keep imports headless-safe
        return NIGHT if Adw.StyleManager.get_default().get_dark() else DAWN

    def _orb(self, width, height, pal):
        return width * 0.76, height * HORIZON - height * 0.34, max(14.0, min(width, height) * pal["orb_r"])

    # -------------------------------------------------------- static layer
    def _render_static(self, width, height, pal, scale):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(width * scale)), max(1, int(height * scale)))
        surface.set_device_scale(scale, scale)
        cr = cairo.Context(surface)
        horizon = height * HORIZON

        cr.set_source(_gradient(0, horizon, pal["sky"]))
        cr.rectangle(0, 0, width, horizon + 1)
        cr.fill()

        ox, oy, r = self._orb(width, height, pal)
        _paint_glow(cr, ox, oy, r, pal, width, horizon, scale)
        # The sun or moon itself is drawn each frame (see _draw), so it is
        # exactly round even while a stretched still layer is shown.

        # Lake, then hills and their softened reflections.
        cr.set_source(_gradient(horizon, height, pal["lake"]))
        cr.rectangle(0, horizon, width, height - horizon)
        cr.fill()
        heights = (0.19, 0.105, 0.055)
        amps = (0.085, 0.04, 0.025)
        for i, rgb in enumerate(pal["hills"]):
            base = horizon - height * heights[i]
            amp = height * amps[i]
            step = 6
            for mirror in (False, True):
                cr.move_to(0, horizon)
                for x in range(0, int(width) + step, step):
                    y = base - ridge(x, width, self._phases[i], amp, peaks=i == 0)
                    if mirror:
                        y = horizon + (horizon - y) * 0.55
                    cr.line_to(x, y)
                cr.line_to(width, horizon)
                cr.close_path()
                cr.set_source_rgba(*rgb, 0.22 if mirror else 1.0)
                cr.fill()
            # Haze between hill layers gives depth.
            haze = cairo.LinearGradient(0, horizon - height * 0.06, 0, horizon)
            haze.add_color_stop_rgba(0, *pal["haze"], 0)
            haze.add_color_stop_rgba(1, *pal["haze"], 0.35)
            cr.set_source(haze)
            cr.rectangle(0, horizon - height * 0.06, width, height * 0.06)
            cr.fill()

        # Soft column of light under the sun or moon, widening towards the viewer.
        depth = height - horizon
        y = horizon
        while y < height:
            near = (y - horizon) / depth
            half = r * (1.3 + 4.6 * near) * pal["path_spread"]
            glow = cairo.LinearGradient(ox - half, 0, ox + half, 0)
            alpha = pal["path_alpha"] * (1 - 0.6 * near)
            # Bell-shaped falloff: no hard edge, no bright core line.
            for stop, k in ((0, 0), (0.2, 0.18), (0.35, 0.6), (0.5, 1.0), (0.65, 0.6), (0.8, 0.18), (1, 0)):
                glow.add_color_stop_rgba(stop, *pal["shimmer"], alpha * k)
            cr.set_source(glow)
            cr.rectangle(ox - half, y, 2 * half, 3)
            cr.fill()
            y += 3

        cr.set_source_rgba(*pal["haze"], 0.7)
        cr.set_line_width(1)
        cr.move_to(0, horizon + 0.5)
        cr.line_to(width, horizon + 0.5)
        cr.stroke()
        return surface

    # ----------------------------------------------------------- drawing
    def _draw(self, _area, cr, width, height):
        pal = self._palette()
        t = self.elapsed
        scale = self.get_scale_factor()
        key = (width, height, scale, id(pal))
        if key != self._static_key:
            if self._static is None or self._static_key[2:] != key[2:]:
                # First frame, or a new theme or scale: it must be exact now.
                self._static = self._render_static(width, height, pal, scale)
                self._static_key = key
            else:
                self._settle_later()
        if self._static_key == key:
            cr.set_source_surface(self._static, 0, 0)
            cr.paint()
        else:
            old_w, old_h = self._static_key[:2]
            cr.save()
            cr.scale(width / old_w, height / old_h)
            cr.set_source_surface(self._static, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.paint()
            cr.restore()

        ox, oy, r = self._orb(width, height, pal)
        cr.arc(ox, oy, r, 0, 2 * math.pi)
        cr.set_source_rgb(*pal["orb"])
        cr.fill()

        horizon = height * HORIZON
        if pal["stars"]:
            self._draw_stars(cr, width, horizon, t)
        self._draw_clouds(cr, width, height, pal, t)
        self._draw_birds(cr, width, height, pal, t)
        self._draw_shimmer(cr, width, height, pal, t)
        self._draw_glints(cr, width, height, pal, t)
        self._draw_dives(cr, width, height, pal, t)
        self._draw_motes(cr, width, height, pal, t)
        self._draw_reeds(cr, width, height, pal, t)

    def _settle_later(self):
        if self._settle:
            GLib.source_remove(self._settle)
        self._settle = GLib.timeout_add(RESIZE_SETTLE_MS, self._render_settled)

    def _render_settled(self):
        self._settle = 0
        width, height, scale = self.get_width(), self.get_height(), self.get_scale_factor()
        if width > 0 and height > 0:
            pal = self._palette()
            self._static = self._render_static(width, height, pal, scale)
            self._static_key = (width, height, scale, id(pal))
            self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _draw_stars(self, cr, width, horizon, t):
        for u, v, phase, size in self._stars:
            alpha = 0.35 + 0.45 * (0.5 + 0.5 * math.sin(t * 1.7 * size + phase))
            cr.arc(u * width, v * horizon * 0.85, 0.6 * size, 0, 2 * math.pi)
            cr.set_source_rgba(0.92, 0.94, 1.0, alpha)
            cr.fill()

    def _draw_clouds(self, cr, width, height, pal, t):
        span = width + 400
        for u, v, speed, size in self._clouds:
            x = (u * span + speed * t) % span - 200
            y = v * height
            w = 120 * size
            cr.set_source_rgba(*pal["cloud"], pal["cloud_alpha"])
            for dx, dy, rx, ry in ((0, 0, 0.5, 0.16), (-0.28, 0.05, 0.3, 0.12), (0.3, 0.04, 0.32, 0.12),
                                   (0.05, -0.08, 0.26, 0.14)):
                cr.save()
                cr.translate(x + dx * w, y + dy * w)
                cr.scale(rx * w, ry * w)
                cr.arc(0, 0, 1, 0, 2 * math.pi)
                cr.restore()
                cr.fill()

    def _draw_birds(self, cr, width, height, pal, t):
        cr.set_source_rgba(*pal["birds"], 0.75)
        cr.set_line_width(1.4)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for offset, row, size in ((0.0, 0.16, 6.0), (0.35, 0.20, 4.6), (0.42, 0.13, 5.2)):
            cycle = 34.0
            p = ((t / cycle) + offset) % 1.0
            x = -40 + p * (width + 80)
            y = height * row + math.sin(t * 0.8 + offset * 9) * 8
            flap = math.sin(t * 7 + offset * 20) * 0.45
            cr.move_to(x - size, y - size * flap)
            cr.curve_to(x - size * 0.5, y - size * 0.6, x - size * 0.15, y - size * 0.15, x, y)
            cr.curve_to(x + size * 0.15, y - size * 0.15, x + size * 0.5, y - size * 0.6, x + size, y - size * flap)
            cr.stroke()

    def _draw_shimmer(self, cr, width, height, pal, t):
        """The sun or moon's path on the water: many small glints scattered
        around a centre line, widening towards the viewer, each catching the
        light on its own rhythm. The soft glow under them is in the static layer."""
        ox, _, r = self._orb(width, height, pal)
        horizon = height * HORIZON
        depth = height - horizon
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for v, g, phase, freq, size in self._sparkles:
            y = horizon + 3 + v ** 1.5 * (depth - 6)
            spread = r * (0.6 + 3.8 * v) * pal["path_spread"]
            # Glints drift sideways with the swell instead of sitting on one line.
            sway = math.sin(t * 0.5 + phase) * (1 + 3 * v) + math.sin(y * 0.045 + t * 0.35) * (2 + 7 * v)
            x = ox + g * spread + sway
            # Mostly dim, briefly bright: how light catches moving water.
            catch = (0.5 + 0.5 * math.sin(t * freq + phase)) ** 3
            alpha = pal["shimmer_alpha"] * (1 - 0.5 * v) * (0.15 + 0.85 * catch) * math.exp(-g * g * 0.35)
            half = (1.2 + 6.5 * v) * size * (0.6 + 0.4 * catch)
            cr.move_to(x - half, y)
            cr.line_to(x + half, y)
            cr.set_source_rgba(*pal["shimmer"], alpha)
            cr.set_line_width(0.8 + 1.3 * v)
            cr.stroke()

    def _draw_glints(self, cr, width, height, pal, t):
        horizon = height * HORIZON
        depth = height - horizon
        span = width + 120
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for u, v, phase, speed in self._glints:
            near = v ** 1.5
            y = horizon + 4 + near * (depth - 8)
            length = 6 + near * 46
            x = (u * span + t * speed * (0.3 + near)) % span - 60
            alpha = pal["glint_alpha"] * (0.5 + 0.5 * math.sin(t * 1.1 + phase)) * (0.35 + 0.65 * near)
            cr.move_to(x, y)
            cr.line_to(x + length, y)
            cr.set_source_rgba(*pal["glint"], alpha)
            cr.set_line_width(0.8 + near * 1.4)
            cr.stroke()

    def _draw_dives(self, cr, width, height, pal, t):
        if not self.dive_every:
            return
        horizon = height * HORIZON
        latest = int(t // self.dive_every)
        for index in range(max(0, latest - 3), latest + 1):
            local = t - index * self.dive_every - 1.2   # a calm moment before the first dive
            if local < 0:
                continue
            start, entry = dive_plan(index, width, height)
            near = (entry[1] - horizon) / (height - horizon)
            if local < DIVE_TIME:
                self._draw_kingfisher(cr, start, entry, (local / DIVE_TIME) ** 1.7)
                continue
            self._draw_entry(cr, entry, local - DIVE_TIME, near, pal, index)
        # Splashes asked for from outside (the start page icon's kingfisher).
        self._splashes = [s for s in self._splashes if t - s[0] < RIPPLE_LIFE + 1.0]
        for i, (t0, entry, size) in enumerate(self._splashes):
            near = (entry[1] - horizon) / (height - horizon) * size
            self._draw_entry(cr, entry, t - t0, near, pal, 1000 + i)

    def splash_at(self, x, y, small=False):
        """A splash and rings at (x, y), in the scene's own style."""
        self._splashes.append((self._time, (x, y), 0.55 if small else 1.0))
        self.queue_draw()

    def _draw_entry(self, cr, entry, after, near, pal, index):
        if after < 0:
            return
        if after < SPLASH_TIME:
            self._draw_splash(cr, entry, after, near, pal, index)
        if after > RIPPLE_LIFE + 1.0:
            return
        for i, p in enumerate(splash_rings(after)):
            # Rings spread fast, then slow and fade as the water settles.
            radius = (6 + ease_out(p) * (130 - i * 22)) * (0.6 + near)
            cr.save()
            cr.translate(*entry)
            cr.scale(1, 0.22)
            cr.arc(0, 0, radius, 0, 2 * math.pi)
            cr.restore()
            fade_in = min(1.0, p / 0.06)
            cr.set_source_rgba(*pal["ripple"], 0.6 * fade_in * (1 - p) ** 1.6)
            cr.set_line_width((1.0 + near) * (1.4 - 0.6 * p))
            cr.stroke()

    @staticmethod
    def _draw_kingfisher(cr, start, entry, p):
        """A small kingfisher, wings folded, diving into the lake."""
        def at(q):
            # Slight arc: the bird drops more steeply near the water.
            return (start[0] + (entry[0] - start[0]) * q,
                    start[1] + (entry[1] - start[1]) * q ** 1.25)
        head = at(p)
        ahead = at(min(1.0, p + 0.02))
        tail = at(max(0.0, p - 0.28))

        trail = cairo.LinearGradient(*tail, *head)
        trail.add_color_stop_rgba(0, *KINGFISHER_BLUE, 0)
        trail.add_color_stop_rgba(1, *KINGFISHER_BLUE, 0.35)
        cr.set_source(trail)
        cr.set_line_width(2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(*tail)
        cr.line_to(*head)
        cr.stroke()

        cr.save()
        cr.translate(*head)
        cr.rotate(math.atan2(ahead[1] - head[1], ahead[0] - head[0]) if p < 1 else math.pi / 2)
        # Streamlined body: x runs from tail (negative) to beak (positive).
        cr.move_to(-13, 0)
        cr.curve_to(-8, -4.6, 2, -4.8, 6, -2.2)
        cr.line_to(6, 2.2)
        cr.curve_to(2, 4.8, -8, 4.6, -13, 0)
        cr.close_path()
        cr.set_source_rgb(*KINGFISHER_BLUE)
        cr.fill()
        # Orange underside.
        cr.move_to(-9, 1.0)
        cr.curve_to(-4, 4.4, 2, 4.2, 5.4, 2.0)
        cr.line_to(5.4, 0.6)
        cr.curve_to(0, 1.6, -5, 1.6, -9, 1.0)
        cr.close_path()
        cr.set_source_rgb(*KINGFISHER_ORANGE)
        cr.fill()
        # Long dark beak.
        cr.move_to(5.5, -1.6)
        cr.line_to(14, 0)
        cr.line_to(5.5, 1.6)
        cr.close_path()
        cr.set_source_rgb(0.10, 0.12, 0.16)
        cr.fill()
        # Bright eye and a light stripe along the back.
        cr.arc(3.2, -1.2, 0.9, 0, 2 * math.pi)
        cr.set_source_rgb(1, 1, 1)
        cr.fill()
        cr.move_to(-10, -1.2)
        cr.curve_to(-5, -3.4, 0, -3.4, 3, -2.4)
        cr.set_source_rgba(0.55, 0.88, 1.0, 0.9)
        cr.set_line_width(1.1)
        cr.stroke()
        cr.restore()

    @staticmethod
    def _draw_splash(cr, entry, after, near, pal, index):
        rng = random.Random(index)
        q = after / SPLASH_TIME
        size = 0.7 + near
        # A short white crown where the bird went in.
        if q < 0.45:
            crown = ease_out(q / 0.45)
            cr.save()
            cr.translate(*entry)
            cr.scale(1, 0.35)
            cr.arc(0, 0, 4 + 14 * crown * size, math.pi, 2 * math.pi)
            cr.restore()
            cr.set_source_rgba(*pal["ripple"], 0.8 * (1 - crown))
            cr.set_line_width(2)
            cr.stroke()
        for _ in range(10):
            vx = (rng.random() - 0.5) * 70 * size
            vy = -(55 + rng.random() * 60) * size
            x = entry[0] + vx * after
            y = entry[1] + vy * after + 190 * after * after
            if y > entry[1] + 2:
                continue
            cr.arc(x, y, (1.0 + 0.8 * rng.random()) * size, 0, 2 * math.pi)
            cr.set_source_rgba(*pal["ripple"], 0.8 * (1 - q) ** 1.5)
            cr.fill()

    def _mote_glow(self, pal):
        """One glow pattern per palette, reused for every mote and frame."""
        if self._glow_for is not pal:
            glow = cairo.RadialGradient(0, 0, 0, 0, 0, 6)
            glow.add_color_stop_rgba(0, *pal["motes"], 1)
            glow.add_color_stop_rgba(1, *pal["motes"], 0)
            self._glow, self._glow_for = glow, pal
        return self._glow

    def _draw_motes(self, cr, width, height, pal, t):
        band = height * 0.5
        glow = self._mote_glow(pal)
        for u, v, phase, speed in self._motes:
            y = height - ((v * band + t * speed) % band)
            x = u * width + math.sin(t * 0.5 + phase) * 22
            life = (height - y) / band
            alpha = pal["mote_alpha"] * math.sin(math.pi * life) * (0.45 + 0.55 * math.sin(t * 2.2 + phase) ** 2)
            if alpha <= 0.01:
                continue
            cr.save()
            cr.translate(x, y)
            cr.arc(0, 0, 6, 0, 2 * math.pi)
            cr.clip()
            cr.set_source(glow)
            cr.paint_with_alpha(alpha)
            cr.restore()

    def _draw_reeds(self, cr, width, height, pal, t):
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(*pal["reeds"], pal["reed_alpha"])
        for cluster in self._reeds:
            for u, h, phase, cattail, thick in cluster:
                bx = u * width
                top = height * h
                sway = math.sin(t * 1.1 + phase) * (4 + top * 0.06) + math.sin(t * 2.7 + phase * 2) * 1.5
                tip = (bx + sway, height - top)
                cr.move_to(bx, height + 4)
                cr.curve_to(bx, height - top * 0.45, bx + sway * 0.45, height - top * 0.8, *tip)
                cr.set_line_width(thick)
                cr.stroke()
                if cattail:
                    cr.save()
                    cr.translate(tip[0] - sway * 0.04, tip[1] + 9)
                    cr.rotate(sway * 0.012)
                    cr.scale(2.6, 9)
                    cr.arc(0, 0, 1, 0, 2 * math.pi)
                    cr.restore()
                    cr.fill()
            # A couple of long leaves bending in the breeze.
            for i, (u, h, phase, _c, _t) in enumerate(cluster[:4]):
                bx = u * width
                lean = (1 if u < 0.5 else -1) * (18 + i * 6)
                sway = math.sin(t * 0.9 + phase + 1) * 5
                cr.move_to(bx, height + 4)
                cr.curve_to(bx + lean * 0.2, height - height * h * 0.5,
                            bx + lean * 0.8 + sway, height - height * h * 0.6,
                            bx + lean * 1.4 + sway, height - height * h * 0.42)
                cr.set_line_width(2.2)
                cr.stroke()


class ThemeSwatch(Gtk.DrawingArea):
    """A tiny version of the lake for the style switcher: dawn for light, night
    for dark, half of each for "follow system". The sun or moon rises a little
    on hover, and the chosen option gets an accent ring."""

    SIZE = 28

    def __init__(self, button, kind):
        super().__init__(content_width=self.SIZE + 6, content_height=self.SIZE + 6,
                         halign=Gtk.Align.CENTER, can_target=False)
        self.kind = kind                 # "default" | "light" | "dark"
        self._button = button
        self._rise = 0.0
        self._timer = 0
        self.set_draw_func(self._draw)
        button.connect("state-flags-changed", lambda *_: self._kick())
        button.connect("notify::active", lambda *_: self.queue_draw())

    def _target(self):
        flags = self._button.get_state_flags()
        return 1.0 if flags & (Gtk.StateFlags.PRELIGHT | Gtk.StateFlags.FOCUS_VISIBLE) else 0.0

    def _kick(self):
        if not animations_enabled():
            self._rise = self._target()
            self.queue_draw()
        elif not self._timer:
            self._timer = GLib.timeout_add(16, self._step)

    def _step(self):
        target = self._target()
        self._rise += (target - self._rise) * 0.2
        self.queue_draw()
        if abs(target - self._rise) < 0.01:
            self._rise = target
            self._timer = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _draw(self, _area, cr, width, height):
        cx, cy, r = width / 2, height / 2, self.SIZE / 2
        rise = self._rise * r * 0.10
        cr.save()
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.clip()
        if self.kind == "default":
            # One disc split along the diagonal: dawn and sun / night and moon.
            for night in (False, True):
                cr.save()
                _diagonal_half(cr, cx, cy, r, night)
                cr.clip()
                _sky(cr, cx, cy, r, night)
                # Each half-disc's centre of mass lies 4/(3π) of the radius
                # from the middle, along the perpendicular to the split.
                off = r * 4 / (3 * math.pi) / math.sqrt(2)
                if night:
                    _crescent(cr, cx + off, cy + off - rise, r * 0.30)
                else:
                    cr.arc(cx - off, cy - off - rise, r * 0.24, 0, 2 * math.pi)
                    cr.set_source_rgb(*SUN)
                    cr.fill()
                cr.restore()
        elif self.kind == "dark":
            _sky(cr, cx, cy, r, night=True)
            _crescent(cr, cx, cy - rise, r * 0.46)
        else:
            _sky(cr, cx, cy, r, night=False)
            cr.arc(cx, cy - rise, r * 0.40, 0, 2 * math.pi)
            cr.set_source_rgb(*SUN)
            cr.fill()
        cr.restore()

        # Ring: hairline normally, accent when chosen.
        ctx = self.get_style_context()
        if self._button.get_active():
            found, accent = ctx.lookup_color("accent_bg_color")
            rgba = (accent.red, accent.green, accent.blue, 1.0) if found else (0.11, 0.44, 0.88, 1.0)
            cr.set_source_rgba(*rgba)
            cr.set_line_width(2)
            cr.arc(cx, cy, r + 2, 0, 2 * math.pi)
        else:
            fg = ctx.get_color()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.16)
            cr.set_line_width(1)
            cr.arc(cx, cy, r - 0.5, 0, 2 * math.pi)
        cr.stroke()


SUN = (1.00, 0.74, 0.22)
MOON = (0.96, 0.95, 0.86)
# The crescent is a disc with an offset disc cut away (offsets in disc radii).
CUT_DX, CUT_DY, CUT_R = 0.50, -0.30, 0.86


def _crescent_bounds_centre(samples=200):
    """Centre of the crescent's bounding box relative to its disc, in disc
    radii. Icons centre a crescent by its outer bounds; centring its centre of
    mass instead pulls the thick part to the middle and pushes the tips out."""
    xs, ys = [], []
    for i in range(samples):
        for j in range(samples):
            x = -1 + 2 * (i + 0.5) / samples
            y = -1 + 2 * (j + 0.5) / samples
            if x * x + y * y <= 1 and (x - CUT_DX) ** 2 + (y - CUT_DY) ** 2 > CUT_R ** 2:
                xs.append(x)
                ys.append(y)
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


# Precomputed with _crescent_bounds_centre() (checked by the tests), so
# starting the app does not pay for the sampling.
CRESCENT_CENTRE = (-0.06, 0.0)


def _crescent(cr, cx, cy, radius):
    """A crescent whose outer bounds are centred on (cx, cy)."""
    ox = cx - CRESCENT_CENTRE[0] * radius
    oy = cy - CRESCENT_CENTRE[1] * radius
    cr.push_group()
    cr.arc(ox, oy, radius, 0, 2 * math.pi)
    cr.set_source_rgb(*MOON)
    cr.fill()
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.arc(ox + CUT_DX * radius, oy + CUT_DY * radius, CUT_R * radius, 0, 2 * math.pi)
    cr.fill()
    cr.set_operator(cairo.OPERATOR_OVER)
    cr.pop_group_to_source()
    cr.paint()


def _sky(cr, cx, cy, r, night):
    top, bottom = ((0.09, 0.15, 0.30), (0.17, 0.26, 0.45)) if night else ((0.84, 0.91, 0.99), (1.00, 0.93, 0.82))
    grad = cairo.LinearGradient(0, cy - r, 0, cy + r)
    grad.add_color_stop_rgb(0, *top)
    grad.add_color_stop_rgb(1, *bottom)
    cr.set_source(grad)
    cr.paint()


def _diagonal_half(cr, cx, cy, r, lower_right):
    if lower_right:
        cr.move_to(cx + r, cy - r)
        cr.line_to(cx + r, cy + r)
        cr.line_to(cx - r, cy + r)
    else:
        cr.move_to(cx - r, cy - r)
        cr.line_to(cx + r, cy - r)
        cr.line_to(cx - r, cy + r)
    cr.close_path()
