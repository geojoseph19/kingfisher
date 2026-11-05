# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared motion helpers: the animation driver and the golden scan line.

Both are drawn with cairo. Animation runs only while the widget is visible in
the focused window, and never when the desktop has animations turned off.
"""

import math
import weakref

import cairo
from gi.repository import Gdk, GLib, Gtk

GOLD = (1.0, 0.70, 0.12)
ORANGE = (1.0, 0.42, 0.06)
GOLD_LIGHT = (1.0, 0.80, 0.20)
AMBER = (0.96, 0.56, 0.06)   # gold that still reads on white
SCAN_PERIOD = 1.1        # seconds per sweep of the scan line
FRAME_MS = 40            # 25 fps is plenty for slow water
RIPPLE_LIFE = 3.0        # seconds a ripple ring stays visible


IDLE_PAUSE_US = 120 * 1_000_000   # freeze ambient motion after 2 min without input

_last_input = GLib.get_monotonic_time()
_animated = weakref.WeakSet()


def note_activity():
    """Called on pointer or key input; wakes animations paused for idleness."""
    global _last_input
    _last_input = GLib.get_monotonic_time()
    for widget in list(_animated):
        widget._update()


def user_idle():
    return GLib.get_monotonic_time() - _last_input > IDLE_PAUSE_US


_setting_watched = False


def _watch_animation_setting():
    """Follow GNOME's "Animations" switch while the app runs."""
    global _setting_watched
    settings = Gtk.Settings.get_default()
    if _setting_watched or settings is None:
        return
    _setting_watched = True
    settings.connect("notify::gtk-enable-animations",
                     lambda *_: [w._update() for w in list(_animated)])


def animations_enabled():
    settings = Gtk.Settings.get_default()
    return settings is None or settings.props.gtk_enable_animations


def scan_progress(elapsed, period=SCAN_PERIOD):
    """Eased 0..1 position of the scan line and whether it is moving down."""
    phase = (elapsed / period) % 2
    t = phase if phase < 1 else 2 - phase
    return t * t * (3 - 2 * t), phase < 1


def splash_rings(after, count=3, spacing=0.45, life=RIPPLE_LIFE):
    """Progress (0..1) of the rings spreading from one splash, ``after``
    seconds after it. A splash makes a fixed few rings, then the water calms."""
    rings = []
    for k in range(count):
        age = after - k * spacing
        if 0 <= age < life:
            rings.append(age / life)
    return rings


def ease_out(p):
    return 1 - (1 - p) ** 3


def draw_scan_line(cr, x, y, w, h, elapsed):
    """Golden line with a soft trail, sweeping over the rectangle."""
    if h < 6 or w < 6:
        return
    t, down = scan_progress(elapsed)
    ly = y + 2 + (h - 4) * t
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    trail = 34 if down else -34
    glow = cairo.LinearGradient(0, ly - trail, 0, ly)
    glow.add_color_stop_rgba(0, *GOLD, 0)
    glow.add_color_stop_rgba(1, *GOLD, 0.22)
    cr.set_source(glow)
    cr.rectangle(x, min(ly, ly - trail), w, abs(trail))
    cr.fill()
    line = cairo.LinearGradient(x, 0, x + w, 0)
    line.add_color_stop_rgba(0, *GOLD, 0)
    line.add_color_stop_rgba(0.2, *GOLD, 1)
    line.add_color_stop_rgba(0.5, *ORANGE, 1)
    line.add_color_stop_rgba(0.8, *GOLD, 1)
    line.add_color_stop_rgba(1, *GOLD, 0)
    cr.set_source(line)
    cr.rectangle(x, ly - 1.5, w, 3)
    cr.fill()
    cr.restore()



def spinner(**props):
    """A spinner that spins only while it is on screen.

    In GTK 4.6 a spinning Gtk.Spinner keeps its window repainting at the full
    frame rate even when it sits on a page that isn't shown, so it is started
    on map and stopped on unmap."""
    widget = Gtk.Spinner(**props)
    widget.connect("map", lambda w: w.start())
    widget.connect("unmap", lambda w: w.stop())
    return widget

