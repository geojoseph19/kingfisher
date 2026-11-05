# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""The start page icon, drawn from its parts (tile, selection, bird), and the
bird's flights over the page."""

import base64
import math
import random
import re

import cairo
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from .effects import animations_enabled, ease_out, note_activity
from .scene import HORIZON

CANVAS = 1254              # the icon's coordinate space
CLICKS = 3                 # clicks that send the kingfisher fishing
CLICK_GAP = 1.2            # seconds allowed between those clicks
GOLD = (1.0, 0.72, 0.16)


# ------------------------------------------------------------------ the icon
class IconParts:
    """The app icon split into tile (pixbuf), selection (path) and bird (pixbuf)."""

    def __init__(self, svg_path):
        with open(svg_path, encoding="utf-8") as f:
            text = f.read()
        image = re.search(r'<image id="bird" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"[^>]*'
                          r'href="data:image/png;base64,([^"]+)"', text)
        sel = re.search(r'<path id="selection" d="([^"]+)" stroke="(#[0-9a-f]{6})" fill="none" '
                        r'stroke-width="([\d.]+)" stroke-linecap="round" stroke-dasharray="([\d.]+) ([\d.]+)" '
                        r'stroke-dashoffset="([\d.]+)"', text)
        self.bird_rect = tuple(float(v) for v in image.groups()[:4])
        loader = GdkPixbuf.PixbufLoader()
        loader.write(base64.b64decode(image.group(5)))
        loader.close()
        self.bird = loader.get_pixbuf()
        self.sel_path = _parse_path(sel.group(1))
        c = sel.group(2)
        self.sel_colour = tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
        self.sel_width = float(sel.group(3))
        self.sel_dash = (float(sel.group(4)), float(sel.group(5)))
        self.sel_offset = float(sel.group(6))
        # The tile alone: the SVG without the selection and the bird.
        self._tile_svg = re.sub(r'<image id="bird"[^>]*/>', "", re.sub(r'<path id="selection"[^>]*/>', "", text))
        self._tiles = {}
        self._birds = {}

    def tile(self, pixels):
        if pixels not in self._tiles:
            loader = GdkPixbuf.PixbufLoader()
            loader.set_size(pixels, pixels)
            loader.write(self._tile_svg.encode())
            loader.close()
            self._tiles[pixels] = loader.get_pixbuf()
        return self._tiles[pixels]

    def bird_surface(self, width_px):
        """The bird as a cairo surface about `width_px` wide (cached in steps)."""
        step = max(16, int(math.ceil(width_px / 16) * 16))
        if step not in self._birds:
            h = round(self.bird.get_height() * step / self.bird.get_width())
            scaled = self.bird.scale_simple(step, h, GdkPixbuf.InterpType.HYPER)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, step, h)
            cr = cairo.Context(surface)
            Gdk.cairo_set_source_pixbuf(cr, scaled, 0, 0)
            cr.paint()
            self._birds[step] = surface
        return self._birds[step]

    def draw_selection(self, cr, march=0.0, glint=0.0):
        cr.save()
        _replay(cr, self.sel_path)
        cr.set_source_rgb(*self.sel_colour)
        cr.set_line_width(self.sel_width)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        period = sum(self.sel_dash)
        cr.set_dash(list(self.sel_dash), (self.sel_offset - march * period) % period)
        cr.stroke_preserve()
        if glint > 0:
            cr.set_source_rgba(1, 1, 1, 0.55 * glint)
            cr.stroke()
        cr.new_path()
        cr.restore()


def _parse_path(d):
    tokens = re.findall(r"[MHVCLZ]|-?[\d.]+", d)
    out, i = [], 0
    while i < len(tokens):
        cmd = tokens[i]
        n = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Z": 0}[cmd]
        out.append((cmd, [float(v) for v in tokens[i + 1:i + 1 + n]]))
        i += 1 + n
    return out


