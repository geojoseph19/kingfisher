# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Application controller: command line, capture flow, OCR jobs and actions."""

import io
import os
import sys
import threading
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from PIL import Image, ImageOps

from . import APP_ICON, APP_ID, APP_NAME, DATA_DIR, ICON_DIR, IS_SNAP, VERSION, display_path, shortcut, tray
from . import codes as code_reader
from .capture import capture_screen, last_capture_was_silent, needs_access_note
from .history import History, HistoryEntry
from .languages import PackageKitInstaller
from .ocr import CancelToken, OcrCancelled, OcrError, TesseractEngine, to_rgb
from .overlay import RegionSelector
from .settings import Settings
from .clipboard import set_clipboard_text
from .window import MainWindow

HIDE_DELAY_MS = 350                    # let the compositor finish hiding our windows
# GNOME Shell tells us about a click in the top-bar menu when the menu starts
# to fade out, which takes 150 ms. A capture taken sooner freezes the
# half-faded menu into the picture.
MENU_CLOSE_DELAY_MS = 300
PORTAL_DIALOG_DELAY_MS = 250           # let the portal's "Share" dialog close first
RESIDENT_TIMEOUT_MS = 10 * 60 * 1000   # stay loaded after the last window closes


COLOR_SCHEMES = {"default": Adw.ColorScheme.DEFAULT, "light": Adw.ColorScheme.FORCE_LIGHT,
                 "dark": Adw.ColorScheme.FORCE_DARK}


def option_path(value):
    """A path from GLib.OPTION_REMAINING.

    PyGObject exposes a FILENAME_ARRAY as a list of byte values ending in a
    NUL rather than as a string; other versions return bytes."""
    if isinstance(value, (bytes, bytearray)):
        return os.fsdecode(value)
    if isinstance(value, (list, tuple)):
        return os.fsdecode(bytes(value).rstrip(b"\0"))
    return value


def read_codes_or_none(image, reader=code_reader):
    """The QR codes and barcodes in ``image``; none if reading them fails.

    Codes are an extra. A fault in reading them must never cost the user the
    text, which is what the capture was for."""
    try:
        return reader.read_codes(image)
    except Exception as err:
        print(f"Reading codes failed: {err!r}", file=sys.stderr)
        return []


def load_image(path):
    """The image in a file, upright and in RGB."""
    with Image.open(path) as img:
        try:
            upright = ImageOps.exif_transpose(img)
        except Exception:   # damaged orientation data: read the picture as it is
            upright = img
        return to_rgb(upright)


def unreadable_message(path, error, sandboxed=IS_SNAP):
    """What to say when an image file cannot be opened.

    The snap reaches the home folder and, once connected, removable drives.
    Anything else is outside its sandbox: such a file is either refused or,
    under /tmp, not there at all, since the snap has a /tmp of its own."""
    path = Path(path)
    if sandboxed and isinstance(error, (PermissionError, FileNotFoundError)):
        return f"Kingfisher can't read files in {display_path(path.parent)}"
    return f"Not a readable image: {path.name}"


class KingfisherApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.settings = Settings()
        self.engine = TesseractEngine()
        self.history = History()
        self.window = None
        self.selector = None
        self.icon_name = APP_ID
        self._job = 0
        self._token = None        # CancelToken of the running OCR job
        self._window_job = None   # job whose busy page the main window shows
        self._capturing = False
        self._hidden = []
        self._tray = None
        self._tray_held = False
        self._access_note = None          # the one-time screen access note, while open
        self._host_watch = 0
        self._waiting_hold = False

        add = self.add_main_option
        none = GLib.OptionArg.NONE
        add("capture", ord("c"), GLib.OptionFlags.NONE, none, "Select a screen area and extract its text", None)
        add("full", ord("f"), GLib.OptionFlags.NONE, none, "Extract text from the whole screen", None)
        add("background", ord("b"), GLib.OptionFlags.NONE, none, "Start in the background with only the top-bar icon", None)
        add("enable-shortcut", 0, GLib.OptionFlags.NONE, none, "Register the system-wide keyboard shortcut and exit", None)
        add("shortcut-key", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Key for --enable-shortcut, e.g. '<Ctrl>Print'", "ACCEL")
        add("disable-shortcut", 0, GLib.OptionFlags.NONE, none, "Remove the system-wide keyboard shortcut and exit", None)
        add("version", ord("v"), GLib.OptionFlags.NONE, none, "Print the version and exit", None)
        add(GLib.OPTION_REMAINING, 0, GLib.OptionFlags.NONE, GLib.OptionArg.FILENAME_ARRAY,
            "Image files to read", "[IMAGE…]")

    def _apply_color_scheme(self):
        scheme = self.settings["color_scheme"]
        if scheme not in COLOR_SCHEMES:
            scheme = "default"
        Adw.StyleManager.get_default().set_color_scheme(COLOR_SCHEMES[scheme])
        return scheme

    def _on_color_scheme(self, action, value):
        action.set_state(value)
        self.settings["color_scheme"] = value.get_string()
        self._apply_color_scheme()

    def _on_dark_changed(self, style, *_):
        display = Gdk.Display.get_default()
        dark = style.get_dark()
        if dark and not self._dark_css_added:
            for provider in self._dark_css:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        elif not dark and self._dark_css_added:
            for provider in self._dark_css:
                Gtk.StyleContext.remove_provider_for_display(display, provider)
        self._dark_css_added = dark

    @staticmethod
    def _css(name):
        provider = Gtk.CssProvider()
        provider.load_from_path(str(DATA_DIR / name))
        return provider

    # =============================================================== startup
    def do_startup(self):
        Adw.Application.do_startup(self)
        self.connect("window-removed", self._on_window_removed)
        self.set_inactivity_timeout(RESIDENT_TIMEOUT_MS)
        self._apply_color_scheme()

        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_path(str(DATA_DIR / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._dark_css = [self._css("style-dark.css")]
        if (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6):
            # Newer libadwaita reads its colors from CSS variables only.
            Gtk.StyleContext.add_provider_for_display(display, self._css("colors.css"),
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self._dark_css.append(self._css("colors-dark.css"))
        self._dark_css_added = False
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", self._on_dark_changed)
        self._on_dark_changed(style)
        theme = Gtk.IconTheme.get_for_display(display)
        theme.add_search_path(str(ICON_DIR))
        # Installed builds ship the icon as APP_ID; source checkouts only have APP_ICON.
        self.icon_name = APP_ID if theme.has_icon(APP_ID) else APP_ICON
        Gtk.Window.set_default_icon_name(self.icon_name)

        actions = {
            "capture-area": (lambda *_: self.start_capture("area"), ["<Ctrl>n"]),
            "capture-screen": (lambda *_: self.start_capture("screen"), ["<Ctrl><Shift>n"]),
            "capture-delayed": (lambda *_: self.start_capture("screen", delay=3), []),
            "open-image": (lambda *_: self.open_image(), ["<Ctrl>o"]),
            "paste-image": (lambda *_: self.paste_image(), ["<Ctrl><Shift>v"]),
            "preferences": (lambda *_: self.show_preferences(), ["<Ctrl>comma"]),
            "shortcuts": (lambda *_: self.show_shortcuts(), ["<Ctrl>question"]),
            "about": (lambda *_: self.show_about(), []),
            "languages": (lambda *_: self.show_languages(), ["<Ctrl>l"]),
            "quit": (lambda *_: self.quit(), ["<Ctrl>q"]),
        }
        for name, (callback, accels) in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        scheme = Gio.SimpleAction.new_stateful("color-scheme", GLib.VariantType.new("s"),
                                               GLib.Variant("s", self._apply_color_scheme()))
        scheme.connect("change-state", self._on_color_scheme)
        self.add_action(scheme)
        self.set_accels_for_action("win.copy", ["<Ctrl><Shift>c"])
        self.set_accels_for_action("win.save", ["<Ctrl>s"])
        self.set_accels_for_action("win.home", ["<Alt>Left"])
        self.set_accels_for_action("win.history", ["<Ctrl>h"])
        self.set_accels_for_action("window.close", ["<Ctrl>w"])

        self.settings.connect(self._on_setting_changed)
        self._update_tray()

    def do_activate(self):
        self.get_window().present()

    def do_handle_local_options(self, options):
        """One-shot commands run here, before the app registers or starts its
        top-bar icon, so they print their result and exit immediately."""
        # Read without end(): it would invalidate the dict GApplication passes on.
        opts = {key: options.lookup_value(key, None).unpack()
                for key in ("version", "enable-shortcut", "disable-shortcut", "shortcut-key")
                if options.contains(key)}
        if opts.get("version"):
            print(f"{APP_NAME} {VERSION}")
            return 0
        if opts.get("enable-shortcut"):
            return self._enable_shortcut_cli(opts.get("shortcut-key") or self.settings["shortcut_key"])
        if opts.get("disable-shortcut"):
            shortcut.disable()
            print("Shortcut removed")
            return 0
        return -1  # continue with normal start-up

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if options.get("background"):
            self._wait_for_tray()
            return 0

        files = [option_path(value) for value in options.get(GLib.OPTION_REMAINING) or []]
        if files:
            name = files[0]
            path = command_line.create_file_for_arg(name).get_path()
            if path is None:
                self.get_window().present()
                self.get_window().toast("Only images on this computer can be opened", timeout=4)
            else:
                self.ocr_file(path)
                if len(files) > 1:
                    self.get_window().toast(f"Reading the first of {len(files)} images", timeout=4)
        elif options.get("capture"):
            # The system-wide shortcut: capture the screen exactly as it is,
            # Kingfisher's own windows included, like GNOME's Print Screen.
            self.start_capture("area", step_aside=False)
        elif options.get("full"):
            self.start_capture("screen", step_aside=False)
        else:
            self.activate()
        return 0

    def _enable_shortcut_cli(self, accel):
        ok, key, _mods = Gtk.accelerator_parse(accel)
        if not ok or not key:
            print(f"Not a valid key: {accel}", file=sys.stderr)
            return 1
        try:
            shortcut.enable(accel)
        except RuntimeError as err:
            print(err, file=sys.stderr)
            return 1
        self.settings["shortcut_key"] = accel
        print(f"Shortcut enabled: {shortcut.binding_label()}")
        return 0

    # ================================================================ window
    def get_window(self):
        # A closed window is detached from the application; never reuse it.
        if self.window is not None and self.window.get_application() is None:
            self.window = None
        if self.window is None:
            self.window = MainWindow(self)
            self.window.update_global_hint(shortcut.binding_label())
        return self.window

    def _live_window(self):
        if self.window is not None and self.window.get_application() is not None:
            return self.window
        return None

    def _on_window_removed(self, _app, window):
        # PyGObject keeps the wrapper alive, so "destroy" is not reliable here.
        if window is self.window:
            self.window = None

    # ================================================================== tray
    def _update_tray(self):
        if not self.settings["show_tray"]:
            self._remove_tray()
            return
        if self._tray is not None:
            return
        if not tray.available():
            # No tray host yet (locked screen, or the extension is still
            # loading at login): create the icon as soon as one appears.
            if not self._host_watch:
                self._host_watch = tray.watch_host(self._on_tray_host)
            return
        self._tray = tray.TrayIcon(str(ICON_DIR / "tray" / "kingfisher-tray-mark.svg"), APP_NAME, [
            ("Select Area…", lambda: self.start_capture("area", step_aside=False, from_menu=True)),
            ("Full Screen", lambda: self.start_capture("screen", step_aside=False, from_menu=True)),
            None,
            (f"Open {APP_NAME}", lambda: self.get_window().present()),
            ("Recent Results…", self.show_history),
            ("Preferences", self.show_preferences),
            None,
            (f"Quit {APP_NAME}", self.quit),
        ])
        self._tray.show()
        if not self._tray_held:
            # Keep running in the background while the icon is shown.
            self.hold()
            self._tray_held = True
        self._stop_waiting_for_tray()

    def _on_tray_host(self):
        # The watcher can appear a moment before it accepts icons; retry briefly.
        attempts = {"left": 10}

        def attempt():
            self._update_tray()
            attempts["left"] -= 1
            return self._tray is None and attempts["left"] > 0 and self.settings["show_tray"]

        if attempt():
            GLib.timeout_add(500, attempt)

    def _remove_tray(self):
        if self._host_watch:
            tray.unwatch_host(self._host_watch)
            self._host_watch = 0
        if self._tray is not None:
            self._tray.hide()
            self._tray = None
        if self._tray_held:
            self._tray_held = False
            self.release()
        self._stop_waiting_for_tray()

    def _wait_for_tray(self):
        """--background (start at login): stay running until a tray host
        appears, e.g. after the AppIndicator extension loads or the screen is
        unlocked, so the icon always shows up."""
        if self._tray is not None or self._waiting_hold or not self.settings["show_tray"]:
            return
        if not tray.supported():
            return   # no top-bar menu support installed: an icon can never appear
        self.hold()
        self._waiting_hold = True

    def _stop_waiting_for_tray(self):
        if self._waiting_hold:
            self._waiting_hold = False
            self.release()

    def _on_setting_changed(self, key, _value):
        if key == "show_tray":
            self._update_tray()

    # =============================================================== capture
    def start_capture(self, mode, delay=0, step_aside=True, counted_down=0, from_menu=False):
        """mode: "area" (interactive selection) or "screen" (whole screen).

        ``step_aside``: hide Kingfisher's windows first. Right when the capture
        starts from inside Kingfisher (its window would cover what you want);
        wrong for the system shortcut or the top-bar icon, which must capture
        the screen exactly as it is, Kingfisher included.

        ``counted_down``: the countdown that has just run for this capture,
        so that it can run again if screen access must be asked for first.

        ``from_menu``: started from the top-bar icon's menu, which is still
        closing: wait for it, or it ends up in the picture.
        """
        if self._capturing and self.selector is not None and not self.selector.alive():
            # The overlay was closed behind the selector's back: start over.
            self.selector = None
            self._finish_capture()
        if self._capturing:
            # Pressed again while selecting: bring the selection to the front.
            if self.selector:
                for win in self.selector.windows:
                    win.present()
            return
        live = self._live_window()
        if live is not None:
            live.stop_countdown(restore=True)
        if not self.settings["screen_access_explained"] and needs_access_note():
            self._explain_screen_access(lambda aside: self.start_capture(mode, delay, step_aside or aside))
            return
        if delay:
            win = self.get_window()
            win.present()
            win.start_countdown(delay, lambda: self.start_capture(mode, counted_down=delay))
            return

        self._capturing = True
        self.hold()
        # When starting from inside Kingfisher, hide its windows (main window,
        # preferences, dialogs) so none of them covers the screenshot.
        self._hidden = [w for w in self.get_windows() if w.get_visible()] if step_aside else []
        for win in self._hidden:
            win.set_visible(False)
        was_visible = bool(self._hidden)

        def grab(retries):
            def on_result(image, error):
                if error and retries and getattr(error, "retryable", False):
                    GLib.timeout_add(400, grab, retries - 1)
                else:
                    self._on_captured(image, error, mode, was_visible, counted_down)
            capture_screen(on_result)
            return GLib.SOURCE_REMOVE

        idle_wait = MENU_CLOSE_DELAY_MS if from_menu else 60
        GLib.timeout_add(HIDE_DELAY_MS if was_visible else idle_wait, grab, 1)

    def _on_captured(self, image, error, mode, was_visible, counted_down=0):
        if error:
            self._finish_capture()
            if getattr(error, "needs_access", False):
                # The desktop refused its screenshot permission, usually
                # without asking the user anything. The capture has switched
                # to screen sharing, which does ask: start again, beginning
                # with the note that explains it.
                if was_visible:
                    self.get_window().present()
                self.start_capture(mode, delay=counted_down, step_aside=was_visible)
                return
            if was_visible or getattr(error, "retryable", True):
                win = self.get_window()
                win.present()
                win.toast(error, timeout=4)
            # Otherwise the user cancelled a shortcut-triggered capture: stay quiet.
            return
        if mode == "screen":
            self._finish_capture()
            self.get_window().present()
            self.run_ocr(image, "screen")
            return

        def on_done(selector, box):
            if box is None:
                self.selector = None
                self._finish_capture()
                if was_visible:
                    self.get_window().present()
                return
            crop = image.crop(box)
            if self.settings["quick_mode"] == "copy":
                self._quick_copy(selector, crop, was_visible)
                return
            # Present the main window while the overlay still has focus so the
            # window manager hands focus over instead of showing "is ready".
            self.get_window().present()
            selector.close()
            self.selector = None
            self._finish_capture()
            self.run_ocr(crop)

        def open_selector():
            self.selector = RegionSelector(self, image, self.settings, on_done)
            return GLib.SOURCE_REMOVE

        if last_capture_was_silent():
            open_selector()
        else:
            GLib.timeout_add(PORTAL_DIALOG_DELAY_MS, open_selector)

    def _explain_screen_access(self, proceed):
        """Shown once in the snap, before GNOME's screen-sharing dialog first
        appears: what to choose in it so that GNOME does not ask again.

        The note is attached to the main window, which centres it and dims the
        window behind it, so the window comes to the front for it. The capture
        must not include it: a window opened only for the note is closed
        again, and one that was already open steps aside for this capture.

        ``proceed(step_aside)`` starts the capture."""
        if self._access_note is not None:
            self._access_note.present(self.get_window())   # already open: bring it back
            return
        if not hasattr(Adw, "AlertDialog"):   # libadwaita before 1.5
            self.settings["screen_access_explained"] = True
            proceed(False)
            return
        dialog = Adw.AlertDialog(
            heading="Allow Screen Access",
            body="Kingfisher needs permission to read your screen. You only need to give it once.\n\n"
                 "In the next window, select every screen you use, turn on "
                 "“Remember this selection”, then click Share.")
        dialog.add_response("cancel", "Not Now")
        dialog.add_response("continue", "Continue")
        dialog.set_response_appearance("continue", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("continue")
        dialog.set_close_response("cancel")

        window = self.get_window()
        opened_for_note = not window.get_visible()

        def on_response(_dialog, response):
            self._access_note = None
            if opened_for_note:
                # Close rather than hide: a hidden window would keep the
                # application running unseen when there is no top-bar icon.
                # The capture opens a new window if it needs one.
                window.close()
            if response != "continue":
                return
            self.settings["screen_access_explained"] = True
            # Let the note fade out before the capture starts.
            GLib.timeout_add(HIDE_DELAY_MS, lambda: proceed(True) and False)
        dialog.connect("response", on_response)
        self._access_note = dialog
        window.present()
        dialog.present(window)

    def _finish_capture(self):
        if not self._capturing:
            return
        self._capturing = False
        for win in self._hidden:
            if win is not self.window and win.get_application() is not None:
                win.present()
        self._hidden = []
        self.release()

    def _quick_copy(self, selector, crop, was_visible):
        """Copy-only mode: OCR while the selection stays on screen, then copy."""
        job, token = self._next_job()
        source = next((w for w in selector.windows if w.busy.get_visible()), selector.windows[0])

        def done(result, error, cancelled):
            if cancelled or job != self._job:
                # Cancelled (Esc) or replaced: never leave the overlay up.
                if self.selector is selector:
                    selector.close()
                    self.selector = None
                    self._finish_capture()
                    if was_visible:
                        self.get_window().present()
                return
            text = self._result_text(result)
            if result is not None:
                self._remember(result, "area")
            if text.strip():
                set_clipboard_text(source.get_clipboard(), text)
            show_window = was_visible or error or not text.strip()
            if show_window:
                win = self.get_window()
                win.present()
                if result is not None:
                    win.show_result(result, crop)
            selector.close()
            self.selector = None
            if show_window:
                if error:
                    self.get_window().toast(error, timeout=4)
                elif text.strip():
                    self.get_window().toast("Text copied to clipboard")
            else:
                self._notify(text, result)
            self._finish_capture()

        self._ocr_thread(crop, done, token)

    def _notify(self, text, result):
        title = ("Text copied to clipboard" if result.words else
                 f"{result.codes[0].kind} copied to clipboard")
        notification = Gio.Notification.new(title)
        # Never put the captured text itself in a notification: it can stay in
        # the message list and show on the lock screen.
        count = result.word_count
        notification.set_body(f"{count} word{'s' if count != 1 else ''} · paste it anywhere" if result.words
                              else "Paste it anywhere")
        notification.set_icon(Gio.ThemedIcon.new_from_names([APP_ID, APP_ICON]))
        self.send_notification("copied", notification)

    # =============================================================== history
    def _result_text(self, result):
        if result is None:
            return ""
        if result.words:
            return result.text(self.settings["text_format"])
        return "\n".join(c.data for c in result.codes)

    def _remember(self, result, source):
        if not self.settings["history_enabled"]:
            return
        self.history.add(HistoryEntry(
            # Keep both layouts so the format switch also works from history.
            text=result.text("lines") if result.words else "",
            paragraphs=result.text("paragraphs") if result.words else "",
            word_count=result.word_count, confidence=round(result.confidence, 1),
            languages=list(result.languages), codes=[[c.kind, c.data] for c in result.codes],
            source=source))

    def show_history(self):
        win = self.get_window()
        win.present()
        GLib.timeout_add(150, lambda: win.show_history() or False)

    # =================================================================== OCR
    def _next_job(self):
        """Start a new OCR job; any job still running is cancelled."""
        self._job += 1
        if self._token is not None:
            self._token.cancel()
        self._token = CancelToken()
        return self._job, self._token

    def _ocr_thread(self, image, callback, token):
        """Run OCR off the main loop. ``callback(result, error, cancelled)``
        always runs exactly once on the main loop, so whatever waits on the
        job (the busy page, the selection overlay) can always clean up."""
        langs = list(self.settings["languages"])
        layout = self.settings["layout"]
        enhance = self.settings["enhance"]
        model = self.settings["model"]
        read_codes = self.settings["read_codes"] and code_reader.available()

        def deliver(*args):
            # Default priority, above redrawing: an animation must never hold back a result.
            GLib.idle_add(lambda: callback(*args) and False, priority=GLib.PRIORITY_DEFAULT)

        def work():
            try:
                token.check()
                found = read_codes_or_none(image) if read_codes else []
                token.check()
                # Hide codes from OCR so their patterns aren't read as text.
                result = self.engine.recognize(code_reader.mask_codes(image, found),
                                               langs, layout, enhance, model, token=token)
                result.codes = found
                deliver(result, None, False)
            except OcrCancelled:
                deliver(None, None, True)
            except OcrError as err:
                deliver(None, str(err), False)
            except Exception as err:  # never leave the UI spinning
                deliver(None, f"Unexpected error: {err}", False)

        threading.Thread(target=work, daemon=True).start()

    def run_ocr(self, image, source="area"):
        win = self.get_window()
        win.stop_countdown(restore=False)
        if not self.engine.available:
            win.refresh_engine_state()
            return
        job, token = self._next_job()
        self._window_job = job
        win.show_busy(image)

        def done(result, error, cancelled):
            if self._live_window() is not win:
                return
            if cancelled or job != self._job:
                # Replaced or cancelled. If nothing newer took over the window
                # (e.g. a quick copy did), leave the busy page.
                if self._window_job == job:
                    self._window_job = None
                    win.hide_busy()
                return
            self._window_job = None
            if error:
                win.hide_busy()
                win.toast(error, timeout=4)
                return
            win.show_result(result, image)
            self._remember(result, source)
            if (result.words or result.codes) and self.settings["auto_copy"]:
                win.copy_result(silent=True)
                win.toast("Text copied to clipboard" if result.words else
                          f"{result.codes[0].kind} copied to clipboard")

        self._ocr_thread(image, done, token)

    def cancel_ocr(self):
        """Stop whatever is being read: the main window's job or a quick copy."""
        self._job += 1
        if self._token is not None:
            self._token.cancel()
            self._token = None
        win = self._live_window()
        if win is not None:
            self._window_job = None
            win.hide_busy()

    # ================================================================ inputs
    def ocr_file(self, path):
        win = self.get_window()
        win.present()
        if not path:
            win.toast("Only images on this computer can be opened", timeout=4)
            return
        try:
            image = load_image(path)
        except Exception as err:   # a damaged file can fail in many ways; none may go unreported
            win.toast(unreadable_message(path, err), timeout=5)
            return
        self.run_ocr(image, "file")

    def open_image(self):
        dialog = Gtk.FileChooserNative(title="Open Image", transient_for=self.get_window(), modal=True,
                                       action=Gtk.FileChooserAction.OPEN, accept_label="_Open")
        images = Gtk.FileFilter(name="Images")
        images.add_pixbuf_formats()
        dialog.add_filter(images)

        def on_response(dlg, response):
            if response == Gtk.ResponseType.ACCEPT and dlg.get_file():
                self.ocr_file(dlg.get_file().get_path())
            dlg.destroy()

        dialog.connect("response", on_response)
        self._file_dialog = dialog  # keep a reference while it is open
        dialog.show()

    def paste_image(self):
        win = self.get_window()

        def on_texture(clipboard, result):
            try:
                texture = clipboard.read_texture_finish(result)
            except GLib.Error:
                texture = None
            if texture is None:
                win.toast("The clipboard does not contain an image")
                return
            data = texture.save_to_png_bytes().get_data()
            self.run_ocr(to_rgb(Image.open(io.BytesIO(data))), "clipboard")

        win.get_clipboard().read_texture_async(None, on_texture)

    # =============================================================== dialogs
    def show_preferences(self):
        from .preferences import PreferencesWindow
        PreferencesWindow(self, self.get_window()).present()

    # Swappable so tests can use a fake; None means "no PackageKit here".
    software_installer = staticmethod(PackageKitInstaller.create)

    def show_languages(self, parent=None):
        from .language_window import LanguagesWindow
        if getattr(self, "_languages", None) is not None and self._languages.get_application() is not None:
            self._languages.present()
            return
        self._languages = LanguagesWindow(self, parent or self.get_active_window() or self.get_window(),
                                          self.software_installer())
        self._languages.present()

    def window_engine_changed(self):
        """A language pack was added or removed: refresh what lists languages."""
        if self._live_window():
            self.window.refresh_engine_state()

    def shortcut_changed(self):
        if self._live_window():
            self.window.update_global_hint(shortcut.binding_label())

    def show_shortcuts(self):
        builder = Gtk.Builder.new_from_file(str(DATA_DIR / "shortcuts.ui"))
        dialog = builder.get_object("shortcuts")
        dialog.set_transient_for(self.get_window())
        dialog.present()

    def show_about(self):
        from .about import AboutWindow
        AboutWindow(self.get_window(), self.icon_name).present()
