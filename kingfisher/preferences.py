# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preferences window."""

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import IS_SNAP, autostart, capture, codes, display_path, screencast, shortcut
from .history import MAX_ENTRIES
from .install_button import InstallButton
from .ocr import USER_TESSDATA, language_name
from .toasts import fit_toasts

LAYOUTS = [
    ("auto", "Automatic", "Detect columns, blocks and lines"),
    ("block", "Single block", "One uniform block of text"),
    ("line", "Single line", "Exactly one line, e.g. a title or field"),
    ("sparse", "Sparse text", "Scattered labels, such as app UIs"),
]
FORMATS = [("lines", "As on Screen"), ("paragraphs", "Paragraphs")]
QUICK = [("window", "Show the results window"), ("copy", "Copy text and notify")]
MODIFIER_KEYS = {
    Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
    Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Meta_L,
    Gdk.KEY_Meta_R, Gdk.KEY_ISO_Level3_Shift, Gdk.KEY_Caps_Lock, Gdk.KEY_Hyper_L, Gdk.KEY_Hyper_R,
}


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _switch_row(title, subtitle, settings, key):
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    switch = Gtk.Switch(active=settings[key], valign=Gtk.Align.CENTER)
    switch.connect("notify::active", lambda s, _p: settings.__setitem__(key, s.get_active()))
    row.add_suffix(switch)
    row.set_activatable_widget(switch)
    return row