class Animated(Gtk.DrawingArea):
    """A drawing area that animates while it can be seen.

    It keeps its own clock, which only advances while the animation runs, so
    after a pause (window minimised, user away) motion resumes from exactly
    where it stopped instead of jumping ahead.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._time = 0.0          # seconds of animation shown so far
        self._last_frame = 0      # monotonic µs of the previous frame
        self._tick = 0
        self.frame_ms = FRAME_MS
        self._root = None
        self._handlers = []
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        _animated.add(self)
        _watch_animation_setting()

    @property
    def elapsed(self):
        return self._time

    def restart(self):
        self._time = 0.0
        self.queue_draw()

    def _on_map(self, *_):
        root = self.get_root()
        if root is not self._root:
            for obj, handler in self._handlers:
                obj.disconnect(handler)
            self._handlers = []
            self._root = root
            self._handlers.append((root, root.connect("notify::is-active", self._on_active_changed)))
            surface = root.get_surface()
            if surface is not None:
                self._handlers.append((surface, surface.connect("notify::state", lambda *_: self._update())))
        self._update()

    def _on_active_changed(self, root, _pspec):
        if root.is_active():
            note_activity()   # coming back to the window counts as input

    def _on_unmap(self, *_):
        self._update()

    def _minimized(self):
        surface = self._root.get_surface() if self._root is not None else None
        try:
            return bool(surface.get_state() & Gdk.ToplevelState.MINIMIZED)
        except (AttributeError, TypeError):
            return False

    def _update(self):
        # Keep moving while the window can be seen, focused or not; a frozen
        # scene behind other windows looks stuck when you glance back.
        run = (self.get_mapped() and animations_enabled() and not user_idle()
               and self._root is not None and not self._minimized())
        if run and not self._tick:
            self._last_frame = GLib.get_monotonic_time()
            # A plain timer, not a tick callback: tick callbacks keep the frame
            # clock at the full refresh rate, which is wasted on calm scenery.
            self._tick = GLib.timeout_add(self.frame_ms, self._on_timer)
        elif not run and self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0

    def _on_timer(self):
        if user_idle():
            self._tick = 0
            return GLib.SOURCE_REMOVE
        now = GLib.get_monotonic_time()
        # Cap each step so a stalled main loop never makes the scene lurch.
        self._time += min(now - self._last_frame, 100_000) / 1e6
        self._last_frame = now
        self.queue_draw()
        return GLib.SOURCE_CONTINUE


class ScanSweep(Animated):
    """Transparent layer that sweeps the golden scan line over its area."""

    def __init__(self):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.set_draw_func(lambda _a, cr, w, h: draw_scan_line(cr, 0, 0, w, h, self.elapsed))


class ButtonGlyph(Gtk.DrawingArea):
    """An icon that acts out its button's action. Subclasses draw from
    ``hover`` and ``press`` (both eased 0..1) and ``phase`` (seconds of hover)."""

    SIZE = 18

    def __init__(self, button):
        # Always its own size, centred: icon-only buttons are wider than the icon.
        super().__init__(content_width=self.SIZE, content_height=self.SIZE,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
        self.hover = 0.0
        self.press = 0.0
        self.phase = 0.0
        self._timer = 0
        self._button = button
        self.set_draw_func(self._on_draw)
        button.connect("state-flags-changed", lambda *_: self._kick())
        self.connect("unmap", lambda *_: self._stop())

    def _targets(self):
        flags = self._button.get_state_flags()
        hover = bool(flags & (Gtk.StateFlags.PRELIGHT | Gtk.StateFlags.FOCUS_VISIBLE))
        press = bool(flags & Gtk.StateFlags.ACTIVE)
        return (1.0 if hover or press else 0.0), (1.0 if press else 0.0)

    def _kick(self):
        if not animations_enabled():
            self.hover, self.press = self._targets()
            self.queue_draw()
            return
        if not self._timer:
            self._timer = GLib.timeout_add(16, self._step)

    def _stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.hover, self.press = self._targets()
        self.queue_draw()

    def _step(self):
        if not self.get_mapped() or user_idle():
            # Hidden, or the pointer has rested here a long time: settle.
            self._timer = 0
            self.hover, self.press = self._targets()
            self.queue_draw()
            return GLib.SOURCE_REMOVE
        hover, press = self._targets()
        # Quick ease toward the target; press snaps in faster than it lets go.
        self.hover += (hover - self.hover) * 0.22
        self.press += (press - self.press) * (0.45 if press else 0.25)
        self.phase = self.phase + 0.016 if hover else 0.0
        self.queue_draw()
        settled = abs(hover - self.hover) < 0.01 and abs(press - self.press) < 0.01
        if settled and not (hover and self.CONTINUOUS):
            self.hover, self.press = hover, press
            self._timer = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    CONTINUOUS = False   # keep animating while hovered (e.g. marching ants)

    def _on_draw(self, _area, cr, width, height):
        color = self.get_style_context().get_color()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        self.draw_glyph(cr, width, height, (color.red, color.green, color.blue, color.alpha))

    def draw_glyph(self, cr, width, height, fg):
        raise NotImplementedError


class SelectGlyph(ButtonGlyph):
    """Select Area: the corners open out and a small gold selection with
    marching ants appears; on press the corners snap in, like the capture."""

    CONTINUOUS = True

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        cx, cy = width / 2, height / 2
        half = 6.4 + 1.9 * h - 2.6 * p
        arm = 4.4 - 0.8 * h
        cr.set_line_width(2)
        cr.set_source_rgba(*fg)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x, y = cx + sx * half, cy + sy * half
            cr.move_to(x, y - sy * arm)
            cr.line_to(x, y)
            cr.line_to(x - sx * arm, y)
        cr.stroke()
        if h > 0.02:
            inner = 3.0 + 1.4 * h - 1.6 * p
            cr.set_source_rgba(*GOLD_LIGHT, min(1.0, h * 1.2))
            cr.set_line_width(1.6)
            cr.set_dash([2.0, 2.0], -(self.phase * 22) % 8)
            cr.rectangle(cx - inner, cy - inner, inner * 2, inner * 2)
            cr.stroke()
            cr.set_dash([])


class ScreenGlyph(ButtonGlyph):
    """Full Screen: gold corners grow from the middle of the monitor out to its
    edges ("all of it"); on press the screen flashes like a capture."""

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        cx = width / 2
        # Monitor body and stand.
        left, top, w, hgt = 1.5, 2.5, width - 3, 10.5
        cr.set_line_width(1.8)
        cr.set_source_rgba(*fg)
        _rounded(cr, left, top, w, hgt, 2.2)
        cr.stroke()
        cr.move_to(cx - 3.2, top + hgt + 3.2)
        cr.line_to(cx + 3.2, top + hgt + 3.2)
        cr.move_to(cx, top + hgt + 0.9)
        cr.line_to(cx, top + hgt + 3.2)
        cr.stroke()
        # Press: a brief flash filling the screen.
        if p > 0.02:
            cr.set_source_rgba(*AMBER, 0.55 * p)
            _rounded(cr, left + 1.6, top + 1.6, w - 3.2, hgt - 3.2, 1.2)
            cr.fill()
        # Hover: gold corners expand from the centre to the screen's edges.
        if h > 0.02:
            iw, ih = w - 4.8, hgt - 4.8
            mx, my = cx, top + hgt / 2
            spread = 0.25 + 0.75 * h
            hx, hy = iw / 2 * spread, ih / 2 * spread
            arm = 2.2
            cr.set_source_rgba(*AMBER, min(1.0, h * 1.3))
            cr.set_line_width(1.4)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                x, y = mx + sx * hx, my + sy * hy
                cr.move_to(x, y - sy * min(arm, hy))
                cr.line_to(x, y)
                cr.line_to(x - sx * min(arm, hx), y)
            cr.stroke()



class OpenGlyph(ButtonGlyph):
    """Open: the folder's front tips open and a gold page rises out of it;
    on press the page settles back in."""

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        left, right, top, bottom = 1.8, width - 1.8, 3.4, height - 2.6
        flap = top + 5.0 + 1.4 * h                   # the front's top edge drops as it opens
        cr.set_line_width(1.8)
        cr.set_source_rgba(*fg)
        # Back, with its tab.
        cr.move_to(left, bottom)
        cr.line_to(left, top)
        cr.line_to(left + 5.0, top)
        cr.line_to(left + 6.6, top + 1.8)
        cr.line_to(right, top + 1.8)
        cr.line_to(right, bottom)
        cr.close_path()
        cr.stroke()
        # The page, only above the front.
        if h > 0.02:
            page_top = top + 3.4 - 4.2 * h + 2.4 * p
            cr.save()
            cr.rectangle(0, 0, width, flap)
            cr.clip()
            _rounded(cr, left + 3.0, page_top, right - left - 6.0, flap - page_top + 2, 1.0)
            cr.set_source_rgba(*AMBER, 0.28 * h)
            cr.fill_preserve()
            cr.set_source_rgba(*AMBER, min(1.0, h * 1.3))
            cr.set_line_width(1.4)
            cr.stroke()
            cr.restore()
        # Front, tipping open.
        cr.set_source_rgba(*fg)
        cr.set_line_width(1.8)
        cr.move_to(left, bottom)
        cr.line_to(left + 2.2 * h, flap)
        cr.line_to(right, flap)
        cr.line_to(right, bottom)
        cr.close_path()
        cr.stroke()


class PasteGlyph(ButtonGlyph):
    """Paste: lines of gold text write themselves onto the clipboard, one
    after another; on press the board dips as if pressed down."""

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        dy = 0.9 * p
        bx, by, bw, bh = 3.2, 3.4 + dy, width - 6.4, height - 5.0
        cr.set_line_width(1.8)
        cr.set_source_rgba(*fg)
        _rounded(cr, bx, by, bw, bh, 2.0)
        cr.stroke()
        _rounded(cr, width / 2 - 2.8, by - 1.7, 5.6, 3.4, 1.2)
        cr.fill()
        x0 = bx + 2.6
        rows = [(by + 5.0 + i * 2.9, (bw - 5.2) * frac) for i, frac in enumerate((0.85, 1.0, 0.55))]
        cr.set_line_width(1.4)
        if h < 0.98:   # resting: faint lines of text
            cr.set_source_rgba(*fg[:3], fg[3] * 0.5 * (1 - h))
            for y, full in rows:
                cr.move_to(x0, y)
                cr.line_to(x0 + full, y)
            cr.stroke()
        if h > 0.02:   # hover: gold lines write in, one after another
            cr.set_source_rgba(*AMBER, min(1.0, h * 1.4))
            cr.set_line_width(1.6)
            for i, (y, full) in enumerate(rows):
                k = min(1.0, max(0.0, h * 1.8 - i * 0.4))
                if k > 0:
                    cr.move_to(x0, y)
                    cr.line_to(x0 + full * k, y)
            cr.stroke()


def _text_lines(cr, rows, x0, h, fg, faint=0.5):
    """Lines of text: faint in the resting colour, written in gold on hover."""
    if h < 0.98:
        cr.set_source_rgba(*fg[:3], fg[3] * faint * (1 - h))
        cr.set_line_width(1.4)
        for y, full in rows:
            cr.move_to(x0, y)
            cr.line_to(x0 + full, y)
        cr.stroke()
    if h > 0.02:
        cr.set_source_rgba(*AMBER, min(1.0, h * 1.4))
        cr.set_line_width(1.6)
        for i, (y, full) in enumerate(rows):
            k = min(1.0, max(0.0, h * 1.8 - i * 0.4))
            if k > 0:
                cr.move_to(x0, y)
                cr.line_to(x0 + full * k, y)
        cr.stroke()


class SaveGlyph(ButtonGlyph):
    """Save: the arrow drops toward the tray and turns gold; a press pushes
    it home and the tray gives a little."""

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        cx = width / 2
        dip = 0.8 * p
        # Tray.
        cr.set_line_width(1.8)
        cr.set_source_rgba(*fg)
        cr.move_to(2.5, 11.0 + dip)
        cr.line_to(2.5, 14.2 + dip)
        cr.line_to(width - 2.5, 14.2 + dip)
        cr.line_to(width - 2.5, 11.0 + dip)
        cr.stroke()
        # Arrow, moving down.
        drop = 1.4 * h + 1.6 * p
        top, tip = 2.2 + drop, 10.6 + drop * 0.55
        for colour, alpha in ((fg[:3], fg[3] * (1 - h)), (AMBER, h)):   # crossfade to gold
            if alpha > 0.01:
                cr.set_source_rgba(*colour, alpha)
                cr.move_to(cx, top)
                cr.line_to(cx, tip)
                cr.move_to(cx - 3.4, tip - 3.4)
                cr.line_to(cx, tip)
                cr.line_to(cx + 3.4, tip - 3.4)
                cr.stroke()


class CopyGlyph(ButtonGlyph):
    """Copy: a gold duplicate slides out from behind the page; a press
    snaps it back on top."""

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        w, hh, r = 9.6, 11.6, 2.0
        fx, fy = 2.2, 4.6                     # the page
        off = 3.0 + 1.6 * h - 2.2 * p         # how far the copy sits up and right
        bx, by = fx + off, fy - off
        # The copy behind: only the part not covered by the page.
        cr.save()
        cr.rectangle(0, 0, width, height)
        _rounded(cr, fx - 1.2, fy - 1.2, w + 2.4, hh + 2.4, r + 1)
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.clip()
        _rounded(cr, bx, by, w, hh, r)
        cr.set_line_width(1.8)
        if h > 0.02:
            cr.set_source_rgba(*AMBER, min(1.0, h * 1.3))
        else:
            cr.set_source_rgba(*fg)
        cr.stroke()
        cr.restore()
        # The page.
        cr.set_source_rgba(*fg)
        cr.set_line_width(1.8)
        _rounded(cr, fx, fy, w, hh, r)
        cr.stroke()


class CheckGlyph(Gtk.DrawingArea):
    """Copied: a check mark that draws itself in when shown."""

    SIZE = 18

    def __init__(self):
        # Always its own size, centred: icon-only buttons are wider than the icon.
        super().__init__(content_width=self.SIZE, content_height=self.SIZE,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
        self.progress = 1.0
        self._timer = 0
        self.set_draw_func(self._on_draw)
        self.connect("unmap", lambda *_: self._finish())

    def play(self):
        if not animations_enabled():
            self._finish()
            return
        self.progress = 0.0
        if not self._timer:
            self._timer = GLib.timeout_add(16, self._step)
        self.queue_draw()

    def _finish(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.progress = 1.0
        self.queue_draw()

    def _step(self):
        self.progress = min(1.0, self.progress + 16 / 260)
        self.queue_draw()
        if self.progress >= 1.0:
            self._timer = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _on_draw(self, _area, cr, width, height):
        color = self.get_style_context().get_color()
        t = ease_out(self.progress)
        a, b, c = (3.4, 9.4), (7.2, 13.2), (14.8, 5.0)
        first = math.dist(a, b)
        total = first + math.dist(b, c)
        length = total * t
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_width(2.0)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.move_to(*a)
        if length <= first:
            k = length / first
            cr.line_to(a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
        else:
            cr.line_to(*b)
            k = (length - first) / (total - first)
            cr.line_to(b[0] + (c[0] - b[0]) * k, b[1] + (c[1] - b[1]) * k)
        cr.stroke()


class WindowGlyph(ButtonGlyph):
    """Open in Kingfisher: lines of text write themselves into the window in gold."""

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        inset = 0.6 * p
        x, y, w, hh = 1.8 + inset, 2.6 + inset, width - 3.6 - 2 * inset, 12.8 - 2 * inset
        cr.set_line_width(1.8)
        cr.set_source_rgba(*fg)
        _rounded(cr, x, y, w, hh, 2.2)
        cr.stroke()
        cr.move_to(x, y + 3.4)
        cr.line_to(x + w, y + 3.4)
        cr.stroke()
        _text_lines(cr, [(y + 6.6, w - 5.2), (y + 9.4, (w - 5.2) * 0.6)], x + 2.6, h, fg)


class LoupeGlyph(ButtonGlyph):
    """Magnifier: the lens grows a little and a gold glint sweeps across the glass."""

    CONTINUOUS = True

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        cx, cy = 7.4, 7.4
        r = 4.8 + 0.6 * h - 0.6 * p
        cr.set_line_width(1.8)
        cr.set_source_rgba(*fg)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        hx, hy = cx + r * 0.72, cy + r * 0.72
        cr.set_line_width(2.4)
        cr.move_to(hx + 0.4, hy + 0.4)
        cr.line_to(width - 2.2, height - 2.2)
        cr.stroke()
        if h > 0.02:
            sweep = (self.phase * 1.1) % 2.2 - 0.6        # glint passing, then a pause
            if 0 <= sweep <= 1:
                cr.save()
                cr.arc(cx, cy, r - 1.4, 0, 2 * math.pi)
                cr.clip()
                gx = cx - r + 2 * r * sweep
                cr.set_source_rgba(*GOLD_LIGHT, min(1.0, h) * 0.85)
                cr.set_line_width(1.6)
                cr.move_to(gx - 2.4, cy + r)
                cr.line_to(gx + 2.4, cy - r)
                cr.stroke()
                cr.restore()


class ExtractGlyph(ButtonGlyph):
    """Extract Text: a gold scan line passes over the T, like the reading
    screen; on press the corners snap in."""

    CONTINUOUS = True

    def draw_glyph(self, cr, width, height, fg):
        h, p = self.hover, self.press
        cx, cy = width / 2, height / 2
        half = 7.2 + 0.6 * h - 1.4 * p
        arm = 3.0
        cr.set_line_width(1.8)
        cr.set_source_rgba(*fg)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x, y = cx + sx * half, cy + sy * half
            cr.move_to(x, y - sy * arm)
            cr.line_to(x, y)
            cr.line_to(x - sx * arm, y)
        cr.stroke()
        # The T.
        cr.set_line_width(2.0)
        cr.move_to(cx - 3.6, cy - 3.6)
        cr.line_to(cx + 3.6, cy - 3.6)
        cr.move_to(cx, cy - 3.6)
        cr.line_to(cx, cy + 4.2)
        cr.stroke()
        if h > 0.02:
            pos, _down = scan_progress(self.phase, period=0.9)
            y = cy - 5.0 + 10.0 * pos
            cr.set_source_rgba(*GOLD_LIGHT, min(1.0, h * 1.3))
            cr.set_line_width(1.4)
            cr.move_to(cx - 5.4, y)
            cr.line_to(cx + 5.4, y)
            cr.stroke()


def _rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
