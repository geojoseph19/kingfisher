# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Full-screen region picker modelled on the GNOME screenshot UI.

The screen is captured first, then shown in borderless full-screen windows
(one per monitor). Like GNOME's own tool, the last selection is restored with
handles, a floating panel at the bottom offers Selection / Screen modes and a
round capture button. Because the backdrop is a still image, menus and
tooltips that vanish on focus change are still selectable.
"""

import math

import cairo
from gi.repository import Gdk, GLib, Gtk

from .effects import (CopyGlyph, ExtractGlyph, LoupeGlyph, ScreenGlyph, SelectGlyph, WindowGlyph,
                      animations_enabled, draw_scan_line, spinner)
from .geometry import Monitor, ScreenLayout

DIM = 0.45
HANDLE_HIT = 16
EDGE_HIT = 9
MIN_SIZE = 6
BRACKET = 22          # corner bracket arm length
BRACKET_W = 5         # corner bracket thickness
LOUPE_SIZE = 132      # rounded-square magnifier, docked in a corner
LOUPE_ZOOM = 8
WHITE = (1, 1, 1)

HANDLE_CURSORS = {
    "nw": "nw-resize", "n": "n-resize", "ne": "ne-resize", "e": "e-resize",
    "se": "se-resize", "s": "s-resize", "sw": "sw-resize", "w": "w-resize",
    "move": "move",
}


def pil_to_surface(image):
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    data = bytearray(rgb.tobytes("raw", "BGRX"))
    surface = cairo.ImageSurface.create_for_data(
        data, cairo.FORMAT_RGB24, image.width, image.height, image.width * 4)
    return surface, data


class RegionSelector:
    """Coordinates the per-monitor overlay windows."""

    def __init__(self, app, image, settings, on_done):
        self.app = app
        self.image = image
        self.settings = settings
        self.on_done = on_done
        self.surface, self._data = pil_to_surface(image)
        self.windows = []
        self.finished = False
        self.mode = "selection"  # selection | screen

        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        count = monitors.get_n_items()
        geoms = []
        for i in range(count):
            g = monitors.get_item(i).get_geometry()
            geoms.append(Monitor(g.x, g.y, g.width, g.height))
        self.layout = ScreenLayout(geoms, image.size)

        # Restore the previous selection, like GNOME does; otherwise start on
        # the monitor under the pointer.
        home, restored = self._pointer_monitor(display, monitors), None
        last = settings["last_selection"]
        if len(last) == 4 and not settings["confirm_on_release"]:
            found = self.layout.from_layout(tuple(last))
            if found:
                home, restored = found

        for i, mon in enumerate(self.layout.monitors):
            monitor = monitors.get_item(i) if count else None
            win = OverlayWindow(self, monitor, i, mon, show_panel=(i == home))
            if i == home and not settings["confirm_on_release"]:
                win.sel = restored or win.default_selection()
            self.windows.append(win)
            win.present()

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _pointer_monitor(display, monitors):
        """Index of the monitor under the pointer (0 when unknown)."""
        seat = display.get_default_seat()
        pointer = seat.get_pointer() if seat else None
        if pointer is None or monitors.get_n_items() < 2:
            return 0
        surface, _x, _y = pointer.get_surface_at_position()
        if surface is not None:
            monitor = display.get_monitor_at_surface(surface)
            for i in range(monitors.get_n_items()):
                if monitors.get_item(i) == monitor:
                    return i
        return 0

    def selection_started(self, window):
        for other in self.windows:
            if other is not window:
                other.clear_selection()
        self.move_panel_to(window)

    def move_panel_to(self, window):
        """Show the toolbar on the monitor the user is working on."""
        for win in self.windows:
            win.show_panel(win is window)

    def set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        for win in self.windows:
            win.mode_changed()

    # ------------------------------------------------------------ lifecycle
    def confirm(self, window):
        if self.finished:
            return
        if self.mode != "screen" and not window.sel:
            # The toolbar may be on another monitor than the selection.
            window = next((w for w in self.windows if w.sel), window)
        if self.mode == "screen":
            rect = (0, 0, window.geom.width, window.geom.height)
        elif window.sel:
            rect = window.sel
            self.settings["last_selection"] = list(self.layout.to_layout(window.index, rect))
        else:
            # Capture pressed with nothing selected: take this whole screen.
            rect = (0, 0, window.geom.width, window.geom.height)
        box = self.layout.to_image_box(window.index, rect)
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            return
        self.finished = True
        for win in self.windows:
            win.set_busy(win is window, rect)
        self.on_done(self, box)

    def cancel(self):
        if self.finished:
            return
        self.finished = True
        self.close()
        self.on_done(self, None)

    def close(self):
        self.finished = True
        for win in self.windows:
            win.stop_scan()
            win.destroy()
        self.windows = []
        # The frozen screenshot can be hundreds of MB on large displays.
        self.image = self.surface = self._data = None

    def alive(self):
        """False once the overlay windows are gone (e.g. closed by the WM)."""
        return bool(self.windows) and all(w.get_application() is not None for w in self.windows)


class OverlayWindow(Gtk.Window):
    def __init__(self, selector, monitor, index, geom, show_panel):
        super().__init__(application=selector.app, decorated=False, title="Kingfisher — Select Area")
        self.selector = selector
        self.settings = selector.settings
        self.index = index
        self.geom = geom
        self.sel = None          # (x, y, w, h) in logical window coords
        self.state = "idle"      # idle | drawing | moving | resizing | busy
        self.handle = None
        self.pointer = None
        self.drag_origin = None
        self.sel_origin = None
        self.prev_sel = None
        self._syncing = False
        self._scan_start = None
        self._scan_tick = 0

        self.add_css_class("kingfisher-overlay")
        self.set_default_size(geom.width, geom.height)

        overlay = Gtk.Overlay()
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, focusable=True)
        self.area.set_draw_func(self._draw)
        self.area.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        overlay.set_child(self.area)

        self.panel = self._build_panel()
        self.panel.set_visible(show_panel)
        overlay.add_overlay(self.panel)

        self.busy = Gtk.Box(spacing=10, halign=Gtk.Align.START, valign=Gtk.Align.START, visible=False)
        self.busy.add_css_class("kingfisher-busy")
        self.busy.append(spinner())
        self.busy.append(Gtk.Label(label="Reading text…"))
        overlay.add_overlay(self.busy)
        self.set_child(overlay)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.area.add_controller(drag)

        clicks = Gtk.GestureClick(button=0)
        clicks.connect("pressed", self._on_click)
        self.area.add_controller(clicks)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.area.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        if monitor is not None:
            self.fullscreen_on_monitor(monitor)
        else:
            self.fullscreen()
        GLib.idle_add(lambda: self.area.grab_focus() and False)
        # Ask once more to be raised after mapping, in case the first request
        # raced with the window manager.
        self.connect("map", lambda *_: GLib.timeout_add(150, self._present_again))
        # Ctrl+W, Alt+F4 or the window manager closing an overlay cancels the
        # selection properly instead of leaving the capture half-finished.
        self.connect("close-request", self._on_close_request)
        GLib.timeout_add(60, self._reveal_panel)

    def _present_again(self):
        if not self.selector.finished and self.get_application() is not None:
            self.present()
        return GLib.SOURCE_REMOVE

    def _on_close_request(self, *_):
        if self.state == "busy":
            self.selector.app.cancel_ocr()     # the job's callback closes the overlay
        else:
            self.selector.cancel()
        return True

    # ================================================================= panel
    def _build_panel(self):
        revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                transition_duration=200, halign=Gtk.Align.CENTER,
                                valign=Gtk.Align.END, margin_bottom=8)
        self.revealer = revealer
        # Margins leave room for the drop shadow inside the revealer's clip.
        bar = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, margin_top=28, margin_bottom=28,
                      margin_start=36, margin_end=36)
        bar.add_css_class("shot-bar")

        modes = Gtk.Box(spacing=0)
        modes.add_css_class("shot-segment")
        self.mode_buttons = {}
        group = None
        for mode, glyph, label, key in (("selection", SelectGlyph, "Area", "S"),
                                        ("screen", ScreenGlyph, "Screen", "C")):
            button = Gtk.ToggleButton(group=group, tooltip_text=f"{label} ({key})")
            group = group or button
            content = Gtk.Box(spacing=6)
            content.append(glyph(button))
            content.append(Gtk.Label(label=label))
            button.set_child(content)
            button.connect("toggled", self._on_mode_toggled, mode)
            modes.append(button)
            self.mode_buttons[mode] = button
        self.mode_buttons[self.selector.mode].set_active(True)
        bar.append(modes)
        bar.append(self._separator())

        open_btn = Gtk.ToggleButton(tooltip_text="Open the text in Kingfisher")
        open_btn.set_child(WindowGlyph(open_btn))
        open_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Open the text in Kingfisher"])
        copy_btn = Gtk.ToggleButton(group=open_btn, tooltip_text="Copy the text only")
        copy_btn.set_child(CopyGlyph(copy_btn))
        copy_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Copy the text only"])
        (copy_btn if self.settings["quick_mode"] == "copy" else open_btn).set_active(True)
        open_btn.connect("toggled", lambda b: b.get_active() and self.settings.__setitem__("quick_mode", "window"))
        copy_btn.connect("toggled", lambda b: b.get_active() and self.settings.__setitem__("quick_mode", "copy"))
        loupe = Gtk.ToggleButton(active=self.settings["show_magnifier"], tooltip_text="Magnifier while selecting")
        loupe.set_child(LoupeGlyph(loupe))
        loupe.update_property([Gtk.AccessibleProperty.LABEL], ["Magnifier while selecting"])
        loupe.connect("toggled", lambda b: self.settings.__setitem__("show_magnifier", b.get_active()))
        for b in (open_btn, copy_btn, loupe):
            b.add_css_class("shot-icon")
            bar.append(b)
        bar.append(self._separator())

        capture = Gtk.Button(tooltip_text="Extract text (Enter)")
        content = Gtk.Box(spacing=8)
        content.append(ExtractGlyph(capture))
        content.append(Gtk.Label(label="Extract Text"))
        capture.set_child(content)
        capture.update_property([Gtk.AccessibleProperty.LABEL], ["Extract Text"])
        capture.add_css_class("shot-primary")
        capture.connect("clicked", lambda *_: self.selector.confirm(self))
        bar.append(capture)

        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Cancel (Esc)")
        close.add_css_class("shot-icon")
        close.connect("clicked", lambda *_: self.selector.cancel())
        bar.append(close)
        revealer.set_child(bar)
        return revealer

    @staticmethod
    def _separator():
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.add_css_class("shot-sep")
        return sep

    def _reveal_panel(self):
        self.revealer.set_reveal_child(True)
        return GLib.SOURCE_REMOVE

    def show_panel(self, visible):
        if self.state == "busy" or self.panel.get_visible() == visible:
            return
        self.panel.set_visible(visible)
        self.revealer.set_reveal_child(visible)

    def _on_mode_toggled(self, button, mode):
        if button.get_active() and not self._syncing:
            self.selector.set_mode(mode)

    def mode_changed(self):
        self._syncing = True
        self.mode_buttons[self.selector.mode].set_active(True)
        self._syncing = False
        self.area.queue_draw()

    # ================================================================= state
    def default_selection(self):
        w, h = round(self.geom.width * 0.4), round(self.geom.height * 0.3)
        return ((self.geom.width - w) / 2, (self.geom.height - h) / 2 - 40, w, h)

    def clear_selection(self):
        self.sel = None
        self.area.queue_draw()

    def set_busy(self, active, rect):
        self.state = "busy"
        self.revealer.set_reveal_child(False)
        self.panel.set_visible(False)
        self.area.set_cursor(Gdk.Cursor.new_from_name("wait" if active else "default"))
        if active:
            x, y, w, h = rect
            self.busy.set_visible(True)   # measure only once visible
            _, nat = self.busy.get_preferred_size()
            self.busy.set_margin_start(int(max(8, x + w / 2 - nat.width / 2)))
            self.busy.set_margin_top(int(max(8, y + h / 2 - nat.height / 2)))
            if not self._scan_tick and animations_enabled():
                self._scan_start = GLib.get_monotonic_time()
                self._scan_tick = GLib.timeout_add(33, self._tick_scan)
        self.area.queue_draw()

    def _inside(self, x, y):
        if not self.sel:
            return False
        sx, sy, sw, sh = self.sel
        return sx < x < sx + sw and sy < y < sy + sh

    def _handle_at(self, px, py):
        if not self.sel or self.selector.mode != "selection":
            return None
        x, y, w, h = self.sel
        corners = {"nw": (x, y), "ne": (x + w, y), "se": (x + w, y + h), "sw": (x, y + h)}
        for name, (hx, hy) in corners.items():
            if abs(px - hx) <= HANDLE_HIT and abs(py - hy) <= HANDLE_HIT:
                return name
        within_x = x - EDGE_HIT <= px <= x + w + EDGE_HIT
        within_y = y - EDGE_HIT <= py <= y + h + EDGE_HIT
        if within_x and abs(py - y) <= EDGE_HIT:
            return "n"
        if within_x and abs(py - (y + h)) <= EDGE_HIT:
            return "s"
        if within_y and abs(px - x) <= EDGE_HIT:
            return "w"
        if within_y and abs(px - (x + w)) <= EDGE_HIT:
            return "e"
        if self._inside(px, py):
            return "move"
        return None

    # ================================================================ events
    def _on_click(self, gesture, n_press, x, y):
        if self.state == "busy":
            return
        button = gesture.get_current_button()
        if button == Gdk.BUTTON_SECONDARY:
            self.selector.cancel()
        elif (button == Gdk.BUTTON_PRIMARY and n_press == 2
              and (self.selector.mode == "screen" or self._inside(x, y))):
            self.selector.confirm(self)

    def _on_drag_begin(self, gesture, x, y):
        if self.state == "busy":
            return
        if self.selector.mode == "screen":
            self.selector.set_mode("selection")
        self.drag_origin = (x, y)
        self.handle = self._handle_at(x, y)
        if self.handle == "move":
            self.state = "moving"
            self.sel_origin = self.sel
        elif self.handle:
            self.state = "resizing"
            self.sel_origin = self.sel
        else:
            self.state = "drawing"
            self.prev_sel = self.sel
            self.sel = None
            self.selector.selection_started(self)

    def _on_drag_update(self, gesture, dx, dy):
        if self.state in ("idle", "busy") or self.drag_origin is None:
            return
        ox, oy = self.drag_origin
        W, H = self.geom.width, self.geom.height
        px, py = min(max(ox + dx, 0), W), min(max(oy + dy, 0), H)
        self.pointer = (px, py)
        if self.state == "drawing":
            if abs(dx) < 2 and abs(dy) < 2:
                return
            x0, y0 = min(max(ox, 0), W), min(max(oy, 0), H)
            self.sel = (min(x0, px), min(y0, py), abs(px - x0), abs(py - y0))
        elif self.state == "moving":
            x, y, w, h = self.sel_origin
            self.sel = (min(max(x + dx, 0), W - w), min(max(y + dy, 0), H - h), w, h)
        elif self.state == "resizing":
            x, y, w, h = self.sel_origin
            left, top, right, bottom = x, y, x + w, y + h
            if "w" in self.handle:
                left = min(max(x + dx, 0), W)
            if "e" in self.handle:
                right = min(max(x + w + dx, 0), W)
            if "n" in self.handle:
                top = min(max(y + dy, 0), H)
            if "s" in self.handle:
                bottom = min(max(y + h + dy, 0), H)
            self.sel = (min(left, right), min(top, bottom), abs(right - left), abs(bottom - top))
        self.area.queue_draw()

    def _on_drag_end(self, gesture, dx, dy):
        if self.state in ("idle", "busy"):
            return
        was_drawing = self.state == "drawing"
        self.state = "idle"
        self.drag_origin = None
        too_small = not self.sel or self.sel[2] < MIN_SIZE or self.sel[3] < MIN_SIZE
        if too_small:
            # A plain click keeps the previous selection, like GNOME.
            self.sel = self.prev_sel if was_drawing else self.sel_origin
        elif was_drawing and self.settings["confirm_on_release"]:
            self.selector.confirm(self)
            return
        self.area.queue_draw()

    def _on_motion(self, _ctrl, x, y):
        self.pointer = (x, y)
        if not self.panel.get_visible() and not any(
                w.state in ("drawing", "moving", "resizing") for w in self.selector.windows):
            # The toolbar follows the pointer to whichever monitor is in use.
            self.selector.move_panel_to(self)
        if self.state == "idle":
            handle = self._handle_at(x, y)
            self.area.set_cursor(Gdk.Cursor.new_from_name(HANDLE_CURSORS.get(handle, "crosshair")))
        if self._loupe_visible():
            self.area.queue_draw()

    def _on_leave(self, *_):
        had_loupe = self._loupe_visible()
        self.pointer = None
        if had_loupe:
            self.area.queue_draw()

    def _loupe_visible(self):
        return (self.pointer is not None and self.settings["show_magnifier"]
                and self.state in ("drawing", "resizing"))

    def _on_key(self, _ctrl, keyval, _code, state):
        if self.state == "busy":
            if keyval == Gdk.KEY_Escape:
                self.selector.app.cancel_ocr()
            return True
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if keyval == Gdk.KEY_Escape:
            self.selector.cancel()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter, Gdk.KEY_space):
            self.selector.confirm(self)
        elif keyval in (Gdk.KEY_s, Gdk.KEY_S) and not ctrl:
            self.selector.set_mode("selection")
        elif keyval in (Gdk.KEY_c, Gdk.KEY_C) and not ctrl:
            self.selector.set_mode("screen")
        elif keyval in (Gdk.KEY_a, Gdk.KEY_A) and ctrl:
            self.selector.set_mode("selection")
            self.selector.selection_started(self)
            self.sel = (0, 0, self.geom.width, self.geom.height)
            self.area.queue_draw()
        elif self.sel and keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            step = 10 if state & Gdk.ModifierType.SHIFT_MASK else 1
            dx = {Gdk.KEY_Left: -step, Gdk.KEY_Right: step}.get(keyval, 0)
            dy = {Gdk.KEY_Up: -step, Gdk.KEY_Down: step}.get(keyval, 0)
            x, y, w, h = self.sel
            if state & Gdk.ModifierType.ALT_MASK:
                self.sel = (x, y, min(max(MIN_SIZE, w + dx), self.geom.width - x),
                            min(max(MIN_SIZE, h + dy), self.geom.height - y))
            else:
                self.sel = (min(max(x + dx, 0), self.geom.width - w),
                            min(max(y + dy, 0), self.geom.height - h), w, h)
            self.area.queue_draw()
        else:
            return False
        return True

    # =============================================================== drawing
    def _paint_backdrop(self, cr):
        layout = self.selector.layout
        ox, oy = layout.offset(self.index)
        cr.save()
        cr.scale(1 / layout.scale_x, 1 / layout.scale_y)
        cr.set_source_surface(self.selector.surface, -ox, -oy)
        unscaled = abs(layout.scale_x - 1) < 1e-6 and abs(layout.scale_y - 1) < 1e-6
        cr.get_source().set_filter(cairo.FILTER_NEAREST if unscaled else cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()

    def _draw(self, _area, cr, width, height):
        self._paint_backdrop(cr)

        if self.selector.mode == "screen" and self.state != "busy":
            cr.set_source_rgba(*WHITE, 0.9)
            cr.set_line_width(4)
            cr.rectangle(2, 2, width - 4, height - 4)
            cr.stroke()
            return

        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, width, height)
        if self.sel:
            cr.rectangle(*self.sel)
        cr.set_source_rgba(0, 0, 0, DIM)
        cr.fill()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)

        if self.sel:
            self._draw_selection(cr, width)

        if self._loupe_visible():
            self._draw_loupe(cr, width, height)

    def _draw_selection(self, cr, width):
        x, y, w, h = self.sel
        # Hairline frame with a soft dark outer line so it reads on any backdrop.
        cr.set_line_width(1)
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.rectangle(x - 1.5, y - 1.5, w + 3, h + 3)
        cr.stroke()
        cr.set_source_rgba(*WHITE, 0.95)
        cr.rectangle(x - 0.5, y - 0.5, w + 1, h + 1)
        cr.stroke()

        if self.state == "busy":
            self._draw_scan(cr, x, y, w, h)
            return

        if self.state in ("moving", "resizing"):
            # Rule-of-thirds guides while adjusting.
            cr.set_source_rgba(*WHITE, 0.28)
            cr.set_line_width(1)
            for i in (1, 2):
                gx = math.floor(x + w * i / 3) + 0.5
                gy = math.floor(y + h * i / 3) + 0.5
                cr.move_to(gx, y)
                cr.line_to(gx, y + h)
                cr.move_to(x, gy)
                cr.line_to(x + w, gy)
            cr.stroke()

        self._draw_handles(cr, x, y, w, h)

        if self.state in ("drawing", "moving", "resizing"):
            layout = self.selector.layout
            label = f"{round(w * layout.scale_x)} × {round(h * layout.scale_y)}"
            cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(12)
            ext = cr.text_extents(label)
            bw, bh = ext.width + 20, 26
            bx = min(max(x, 4), width - bw - 4)
            by = y - bh - 10 if y - bh - 10 > 4 else y + 10
            self._rounded_rect(cr, bx, by, bw, bh, 8)
            cr.set_source_rgba(0.08, 0.08, 0.09, 0.85)
            cr.fill()
            cr.set_source_rgb(*WHITE)
            cr.move_to(bx + 10 - ext.x_bearing, by + bh / 2 - ext.height / 2 - ext.y_bearing)
            cr.show_text(label)

    def _draw_scan(self, cr, x, y, w, h):
        """Golden scan line sweeping over the selection while text is read."""
        if self._scan_start is not None:
            draw_scan_line(cr, x, y, w, h, (GLib.get_monotonic_time() - self._scan_start) / 1e6)

    def stop_scan(self):
        if self._scan_tick:
            GLib.source_remove(self._scan_tick)
            self._scan_tick = 0

    def _tick_scan(self):
        if self.state != "busy":
            self._scan_tick = 0
            return GLib.SOURCE_REMOVE
        self.area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw_handles(self, cr, x, y, w, h):
        # Draw just outside the frame so handles stay visible on light content.
        o = BRACKET_W / 2 + 1
        x, y, w, h = x - o, y - o, w + 2 * o, h + 2 * o
        arm = min(BRACKET, w / 2, h / 2)
        corners = (
            ((x, y + arm), (x, y), (x + arm, y)),
            ((x + w - arm, y), (x + w, y), (x + w, y + arm)),
            ((x + w, y + h - arm), (x + w, y + h), (x + w - arm, y + h)),
            ((x + arm, y + h), (x, y + h), (x, y + h - arm)),
        )
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for color, width, dy in (((0, 0, 0, 0.35), BRACKET_W + 3, 1), ((*WHITE, 1), BRACKET_W, 0)):
            cr.set_source_rgba(*color)
            cr.set_line_width(width)
            for a, b, c in corners:
                cr.move_to(a[0], a[1] + dy)
                cr.line_to(b[0], b[1] + dy)
                cr.line_to(c[0], c[1] + dy)
            # Short pills in the middle of each long-enough edge.
            if w > 3 * BRACKET:
                for ey in (y, y + h):
                    cr.move_to(x + w / 2 - 9, ey + dy)
                    cr.line_to(x + w / 2 + 9, ey + dy)
            if h > 3 * BRACKET:
                for ex in (x, x + w):
                    cr.move_to(ex, y + h / 2 - 9 + dy)
                    cr.line_to(ex, y + h / 2 + 9 + dy)
            cr.stroke()
        cr.set_line_cap(cairo.LINE_CAP_BUTT)

    def _draw_loupe(self, cr, width, height):
        px, py = self.pointer
        size, half = LOUPE_SIZE, LOUPE_SIZE / 2
        # Docked in a screen corner rather than chasing the cursor; it only
        # moves to the opposite corner when the pointer comes close to it.
        margin = 28
        spots = {
            "tr": (width - size - margin, margin),
            "tl": (margin, margin),
            "br": (width - size - margin, height - size - 26 - margin),
            "bl": (margin, height - size - 26 - margin),
        }
        corner = getattr(self, "_loupe_corner", "tr")
        lx, ly = spots[corner]
        if lx - 80 < px < lx + size + 80 and ly - 80 < py < ly + size + 106:
            corner = {"tr": "tl", "tl": "tr", "br": "bl", "bl": "br"}[corner]
            lx, ly = spots[corner]
        self._loupe_corner = corner
        cx, cy = lx + half, ly + half

        layout = self.selector.layout
        img_x, img_y = layout.to_image_point(self.index, px, py)
        zoom = LOUPE_ZOOM / layout.scale_x

        self._rounded_rect(cr, lx - 1, ly + 2, size + 2, size + 2, 16)
        cr.set_source_rgba(0, 0, 0, 0.3)
        cr.fill()
        cr.save()
        self._rounded_rect(cr, lx, ly, size, size, 14)
        cr.clip()
        cr.set_source_rgb(0.1, 0.1, 0.1)
        cr.paint()
        cr.translate(cx, cy)
        cr.scale(zoom, LOUPE_ZOOM / layout.scale_y)
        cr.translate(-math.floor(img_x) - 0.5, -math.floor(img_y) - 0.5)
        cr.set_source_surface(self.selector.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_NEAREST)
        cr.paint()
        cr.restore()

        cr.save()
        self._rounded_rect(cr, lx, ly, size, size, 14)
        cr.clip()
        cr.set_source_rgba(0, 0, 0, 0.12)
        cr.set_line_width(1)
        off = (half - zoom / 2) % zoom
        g = off
        while g < size:
            cr.move_to(lx + g, ly)
            cr.line_to(lx + g, ly + size)
            cr.move_to(lx, ly + g)
            cr.line_to(lx + size, ly + g)
            g += zoom
        cr.stroke()
        cr.restore()
        cr.set_source_rgb(*WHITE)
        cr.set_line_width(2)
        cr.rectangle(cx - zoom / 2, cy - zoom / 2, zoom, zoom)
        cr.stroke()
        self._rounded_rect(cr, lx, ly, size, size, 14)
        cr.set_source_rgba(*WHITE, 0.9)
        cr.set_line_width(2)
        cr.stroke()

        label = f"{round(img_x)}, {round(img_y)}"
        cr.select_font_face("Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11)
        ext = cr.text_extents(label)
        bw, bh = ext.width + 16, 20
        bx, by = cx - bw / 2, ly + size + 6
        self._rounded_rect(cr, bx, by, bw, bh, 6)
        cr.set_source_rgba(0.08, 0.08, 0.09, 0.85)
        cr.fill()
        cr.set_source_rgb(*WHITE)
        cr.move_to(bx + 8 - ext.x_bearing, by + bh / 2 - ext.height / 2 - ext.y_bearing)
        cr.show_text(label)

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