def _combo_row(title, subtitle, options, current, on_change):
    row = Adw.ComboRow(title=title, subtitle=subtitle)
    row.set_model(Gtk.StringList.new([label for _, label, *_ in options]))
    keys = [opt[0] for opt in options]
    row.set_selected(keys.index(current) if current in keys else 0)
    row.connect("notify::selected", lambda r, _p: on_change(keys[r.get_selected()]))
    return row


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, app, parent):
        super().__init__(transient_for=parent, modal=True, search_enabled=False)
        self.app = app
        self.settings = app.settings
        self.set_default_size(620, 720)
        self.settings.connect(self._on_setting)
        self.connect("close-request", lambda *_: self.settings.disconnect(self._on_setting) or False)
        # Adw.PreferencesWindow is not an action map: give it its own "win" group.
        actions = Gio.SimpleActionGroup()
        clear = Gio.SimpleAction.new("clear-history", None)
        clear.connect("activate", lambda *_: self._on_clear_history())
        actions.add_action(clear)
        self.insert_action_group("win", actions)

        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        self.add(page)

        # ---------------------------------------------------------- capture
        group = Adw.PreferencesGroup(title="Capture")
        group.add(_switch_row("Extract on release",
                              "Read text as soon as you let go of the mouse. "
                              "Turn off to fine-tune the box with handles first.",
                              self.settings, "confirm_on_release"))
        group.add(_switch_row("Copy text automatically", "Put recognized text on the clipboard right away",
                              self.settings, "auto_copy"))
        group.add(_switch_row("Show magnifier", "Zoomed view in a screen corner while dragging",
                              self.settings, "show_magnifier"))
        page.add(group)

        # ------------------------------------------------------- background
        group = Adw.PreferencesGroup(title="Top Bar")
        group.add(_switch_row("Show icon in the top bar",
                              "Click it to select an area. Kingfisher keeps running in the background.",
                              self.settings, "show_tray"))
        login_row = Adw.ActionRow(title="Start at login",
                                  subtitle="Put the icon in the top bar when you log in")
        login_switch = Gtk.Switch(active=autostart.is_enabled(), valign=Gtk.Align.CENTER)
        login_switch.connect("notify::active",
                             lambda sw, _p: autostart.enable() if sw.get_active() else autostart.disable())
        login_row.add_suffix(login_switch)
        login_row.set_activatable_widget(login_switch)
        group.add(login_row)
        page.add(group)

        # --------------------------------------------------------- shortcut
        group = Adw.PreferencesGroup(
            title="System-Wide Shortcut",
            description="Start a capture from any application, even when Kingfisher is closed.")
        self.shortcut_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.shortcut_row = Adw.ActionRow(title="Enable keyboard shortcut")
        self.shortcut_row.add_suffix(self.shortcut_switch)
        self.shortcut_row.set_activatable_widget(self.shortcut_switch)
        group.add(self.shortcut_row)

        self.binding_row = Adw.ActionRow(title="Key combination",
                                         subtitle="Any key works, including laptop Fn keys")
        self.accel_label = Gtk.ShortcutLabel(valign=Gtk.Align.CENTER, disabled_text="None")
        self.binding_row.add_suffix(self.accel_label)
        change = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
        change.connect("clicked", lambda *_: ShortcutRecorder(self, self._apply_binding).present())
        self.binding_row.add_suffix(change)
        self.binding_row.set_activatable_widget(change)
        group.add(self.binding_row)
        self._refresh_binding()
        group.add(_combo_row("After selecting", "What the shortcut does with the text", QUICK,
                             self.settings["quick_mode"],
                             lambda v: self.settings.__setitem__("quick_mode", v)))
        if not shortcut.supported():
            # "After selecting" stays usable: it also applies to a shortcut
            # added by hand that runs "kingfisher --capture".
            self.shortcut_row.set_sensitive(False)
            self.binding_row.set_sensitive(False)
            group.set_description(shortcut.unavailable_reason())
        self.shortcut_switch.set_active(shortcut.is_enabled())
        self.shortcut_switch.connect("notify::active", self._on_shortcut_toggled)
        page.add(group)

        # ---------------------------------------------------- screen access
        if IS_SNAP:
            access = self._screen_access_group()
            if access is not None:
                page.add(access)

        # ------------------------------------------------------ recognition
        group = Adw.PreferencesGroup(title="Recognition")
        group.add(_combo_row("Page layout", "How text is arranged in the capture",
                             LAYOUTS, self.settings["layout"],
                             lambda v: self.settings.__setitem__("layout", v)))
        group.add(_combo_row("Default text format", None, FORMATS,
                             self.settings["text_format"],
                             lambda v: self.settings.__setitem__("text_format", v)))
        if app.engine.has_best_models():
            group.add(_combo_row("Recognition model", "High accuracy is about 3× slower",
                                 [("fast", "Fast"), ("best", "High accuracy")],
                                 self.settings["model"],
                                 lambda v: self.settings.__setitem__("model", v)))
        group.add(_switch_row("Enhance small text",
                              "Upscale and sharpen captures before reading. Slower, far more accurate.",
                              self.settings, "enhance"))
        page.add(group)

        # -------------------------------------------------------- detection
        group = Adw.PreferencesGroup(title="Detection")
        # The switch only appears when code reading actually works; until then
        # the row offers a one-click install instead of a dead, greyed control.
        self.codes_row = _switch_row("Read QR codes and barcodes",
                                     "Show what a code contains, with an Open or Copy action",
                                     self.settings, "read_codes")
        group.add(self.codes_row)
        self.codes_install_row = Adw.ActionRow(
            title="Read QR codes and barcodes",
            subtitle="Needs a small free component from Ubuntu")
        self.codes_install_row.add_suffix(InstallButton(
            app.software_installer, [codes.PACKAGE], self._on_codes_installed,
            lambda msg: self._toast(msg, timeout=4),
            on_size=lambda size: self.codes_install_row.set_subtitle(
                f"Needs a small free component from Ubuntu ({size})")))
        group.add(self.codes_install_row)
        self._show_codes_row(codes.available())
        group.add(_switch_row("Detect links, emails and phone numbers",
                              "Show one-click actions for what is found in the text",
                              self.settings, "detect_links"))
        page.add(group)

        # ---------------------------------------------------------- history
        group = Adw.PreferencesGroup(
            title="History",
            description=f"Up to {MAX_ENTRIES} recent results, text only, stored privately on this "
                        "computer. Captured images are never saved.")
        group.add(_switch_row("Keep recent results", None, self.settings, "history_enabled"))
        clear_row = Adw.ActionRow(title="Clear history")
        clear = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)
        clear.add_css_class("destructive-action")
        clear.connect("clicked", self._on_clear_history)
        clear_row.add_suffix(clear)
        group.add(clear_row)
        page.add(group)

        # -------------------------------------------------------- languages
        # One place manages languages (the Languages window); this row shows
        # which are on and opens it, so nothing here can go out of date.
        group = Adw.PreferencesGroup(
            title="Languages",
            description="Turn on the languages that appear in your captures. Fewer is faster.")
        self.languages_row = Adw.ActionRow(title="Recognition languages", activatable=True)
        self.languages_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.languages_row.connect("activated", lambda *_: app.show_languages(parent=self))
        self._update_languages_row()
        group.add(self.languages_row)
        page.add(group)

        group = Adw.PreferencesGroup(title="Engine")
        version = app.engine.version()
        group.add(Adw.ActionRow(title="Tesseract",
                                subtitle=f"Version {version}, running on this computer" if version else "Not installed"))
        best = Adw.ActionRow(title="High-accuracy models",
                             subtitle=f"Add high-accuracy models (tessdata_best) to {display_path(USER_TESSDATA)} "
                                      "to use them.")
        best.set_subtitle_lines(3)
        open_folder = Gtk.Button(label="Open Folder", valign=Gtk.Align.CENTER)
        open_folder.connect("clicked", lambda *_: self._open_models_folder())
        best.add_suffix(open_folder)
        group.add(best)
        page.add(group)

    def _screen_access_group(self):
        """Snap only: how Kingfisher may read the screen, and a way to take
        a remembered screen-sharing choice back."""
        route = capture.sandbox_route()
        if route == "x11":
            return None
        group = Adw.PreferencesGroup(title="Screen Access")
        if route == "screenshot":
            row = Adw.ActionRow(title="Screenshot permission",
                                subtitle="Kingfisher asks once for permission to take screenshots. "
                                         "To change your answer, see “Screen access” in the README.")
            row.set_subtitle_lines(3)
            group.add(row)
            return group
        row = Adw.ActionRow(title="Remembered screen selection")
        row.set_subtitle_lines(3)
        forget = Gtk.Button(label="Forget", valign=Gtk.Align.CENTER)

        def refresh():
            remembered = screencast.has_token()
            row.set_subtitle("Kingfisher can read the screen without asking each time." if remembered
                             else "Kingfisher will ask for permission at the next capture.")
            forget.set_sensitive(remembered)

        def on_forget(_button):
            screencast.forget()
            self.app.settings["screen_access_explained"] = False
            refresh()
            self._toast("Kingfisher will ask for screen access at the next capture.")
        forget.connect("clicked", on_forget)
        row.add_suffix(forget)
        refresh()
        group.add(row)
        return group

    def _open_models_folder(self):
        try:
            USER_TESSDATA.mkdir(parents=True, exist_ok=True)
            Gtk.show_uri(self, Gio.File.new_for_path(str(USER_TESSDATA)).get_uri(), Gdk.CURRENT_TIME)
        except (OSError, GLib.Error) as err:
            self._toast(f"Couldn't open the folder: {err}", timeout=5)

    def _show_codes_row(self, available):
        self.codes_row.set_visible(available)
        self.codes_install_row.set_visible(not available)

    def _on_codes_installed(self):
        if codes.reload():
            self.settings["read_codes"] = True
            for child in _walk(self.codes_row):
                if isinstance(child, Gtk.Switch):
                    child.set_active(True)
            self._show_codes_row(True)
            self._toast("QR code and barcode reading is on")
        else:
            self._toast("Installed. Restart Kingfisher to start reading codes", timeout=5)

    def _on_clear_history(self, *_):
        self.app.history.clear()
        self._toast("History cleared")

    def _update_languages_row(self):
        installed = set(self.app.engine.languages())
        names = [language_name(c) for c in self.settings["languages"] if c in installed] or ["English"]
        self.languages_row.set_subtitle(", ".join(names) + " · add or remove languages")

    def _on_setting(self, key, value):
        if key == "languages":
            self._update_languages_row()
        elif key == "history_enabled" and not value and self.app.history.entries():
            count = len(self.app.history.entries())
            toast = Adw.Toast(title=f"History is off. {count} saved result{'s' if count != 1 else ''} kept",
                              button_label="Clear Them", action_name="win.clear-history", timeout=8)
            self.add_toast(toast)
            fit_toasts(self)

    def _toast(self, title, timeout=3):
        """Toast titles are markup in libadwaita 1.1, so escape the text."""
        self.add_toast(Adw.Toast(title=GLib.markup_escape_text(str(title)), timeout=timeout))
        fit_toasts(self)

    def _current_accel(self):
        return shortcut.binding() or self.settings["shortcut_key"]

    def _refresh_binding(self):
        self.accel_label.set_accelerator(self._current_accel())

    def _on_shortcut_toggled(self, switch, _pspec):
        try:
            if switch.get_active():
                shortcut.enable(self._current_accel())
            else:
                shortcut.disable()
        except Exception as err:  # surface any gsettings failure
            self._toast(err, timeout=5)
            # Show the real state again, without re-triggering this handler.
            switch.handler_block_by_func(self._on_shortcut_toggled)
            switch.set_active(shortcut.is_enabled())
            switch.handler_unblock_by_func(self._on_shortcut_toggled)
        self._refresh_binding()
        self.app.shortcut_changed()

    def _apply_binding(self, accel, conflict=None):
        self.settings["shortcut_key"] = accel
        try:
            if conflict:
                shortcut.release_conflict(conflict, accel)
            if shortcut.is_enabled():
                shortcut.enable(accel)
            elif shortcut.supported():
                shortcut.enable(accel)
                self.shortcut_switch.set_active(True)
        except Exception as err:
            self._toast(err, timeout=5)
            return
        self._refresh_binding()
        self.app.shortcut_changed()
        self._toast(f"Shortcut set to {shortcut.accel_label(accel)}")