def _replay(cr, path):
    x = y = 0.0
    for cmd, v in path:
        if cmd in ("M", "L"):
            x, y = v
            (cr.move_to if cmd == "M" else cr.line_to)(x, y)
        elif cmd in ("H", "V"):
            x, y = (v[0], y) if cmd == "H" else (x, v[0])
            cr.line_to(x, y)
        elif cmd == "C":
            cr.curve_to(*v)
            x, y = v[4], v[5]
        elif cmd == "Z":
            cr.close_path()


# ------------------------------------------------------------- the flight
BEAK = math.radians(37)    # in the artwork the beak points left and 37 degrees down
FLAP_HZ = 6.0

# The artwork's wings (both) and the bit of back they hide, in its own pixels
# (986 x 715). The wings beat by scaling about the shoulder line.
ART_WIDTH = 986
WINGS = ((372, 335), (372, 0), (986, 0), (986, 480), (700, 478), (600, 462), (470, 418), (445, 370))
BACK = ((430, 470), (470, 405), (560, 420), (700, 470), (640, 520), (500, 520))
SHOULDER_Y = 430


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def _smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def _wrap(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


def _facing(motion_angle, flip, damp=1.0):
    """Rotation that points the beak along the motion (flip 1 faces left, -1 right)."""
    beak = math.pi - BEAK if flip > 0 else BEAK
    return _wrap(motion_angle - beak) * damp


def _flap(t, hz=FLAP_HZ):
    """Wing scale through a beat: 1 is up (as in the icon), negative is down."""
    return 0.22 + 0.78 * math.cos(2 * math.pi * hz * t)


class Pose:
    __slots__ = ("x", "y", "scale", "angle", "flip", "wings", "visible", "carrying")

    def __init__(self, x, y, scale=1.0, angle=0.0, flip=1.0, wings=1.0, visible=True, carrying=False):
        self.x, self.y, self.scale, self.angle, self.flip = x, y, scale, angle, flip
        self.wings, self.visible, self.carrying = wings, visible, carrying


class FlightPlan:
    """One trip from the icon to the lake and back, a little different each time.

    side: 1 goes right (into the sun's or moon's path), -1 goes left.
    style: "dive" goes under and bursts out; "skim" runs along the surface."""

    def __init__(self, home, width, height, rng, avoid=None):
        """avoid: (x0, y0, x1, y1) of page content the bird must not fly over
        (the recent results); it dives beside it and crosses the sky above it."""
        self.home = home
        self.side = rng.choice((1, -1))
        self.style = "dive" if rng.random() < 0.67 else "skim"
        self.letter = rng.choice("TAKa")
        horizon = height * HORIZON
        water = height - horizon
        cx = width / 2
        skim_len = width * 0.08
        # Open water to dive into: beside the content to avoid, up to the reeds.
        lanes = {}
        for side in (1, -1):
            if avoid is None:
                lanes[side] = (cx + side * width * 0.22, cx + side * width * 0.42)
                continue
            lo, hi = (avoid[2] + 44, width * 0.94) if side > 0 else (width * 0.06, avoid[0] - 44)
            if hi - lo >= 24:
                lanes[side] = (lo, hi) if side > 0 else (hi, lo)        # (nearest the content, farthest)
        if self.side not in lanes and -self.side in lanes:
            self.side = -self.side
        lane = lanes.get(self.side)
        if lane is None:                                             # no room beside it: the old path
            lane = (cx + self.side * width * 0.27, cx + self.side * width * 0.32)
            avoid = None
        near, far = lane
        if self.style == "skim" and abs(far - near) < skim_len + 20:
            self.style = "dive"
        if self.style == "dive":
            ex = near + (far - near) * (0.15 + 0.5 * rng.random())
        else:
            ex = near + self.side * 10
        ey = horizon + water * (0.24 + 0.16 * rng.random())
        self.entry = (ex, ey)
        self.out_flip, self.back_flip = -self.side, self.side
        arc = 0.8 + 0.4 * rng.random()
        hx, hy = home
        ceiling = (avoid[1] - 36) if avoid else horizon         # control points stay above this
        self.lift = (hx, hy - 22)
        if self.style == "dive":
            self.durations = {"takeoff": 0.32, "out": 1.2, "dive": 0.34, "under": 0.7, "burst": 0.3, "back": 1.35, "land": 0.45}
            self.surface_end = (ex + self.side * width * 0.012, ey + 2)
        else:
            self.durations = {"takeoff": 0.32, "out": 1.25, "dive": 0.3, "under": 0.85, "burst": 0.28, "back": 1.35, "land": 0.45}
            self.surface_end = (ex + self.side * skim_len, ey - 3)
        # The last stretch before the water comes down beside the content, from above it.
        self.out_end = (ex - self.side * width * 0.01, min(ey - water * (0.55 if self.style == "dive" else 0.35) * arc,
                                                          ceiling))
        self.out_path = (self.lift, (hx + self.side * width * 0.18 * arc, min(hy - height * 0.10 * arc, ceiling)),
                         (ex, min(ey - water * 1.25 * arc, ceiling)), self.out_end)
        sx, sy = self.surface_end
        # Home: straight up beside the content, then over it.
        self.back_path = ((sx, sy - 26), (sx + self.side * width * 0.02, min(horizon - height * 0.30 * arc, ceiling - 40)),
                          (hx + self.side * width * 0.20, min(hy - height * 0.16 * arc, ceiling)), (hx, hy - 10))
        self.marks = {}
        t = 0.0
        for name in ("takeoff", "out", "dive", "under", "burst", "back", "land"):
            self.marks[name] = (t, t + self.durations[name])
            t += self.durations[name]
        self.total = t
        # Where the water is touched, and when: (time, (x, y), small).
        d0, d1 = self.marks["dive"][1], self.marks["under"][1]
        if self.style == "dive":
            self.splashes = [(d0, self.entry, False), (d1, self.surface_end, True)]
        else:
            n = 5
            self.splashes = [(d0 + (d1 - d0) * i / n,
                              (ex + (sx - ex) * i / n, ey + (sy - ey) * i / n), True) for i in range(n + 1)]

    def _phase(self, t):
        for name, (a, b) in self.marks.items():
            if t < b:
                return name, (t - a) / (b - a)
        return "done", 1.0

    def at(self, t):
        name, k = self._phase(t)
        hx, hy = self.home
        if name == "takeoff":
            e = ease_out(k)
            flip = 1.0 + (self.out_flip - 1.0) * _smooth(k) if self.out_flip < 0 else 1.0
            return Pose(hx, hy + (self.lift[1] - hy) * e, 1.0 + 0.12 * e, 0.12 * e * (1 if flip > 0 else -1),
                        flip, 1.0 + (_flap(t, 7) - 1.0) * e)
        if name == "out":
            q = _smooth(k)
            x, y = _bezier(*self.out_path, q)
            ax, ay = _bezier(*self.out_path, min(1.0, q + 0.03))
            return Pose(x, y, 1.12 - 0.5 * q, _facing(math.atan2(ay - y, ax - x), self.out_flip, 0.6),
                        self.out_flip, _flap(t))
        ex, ey = self.entry
        if name == "dive":
            q = k ** 1.6
            px, py = self.out_end
            if self.style == "dive":
                x, y = px + (ex - px) * q, py + (ey - py) * q
                angle = _facing(math.atan2(ey - py, ex - px), self.out_flip)
                return Pose(x, y, 0.62 - 0.12 * q, angle, self.out_flip, 0.3)          # wings tucked
            x, y = px + (ex - px) * q, py + (ey - 4 - py) * q
            return Pose(x, y, 0.62 - 0.08 * q, _facing(math.atan2(ey - py, ex - px), self.out_flip, 0.5),
                        self.out_flip, 0.6 + 0.4 * math.cos(math.pi * 4 * k))
        if name == "under":
            if self.style == "dive":
                return Pose(ex, ey, visible=False)
            sx, sy = self.surface_end                                                 # skimming
            x, y = ex + (sx - ex) * k, ey - 4 + (sy - ey) * k + math.sin(k * math.pi * 3) * 1.5
            return Pose(x, y, 0.54, 0.0, self.out_flip, 0.55 + 0.45 * math.cos(2 * math.pi * 9 * t),
                        carrying=k > 0.5)
        if name == "burst":
            e = ease_out(k)
            sx, sy = self.surface_end
            y = sy + (self.back_path[0][1] - sy) * e
            nose_up = (1.2 - 0.7 * k) if self.style == "dive" else (0.6 - 0.4 * k)
            return Pose(sx, y, 0.5 + 0.06 * e, nose_up * (1 if self.back_flip > 0 else -1), self.back_flip,
                        _flap(t, 9), carrying=True)
        if name == "back":
            q = _smooth(k)
            x, y = _bezier(*self.back_path, q)
            ax, ay = _bezier(*self.back_path, min(1.0, q + 0.03))
            glide = _smooth((k - 0.7) / 0.3)                  # flaps, then glides in with wings up
            wings = _flap(t) * (1 - glide) + glide
            return Pose(x, y, 0.56 + 0.5 * q, _facing(math.atan2(ay - y, ax - x), self.back_flip, 0.5),
                        self.back_flip, wings, carrying=True)
        if name == "land":
            bounce = math.sin(k * math.pi) * 5 * (1 - k)
            lx, ly = self.back_path[3]
            flip = self.back_flip + (1.0 - self.back_flip) * _smooth(k * 1.6)      # turn to face left again
            return Pose(lx, ly + (hy - ly) * ease_out(k) - bounce, 1.06 - 0.06 * ease_out(k), 0.0, flip,
                        carrying=k < 0.5)
        return Pose(hx, hy)


def draw_bird(cr, surface, wings):
    """The bird with its wings at `wings` (1 up as drawn, 0 edge-on, negative down)."""
    f = surface.get_width() / ART_WIDTH

    def outline(points):
        cr.save()
        cr.scale(f, f)
        cr.move_to(*points[0])
        for p in points[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.restore()

    if abs(wings - 1.0) < 0.02:
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        return
    cr.save()                                     # body: everything but the wings
    cr.rectangle(0, 0, surface.get_width(), surface.get_height())
    outline(WINGS)
    cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    cr.clip()
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    cr.restore()
    cr.save()                                     # the back the wings hide in the artwork
    outline(BACK)
    back = cairo.LinearGradient(450 * f, 400 * f, 700 * f, 520 * f)
    back.add_color_stop_rgb(0, 0.10, 0.62, 0.98)
    back.add_color_stop_rgb(1, 0.05, 0.36, 0.86)
    cr.set_source(back)
    cr.fill()
    cr.restore()
    cr.save()                                     # wings, beating about the shoulder line
    cr.translate(0, SHOULDER_Y * f)
    cr.scale(1, wings if abs(wings) > 0.03 else 0.03)
    cr.translate(0, -SHOULDER_Y * f)
    outline(WINGS)
    cr.clip()
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    cr.restore()


class FollowFlight:
    """The bird flies after the pointer and hovers beside it, beak towards it,
    until a click (or Esc, or a long rest) sends it home.

    Steering with limited speed and acceleration gives the lag, overshoot and
    banking of a real bird; wing beats follow what it is doing."""

    MAX_SPEED, MAX_ACCEL, SLOW_RADIUS = 1100.0, 3200.0, 150.0
    HOME_SPEED = 1400.0
    IDLE_RETURN, LEFT_RETURN, MAX_TIME = 12.0, 4.0, 90.0
    # Clicks only send it home once the pointer has led it somewhere (or after a
    # while): the clicks that started the trip, and any that follow straight on,
    # must not end it.
    GRACE, ENGAGE_DISTANCE, ENGAGE_TIME = 1.0, 90.0, 3.5
    LAND_TIME = 0.45
    SCALE = 0.85

    def __init__(self, home, bird_w, bird_h, pointer):
        self.home = home
        self.half_w, self.half_h = bird_w * self.SCALE / 2, bird_h * self.SCALE / 2
        self.x, self.y = home
        self.vx, self.vy = 0.0, -260.0            # a little jump out of the icon
        self.pointer = pointer
        self.side = 1 if pointer[0] <= home[0] else -1   # which side of the pointer to hover on
        self.flip = 1.0
        self.angle = 0.0
        self.phase = 0.0
        self.wings = 1.0
        self.t = 0.0
        self.last_move = 0.0
        self.left_at = None
        self.returning = False
        self.landing_at = None
        self.trail = []                          # recent (t, x, y)
        self.done = False
        self.origin = pointer
        self.travel = 0.0                         # how far the pointer has led it

    # ---------------------------------------------------------------- input
    def moved(self, x, y):
        self.travel = max(self.travel, math.hypot(x - self.origin[0], y - self.origin[1]))
        self.pointer = (x, y)
        self.last_move = self.t
        self.left_at = None

    def left(self):
        self.left_at = self.t

    def go_home(self):
        self.returning = True

    @property
    def engaged(self):
        return self.t > self.GRACE and (self.travel > self.ENGAGE_DISTANCE or self.t > self.ENGAGE_TIME)

    # ---------------------------------------------------------------- physics
    def step(self, dt):
        self.t += dt
        if not self.returning and (self.t - self.last_move > self.IDLE_RETURN or self.t > self.MAX_TIME
                                   or (self.left_at is not None and self.t - self.left_at > self.LEFT_RETURN)):
            self.returning = True
        if self.landing_at is not None:
            if self.t - self.landing_at >= self.LAND_TIME:
                self.done = True
            return
        if self.returning:
            tx, ty, top = self.home[0], self.home[1] - 10, self.HOME_SPEED
        else:
            px, py = self.pointer
            # Hover beside the pointer with the beak (bottom corner of the art) just short of it.
            if self.x > px + 24:
                self.side = 1
            elif self.x < px - 24:
                self.side = -1
            tx = px + self.side * (self.half_w + 8)
            ty = py - self.half_h * 0.94
            top = self.MAX_SPEED
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        speed = top * min(1.0, dist / self.SLOW_RADIUS)
        want_x, want_y = (dx / dist * speed, dy / dist * speed) if dist > 0.01 else (0.0, 0.0)
        ax, ay = want_x - self.vx, want_y - self.vy
        a = math.hypot(ax, ay)
        limit = self.MAX_ACCEL * dt
        if a > limit:
            ax, ay = ax / a * limit, ay / a * limit
        self.vx += ax
        self.vy += ay
        self.x += self.vx * dt
        self.y += self.vy * dt
        v = math.hypot(self.vx, self.vy)
        if self.returning and dist < 4 and v < 60:
            self.landing_at = self.t
            self.land_from = (self.x, self.y)
            return
        # Facing: the way it flies when fast, towards the pointer when hovering.
        if abs(self.vx) > 140:
            face = -1.0 if self.vx > 0 else 1.0
        elif self.returning:
            face = 1.0
        else:
            face = float(self.side)
        self.flip += (face - self.flip) * min(1.0, dt * 9)
        # Pitch with the flight path; level when hovering.
        pitch = _facing(math.atan2(self.vy, self.vx), 1.0 if self.flip > 0 else -1.0, 0.35) if v > 60 else 0.0
        pitch = max(-0.55, min(0.55, pitch))
        self.angle += (pitch - self.angle) * min(1.0, dt * 8)
        # Wings: fast beats to hover, steady beats to fly, tucked in a fast swoop down.
        hz = 9.0 if v < 160 else (7.5 if self.vy < -120 else 6.0)
        self.phase += 2 * math.pi * hz * dt
        beat = 0.22 + 0.78 * math.cos(self.phase)
        swoop = max(0.0, min(1.0, (self.vy - 380) / 300)) if v > 500 else 0.0
        self.wings = beat * (1 - swoop) + 0.35 * swoop
        self.trail = [p for p in self.trail if self.t - p[0] < 0.1] + [(self.t, self.x, self.y)]

    def pose(self):
        if self.landing_at is not None:
            k = min(1.0, (self.t - self.landing_at) / self.LAND_TIME)
            lx, ly = self.land_from
            bounce = math.sin(k * math.pi) * 5 * (1 - k)
            hx, hy = self.home
            return Pose(lx + (hx - lx) * ease_out(k), ly + (hy - ly) * ease_out(k) - bounce,
                        self.SCALE + (1 - self.SCALE) * ease_out(k), 0.0,
                        self.flip + (1.0 - self.flip) * _smooth(k * 1.6), 1.0)
        hover = math.hypot(self.vx, self.vy) < 120 and not self.returning
        bob = math.sin(self.t * 2 * math.pi * 1.6) * 2.5 if hover else 0.0
        return Pose(self.x, self.y + bob, self.SCALE, self.angle, self.flip, self.wings)

    def trail_start(self):
        if len(self.trail) < 2 or math.hypot(self.vx, self.vy) < 600:
            return None
        return self.trail[0][1], self.trail[0][2]


class StartIcon(Gtk.DrawingArea):
    """The app icon on the start page, drawn from its parts. Three quick clicks
    send the bird out; one trip in FOLLOW_ODDS follows the pointer instead."""

    FOLLOW_ODDS = 8

    STREAK_RESET = 20.0      # seconds between trips that still count as "in a row"

    def __init__(self, svg_path, size, flight_layer_for, lake_for, avoid_for=None):
        super().__init__(content_width=size, content_height=size, halign=Gtk.Align.CENTER)
        self.parts = IconParts(svg_path)
        self.size = size
        self._layer_for = flight_layer_for          # callables: the page's flight layer and lake
        self._lake_for = lake_for
        self._avoid_for = avoid_for                  # callable: widgets the flights should not cross
        self._streak = 0                             # trips in a row without following the pointer
        self._last_trip_end = -1e9
        self._clicks = []
        self._hop = None                             # (start, height)
        self._trip = None                            # (start, FlightPlan) or (start, FollowFlight)
        self._bird_w = 0.0
        self._timer = 0
        self._tick_id = 0
        self._last_frame = 0
        self._glint_until = 0.0
        self._rng = random.Random()
        self._watching = None
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.add_controller(click)
        self.connect("unmap", lambda *_: self._finish())

    # -------------------------------------------------------------- input
    def _on_click(self, *_):
        now = GLib.get_monotonic_time() / 1e6
        if self._trip:
            return
        note_activity()
        self._clicks = [c for c in self._clicks if now - c < CLICK_GAP] + [now]
        if len(self._clicks) >= CLICKS and animations_enabled():
            self._clicks = []
            self._launch(now)
        else:
            self._hop = (now, 4 + 4 * (len(self._clicks) - 1))
            self._run()

    def _home(self, layer):
        ok, rect = self.compute_bounds(layer)
        if not ok:
            return None, 0.0, 0.0
        s = rect.get_width() / CANVAS
        bx, by, bw, bh = self.parts.bird_rect
        return (rect.get_x() + (bx + bw / 2) * s, rect.get_y() + (by + bh / 2) * s), s * bw, s * bh

    def _launch(self, now):
        layer = self._layer_for()
        home, bird_w, bird_h = self._home(layer)
        if home is None:
            return
        self._bird_w = bird_w
        if now - self._last_trip_end > self.STREAK_RESET:
            self._streak = 0
        # The odds improve with every trip in a row, and the fifth is certain.
        odds = max(1, self.FOLLOW_ODDS - 2 * self._streak)
        follow = self._streak >= 4 or self._rng.randrange(odds) == 0
        self._streak = 0 if follow else self._streak + 1
        if follow:
            self._watch(layer.get_parent())
            self._trip = (now, FollowFlight(home, bird_w, bird_h, self._pointer or home))
            layer.flight = self
            self._last_frame = 0
            self._tick_id = layer.add_tick_callback(self._on_frame)
            return
        self._trip = (now, FlightPlan(home, layer.get_width(), layer.get_height(), self._rng, self._avoid(layer)))
        self._splashed = set()
        layer.flight = self
        self._run()

    _pointer = None

    def _avoid(self, layer):
        """The box around the widgets to fly around, in layer coordinates."""
        boxes = []
        for widget in (self._avoid_for() if self._avoid_for else ()):
            if widget is None or not widget.get_mapped():
                continue
            ok, r = widget.compute_bounds(layer)
            if ok and r.get_width() > 0:
                boxes.append((r.get_x(), r.get_y(), r.get_x() + r.get_width(), r.get_y() + r.get_height()))
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _watch(self, page):
        """Pointer, clicks and Esc on the page, listened to only while following."""
        if self._watching is page or page is None:
            return
        self._watching = page
        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        page.add_controller(motion)
        click = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_page_click)
        page.add_controller(click)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        root = self.get_root()
        if root is not None:
            root.add_controller(keys)

    def _following(self):
        return self._trip is not None and isinstance(self._trip[1], FollowFlight) and not self._trip[1].returning

    def _on_motion(self, _ctrl, x, y):
        self._pointer = (x, y)
        if self._following():
            self._trip[1].moved(x, y)

    def _on_leave(self, *_):
        if self._following():
            self._trip[1].left()

    def _on_page_click(self, gesture, *_):
        if self._following():
            # While it is out, clicks are for the bird, not for what is underneath.
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if self._trip[1].engaged:
                self._trip[1].go_home()

    def _on_key(self, _ctrl, keyval, *_):
        if self._following() and keyval == Gdk.KEY_Escape:
            self._trip[1].go_home()
            return True
        return False

    # -------------------------------------------------------------- clock
    def _on_frame(self, _layer, clock):
        """Frame-synced steps while following the pointer."""
        now = clock.get_frame_time()
        dt = 1 / 60 if not self._last_frame else min(0.05, (now - self._last_frame) / 1e6)
        self._last_frame = now
        follow = self._trip[1] if self._trip else None
        if follow is None:
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        follow.step(dt)
        self._layer_for().queue_draw()
        if follow.done:
            self._trip = None
            self._last_trip_end = GLib.get_monotonic_time() / 1e6
            self._tick_id = 0
            self._glint_until = GLib.get_monotonic_time() / 1e6 + 0.6
            self._layer_for().flight = None
            self._run()                          # the glint
            return GLib.SOURCE_REMOVE
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _run(self):
        if not self._timer:
            self._timer = GLib.timeout_add(16, self._tick)

    def _tick(self):
        now = GLib.get_monotonic_time() / 1e6
        busy = False
        if self._hop and now - self._hop[0] < 0.42:
            busy = True
        else:
            self._hop = None
        if self._trip and isinstance(self._trip[1], FlightPlan):
            start, plan = self._trip
            t = now - start
            for i, (at, point, small) in enumerate(plan.splashes):
                if t >= at and i not in self._splashed:
                    self._splashed.add(i)
                    lake = self._lake_for()
                    if lake is not None:
                        lake.splash_at(*point, small=small)
            if t >= plan.total:
                self._trip = None
                self._last_trip_end = now
                self._glint_until = now + 0.6
                self._layer_for().flight = None
            busy = True
        if now < self._glint_until:
            busy = True
        self.queue_draw()
        self._layer_for().queue_draw()
        if not busy:
            self._timer = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _finish(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        layer = self._layer_for()
        if self._tick_id and layer is not None:
            layer.remove_tick_callback(self._tick_id)
        self._tick_id = 0
        self._trip = self._hop = None
        if layer is not None:
            layer.flight = None
            layer.queue_draw()

    # -------------------------------------------------------------- drawing
    def bird_now(self):
        """(pose, trail start or None, letter or None) of the bird while it is out."""
        if not self._trip:
            return None
        start, trip = self._trip
        if isinstance(trip, FollowFlight):
            return trip.pose(), trip.trail_start(), None
        t = GLib.get_monotonic_time() / 1e6 - start
        if t >= trip.total:
            return None
        pose = trip.at(t)
        before = trip.at(max(0.0, t - 0.09))
        fast = before.visible and math.hypot(pose.x - before.x, pose.y - before.y) > 6
        return pose, ((before.x, before.y) if fast else None), trip.letter

    def _draw(self, _area, cr, width, height):
        scale = self.get_scale_factor()
        px = int(self.size * scale)
        cr.save()
        cr.scale(width / px, height / px)
        Gdk.cairo_set_source_pixbuf(cr, self.parts.tile(px), 0, 0)
        cr.paint()
        cr.restore()
        now = GLib.get_monotonic_time() / 1e6
        s = width / CANVAS
        cr.save()
        cr.scale(s, s)
        out = self.bird_now() is not None
        march = (now * 0.9) % 1.0 if out else 0.0
        glint = max(0.0, (self._glint_until - now) / 0.6)
        self.parts.draw_selection(cr, march, glint)
        cr.restore()
        # While it is out, the page's flight layer draws the bird, above everything.
        if out:
            return
        lift = 0.0
        if self._hop:
            k = (now - self._hop[0]) / 0.42
            lift = math.sin(min(1.0, k) * math.pi) * self._hop[1]
        bx, by, bw, bh = self.parts.bird_rect
        surface = self.parts.bird_surface(bw * s * scale)
        k = bw * s / surface.get_width()
        cr.save()
        cr.translate(bx * s, by * s - lift)
        cr.scale(k, k)
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.restore()


class FlightLayer(Gtk.DrawingArea):
    """Draws the bird over the whole start page while it is out of the icon."""

    def __init__(self):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.flight = None
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        icon = self.flight
        now = icon.bird_now() if icon else None
        if not now:
            return
        pose, trail_from, letter = now
        if not pose.visible:
            return
        w = icon._bird_w * pose.scale
        surface = icon.parts.bird_surface(w * self.get_scale_factor())
        if trail_from:
            tx, ty = trail_from
            trail = cairo.LinearGradient(tx, ty, pose.x, pose.y)
            trail.add_color_stop_rgba(0, 0.10, 0.55, 0.95, 0)
            trail.add_color_stop_rgba(1, 0.10, 0.55, 0.95, 0.30)
            cr.set_source(trail)
            cr.set_line_width(max(2.0, w * 0.10))
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(tx, ty)
            cr.line_to(pose.x, pose.y)
            cr.stroke()
        cr.save()
        cr.translate(pose.x, pose.y)
        cr.rotate(pose.angle)
        cr.scale(pose.flip if abs(pose.flip) > 0.04 else 0.04, 1)
        k = w / surface.get_width()
        cr.scale(k, k)
        cr.translate(-surface.get_width() / 2, -surface.get_height() / 2)
        draw_bird(cr, surface, pose.wings)
        if pose.carrying and letter:
            # A golden letter held at the beak tip (the bottom-left of the artwork).
            cr.translate(0, surface.get_height() * 0.97)
            cr.scale(1 / k, 1 / k)
            if pose.flip < 0:
                cr.scale(-1, 1)          # keep the letter readable when the bird faces right
            cr.select_font_face("Ubuntu", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(max(10.0, w * 0.32))
            extents = cr.text_extents(letter)
            cr.move_to(-extents.width * (0.2 if pose.flip > 0 else 0.8), w * 0.10)
            cr.text_path(letter)
            cr.set_source_rgba(0.05, 0.12, 0.25, 0.35)
            cr.set_line_width(2.0)
            cr.stroke_preserve()
            cr.set_source_rgb(*GOLD)
            cr.fill()
        cr.restore()