class ShortcutRecorder(Gtk.Window):
    """Modal dialog that captures the next key press as a shortcut."""

    def __init__(self, parent, on_set):
        super().__init__(transient_for=parent, modal=True, resizable=False,
                         title="Set Shortcut", default_width=440)
        self.on_set = on_set
        self.accel = None
        self.conflict = None
        self._pressed = set()
        header = Adw.HeaderBar(show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.set_button = Gtk.Button(label="Set", sensitive=False)
        self.set_button.add_css_class("suggested-action")
        self.set_button.connect("clicked", self._on_set)
        header.pack_end(self.set_button)
        self.set_titlebar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=28,
                      margin_bottom=28, margin_start=28, margin_end=28)
        box.append(Gtk.Image(icon_name="input-keyboard-symbolic", pixel_size=56,
                             css_classes=["dim-label"]))
        title = Gtk.Label(label="Press the keys you want to use")
        title.add_css_class("title-3")
        box.append(title)
        self.preview = Gtk.ShortcutLabel(halign=Gtk.Align.CENTER, disabled_text="Waiting for keys…")
        self.preview.set_size_request(-1, 36)
        box.append(self.preview)
        self.note = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=44,
                              label="Laptop keys like Fn + F9 work too. Esc cancels.")
        self.note.add_css_class("dim-label")
        self.note.add_css_class("caption")
        box.append(self.note)
        self.set_child(box)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_press)
        keys.connect("key-released", self._on_release)
        self.add_controller(keys)

    def _on_press(self, _ctrl, keyval, keycode, state):
        self._pressed.add(keycode)
        return self._capture(keyval, state)

    def _on_release(self, _ctrl, keyval, keycode, state):
        # Keys already bound in GNOME never deliver a press to apps, only the
        # release. Treat an unmatched release as the key the user pressed.
        if keycode in self._pressed:
            self._pressed.discard(keycode)
        else:
            self._capture(keyval, state)

    def _capture(self, keyval, state):
        mods = state & Gtk.accelerator_get_default_mod_mask()
        keyval = Gdk.keyval_to_lower(keyval)
        name = Gdk.keyval_name(keyval) or ""
        if keyval in MODIFIER_KEYS:
            return True
        if keyval == Gdk.KEY_Escape and not mods:
            self.close()
            return True
        special = 0x1008FF00 <= keyval <= 0x1008FFFF  # XF86 multimedia / laptop keys
        fkey = name.startswith("F") and name[1:].isdigit()
        standalone = special or fkey or name == "Print"
        if not mods and not standalone:
            self.note.set_label("Combine this key with Ctrl, Alt, Shift or Super.")
            return True
        if not Gtk.accelerator_valid(keyval, mods) and not standalone:
            self.note.set_label("That combination can't be used. Try another.")
            return True
        self.accel = Gtk.accelerator_name(keyval, mods)
        if special and not name.startswith("XF86"):
            # GNOME's shortcut parser expects the full X keysym name.
            self.accel = self.accel[: len(self.accel) - len(name)] + "XF86" + name
        self.preview.set_accelerator(self.accel)
        self.set_button.set_sensitive(True)
        self.conflict = shortcut.find_conflict(self.accel)
        self.set_button.set_label("Replace" if self.conflict else "Set")
        if self.conflict:
            self.note.set_label(f"{Gtk.accelerator_get_label(keyval, mods)} is currently used for "
                                f"{self.conflict[2]}. Replace removes it from there and gives it to Kingfisher.")
        elif not mods and fkey:
            self.note.set_label(f"Detected {name}. Apps will no longer receive {name} on its own.")
        elif special:
            self.note.set_label(f"Detected special key XF86{name.removeprefix('XF86')}.")
        else:
            self.note.set_label(f"Detected {Gtk.accelerator_get_label(keyval, mods)}.")
        return True

    def _on_set(self, *_):
        if self.accel:
            self.on_set(self.accel, self.conflict)
        self.close()
