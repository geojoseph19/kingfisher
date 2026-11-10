# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Main application window."""

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from PIL import Image

from . import APP_NAME, ICON_DIR, IS_SNAP
from .clipboard import set_clipboard_text
from .codes import icon_name as code_icon
from .detect import find_entities
from .effects import (CheckGlyph, CopyGlyph, OpenGlyph, PasteGlyph, SaveGlyph, ScanSweep, ScreenGlyph,
                      SelectGlyph, note_activity)
from .scene import LakeScene, ThemeSwatch
from .history import MAX_ENTRIES, group_by_day, history_meta, relative_time
from .horizon import HorizonLayout
from .start_icon import FlightLayer, StartIcon
from .install_button import InstallButton
from .toasts import fit_toasts
from .ocr import language_name, looks_tabular

NARROW_WIDTH = 820
RECENT_ON_WELCOME = 3
BUSY_MAX = (440, 260)   # largest size of the capture shown while reading


def pil_to_texture(image):
    rgb = image.convert("RGB")
    return Gdk.MemoryTexture.new(
        rgb.width, rgb.height, Gdk.MemoryFormat.R8G8B8,
        GLib.Bytes.new(rgb.tobytes()), rgb.width * 3)


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def keycaps(label):
    """A row of keycap labels for an accelerator label such as "Ctrl+Print"."""
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
    for key in (label.split("+") if label else []):
        cap = Gtk.Label(label=key.strip() or "+")
        cap.add_css_class("keycap")
        box.append(cap)
    return box


def play_once(widget, base, duration_ms):
    """Play a CSS animation once, then take it off the widget again.

    GTK 4.6 forgets a CSS animation once it has finished, so any later style
    change (the pointer entering the window, hover, focus) would start it
    over: elements would blink. The class is therefore only present while the
    animation runs. Twin classes ("x" / "x-b") restart it if it is replayed
    mid-way."""
    if widget.has_css_class(base):
        widget.remove_css_class(base)
        widget.add_css_class(base + "-b")
    else:
        widget.remove_css_class(base + "-b")
        widget.add_css_class(base)
    previous = getattr(widget, "_kf_anim_timer", 0)
    if previous:
        GLib.source_remove(previous)

    def finish():
        widget._kf_anim_timer = 0
        widget.remove_css_class(base)
        widget.remove_css_class(base + "-b")
        return GLib.SOURCE_REMOVE
    widget._kf_anim_timer = GLib.timeout_add(duration_ms + 60, finish)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self.settings = app.settings
        self.result = None
        self.image = None
        self._countdown_id = 0
        self._detect_id = 0
        self._copied_id = 0
        self._chip_keys = None
        self._history_codes = None
        self._previous_page = "welcome"
        self._result_origin = "welcome"
        self._history_entry = None
        self._undo_items = {}       # undo token -> [(position, entry), ...]
        self._undo_serial = 0
        self._before_busy = "welcome"
        self.set_default_size(self.settings["window_width"], self.settings["window_height"])
        self.set_size_request(400, 460)

        self.toasts = Adw.ToastOverlay()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._build_header())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=220, vexpand=True)
        self.stack.add_named(self._build_welcome(), "welcome")
        self.stack.add_named(self._build_missing(), "missing")
        self.stack.add_named(self._build_busy(), "busy")
        self.stack.add_named(self._build_countdown(), "countdown")
        self.stack.add_named(self._build_result(), "result")
        self.stack.add_named(self._build_empty(), "empty")
        self.stack.add_named(self._build_history_page(), "history")
        self.stack.connect("notify::visible-child-name", self._on_page_changed)
        body = Gtk.Overlay(vexpand=True)
        body.set_child(self.stack)
        body.add_overlay(self._build_drop_zone())
        root.append(body)
        self.toasts.set_child(root)
        self.set_content(self.toasts)

        self._install_actions()
        self._install_drop_target()
        self.connect("notify::default-width", self._on_resize)
        self.connect("close-request", self._on_close)
        self.settings.connect(self._on_setting_changed)
        self.app.history.connect(self._on_history_changed)
        self.refresh_engine_state()
        self._populate_recent()
        self._on_page_changed()
        self._on_resize()

    # ================================================================ header
    def _build_header(self):
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=APP_NAME))

        menu = Gio.Menu()
        menu.append("Select Area", "app.capture-area")
        menu.append("Full Screen", "app.capture-screen")
        menu.append("Full Screen in 3 Seconds", "app.capture-delayed")
        section = Gio.Menu()
        section.append("Open Image…", "app.open-image")
        section.append("Paste Image", "app.paste-image")
        menu.append_section(None, section)

        self.capture_button = Adw.SplitButton(menu_model=menu,
                                              tooltip_text="Select an area to extract text (Ctrl+N)")
        self.capture_button.set_action_name("app.capture-area")
        self.capture_button.update_property([Gtk.AccessibleProperty.LABEL], ["Capture"])
        # The same living Select Area icon as on the start page; it follows the
        # main half of the split button, not the arrow.
        main_half = next((child for child in _children(self.capture_button)
                          if isinstance(child, Gtk.Button) and not isinstance(child, Gtk.MenuButton)),
                         self.capture_button)
        capture_content = Gtk.Box(spacing=6)
        capture_content.append(SelectGlyph(main_half))
        capture_content.append(Gtk.Label(label="Capture"))
        self.capture_button.set_child(capture_content)
        self.capture_button.add_css_class("suggested-action")
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic", action_name="win.home",
                                      tooltip_text="Back (Alt+Left)")
        header.pack_start(self.back_button)
        header.pack_start(self.capture_button)

        main_menu = Gio.Menu()
        theme = Gio.MenuItem()
        theme.set_attribute_value("custom", GLib.Variant("s", "theme"))
        main_menu.append_item(theme)
        section = Gio.Menu()
        section.append("Open Image…", "app.open-image")
        section.append("Paste Image", "app.paste-image")
        main_menu.append_section(None, section)
        section = Gio.Menu()
        section.append("Preferences", "app.preferences")
        section.append("Keyboard Shortcuts", "app.shortcuts")
        section.append(f"About {APP_NAME}", "app.about")
        main_menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=main_menu,
                                     tooltip_text="Main Menu", primary=True)
        menu_button.get_popover().add_child(self._build_theme_switcher(), "theme")
        header.pack_end(menu_button)

        self.lang_button = Gtk.MenuButton(tooltip_text="Recognition languages")
        self.lang_button.update_property([Gtk.AccessibleProperty.LABEL], ["Recognition languages"])
        self.lang_button.add_css_class("flat")
        # Fixed width: the name sits in a set-width slot and the "+N" badge
        # always keeps its space, so the header never shifts when languages
        # are switched on or off.
        self.lang_label = Gtk.Label(label="English", width_chars=7, max_width_chars=9, xalign=0,
                                    ellipsize=Pango.EllipsizeMode.END)
        # "+N" sits on the globe like a notification badge, so it takes no room.
        globe = Gtk.Overlay()
        globe.set_child(Gtk.Image(icon_name="preferences-desktop-locale-symbolic"))
        self.lang_badge = Gtk.Label(label="+1", opacity=0, halign=Gtk.Align.END, valign=Gtk.Align.START,
                                    can_target=False)
        self.lang_badge.add_css_class("count-badge")
        globe.add_overlay(self.lang_badge)
        lang_box = Gtk.Box(spacing=6)
        lang_box.append(globe)
        lang_box.append(self.lang_label)
        lang_box.append(Gtk.Image(icon_name="pan-down-symbolic"))
        self.lang_button.set_child(lang_box)
        self.lang_popover = Gtk.Popover()
        self.lang_popover.add_css_class("menu")
        self.lang_button.set_popover(self.lang_popover)
        self.lang_popover.connect("show", lambda *_: self._populate_languages())
        header.pack_end(self.lang_button)

        self.history_button = Gtk.Button(icon_name="document-open-recent-symbolic",
                                         action_name="win.history", tooltip_text="History (Ctrl+H)")
        header.pack_end(self.history_button)
        return header

    @staticmethod
    def _build_theme_switcher():
        """System / light / dark, each shown as a tiny version of the lake."""
        box = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, margin_top=4, margin_bottom=2, homogeneous=True)
        box.add_css_class("theme-switcher")
        group = None
        for value, label, tooltip in (("default", "System", "Follow the system style"),
                                      ("light", "Light", "Light style: dawn on the lake"),
                                      ("dark", "Dark", "Dark style: night on the lake")):
            button = Gtk.ToggleButton(action_name="app.color-scheme", tooltip_text=tooltip,
                                      action_target=GLib.Variant("s", value), group=group)
            button.add_css_class("theme-option")
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            content.append(ThemeSwatch(button, value))
            caption = Gtk.Label(label=label)
            caption.add_css_class("caption")
            content.append(caption)
            button.set_child(content)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            group = group or button
            box.append(button)
        return box

    def _on_page_changed(self, *_):
        page = self.stack.get_visible_child_name()
        # The welcome page has its own large capture buttons.
        self.capture_button.set_visible(page in ("result", "empty"))
        self.back_button.set_visible(page in ("result", "empty", "history"))
        # Only offer History where it can open.
        self.history_button.set_visible(page not in ("history", "busy", "countdown", "missing"))
        if page == "welcome":
            self._populate_recent()   # keep "5 min ago" style times current

    # ============================================================== history
    def show_history(self):
        """Open the History page (a full page you navigate to and back from)."""
        if self.stack.get_visible_child_name() in ("busy", "countdown", "missing"):
            return
        self._populate_history()
        self.stack.set_visible_child_name("history")

    def _build_history_page(self):
        self.history_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                         margin_top=24, margin_bottom=32, margin_start=18, margin_end=18)
        head = Gtk.Box(spacing=12)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        title = Gtk.Label(label="History", xalign=0)
        title.add_css_class("title-2")
        titles.append(title)
        note = Gtk.Label(label=f"Your last {MAX_ENTRIES} results. Only the text is kept; "
                               "captures are never stored.",
                         xalign=0, wrap=True)
        note.add_css_class("dim-label")
        titles.append(note)
        head.append(titles)
        clear = Gtk.Button(label="Clear All", valign=Gtk.Align.CENTER)
        clear.add_css_class("destructive-action")
        clear.connect("clicked", lambda *_: self._clear_history())
        head.append(clear)
        column.append(head)
        self.history_groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        column.append(self.history_groups)
        clamp = Adw.Clamp(maximum_size=680, child=column)
        self.history_stack.add_named(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp),
                                     "list")

        self.history_empty = Adw.StatusPage(icon_name="document-open-recent-symbolic")
        prefs = Gtk.Button(label="Open Preferences", action_name="app.preferences", halign=Gtk.Align.CENTER)
        prefs.add_css_class("pill")
        self.history_empty_prefs = prefs
        self.history_empty.set_child(prefs)
        self.history_stack.add_named(self.history_empty, "empty")
        return self.history_stack

    def _populate_history(self):
        while (child := self.history_groups.get_first_child()) is not None:
            self.history_groups.remove(child)
        entries = self.app.history.entries()
        if not entries:
            off = not self.settings["history_enabled"]
            self.history_empty.set_title("History Is Off" if off else "No History Yet")
            self.history_empty.set_description(
                "Turn it on in Preferences to find your recent results here." if off
                else "Text you extract will appear here, so you can come back to it later.")
            self.history_empty_prefs.set_visible(off)
            self.history_stack.set_visible_child_name("empty")
            return
        for label, group in group_by_day(entries):
            heading = Gtk.Label(label=label, xalign=0, margin_top=24, margin_bottom=8)
            heading.add_css_class("heading")
            self.history_groups.append(heading)
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            for entry in group:
                listbox.append(self._history_row(entry))
            self.history_groups.append(listbox)
        self.history_stack.set_visible_child_name("list")

    def _history_row(self, entry):
        when = entry.created_local
        what = history_meta(entry).split(" · ")[-1]
        row = Adw.ActionRow(title=GLib.markup_escape_text(entry.title[:120]),
                            subtitle=f"{when.strftime('%H:%M')} · {what}" if when else what,
                            activatable=True)
        row.set_title_lines(1)
        row.add_prefix(Gtk.Image(icon_name=self._entry_icon(entry)))
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Remove from history")
        remove.add_css_class("flat")
        remove.add_css_class("circular")
        remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {entry.title[:60]}"])
        remove.connect("clicked", lambda *_: self._remove_history(entry))
        row.add_suffix(remove)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self.show_history_entry(entry, origin="history"))
        return row

    def _remove_history(self, entry):
        entries = self.app.history.entries()
        removed = [(i, e) for i, e in enumerate(entries) if e.id == entry.id]
        self.app.history.remove(entry.id)
        self._undo_toast("Removed from history", removed)

    def _clear_history(self):
        entries = self.app.history.entries()
        if not entries:
            return
        self.app.history.clear()
        self._undo_toast("History cleared", list(enumerate(entries)))

    def _undo_toast(self, title, removed):
        """Each toast carries its own token, so Undo on an older toast puts
        back exactly what that toast removed."""
        self._undo_serial += 1
        token = str(self._undo_serial)
        self._undo_items[token] = removed
        toast = Adw.Toast(title=title, button_label="Undo", action_name="win.undo-history",
                          action_target=GLib.Variant("s", token), timeout=5)
        toast.connect("dismissed", lambda *_: self._undo_items.pop(token, None))
        self.toasts.add_toast(toast)
        fit_toasts(self.toasts)

    def _undo_history_change(self, token):
        removed = self._undo_items.pop(token, None)
        if removed:
            self.app.history.reinsert(removed)

    @staticmethod
    def _entry_icon(entry):
        if entry.codes and not entry.word_count:
            return code_icon(entry.codes[0][0])
        return "kingfisher-extract-symbolic"

    def show_history_entry(self, entry, origin="welcome"):
        """Show a result from history: text only, the capture is not stored."""
        self.stop_countdown(restore=False)
        self._result_origin = origin
        self.result = None
        self.image = None
        self._history_codes = [tuple(c) for c in entry.codes]
        self.picture.set_paintable(None)
        # Only text is kept in history, so give it the whole width.
        self.preview_card.set_visible(False)
        self.dims_label.set_visible(False)
        self.boxes_toggle.set_sensitive(False)
        self._history_entry = entry
        self._set_formats_available(entry.has_formats, [c[0] for c in entry.codes])
        self._render_history_text()
        stamp = relative_time(entry.created_local)
        self._set_stats(None, f"From history · {stamp}" if stamp else "From history")
        self._show_result_page()
        self.present()

    def _on_history_changed(self):
        if self.get_application() is not None:
            self._populate_recent()
            if self.stack.get_visible_child_name() == "history":
                self._populate_history()

    # ============================================================ languages
    def _populate_languages(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6,
                      margin_bottom=6, margin_start=6, margin_end=6)
        heading = Gtk.Label(label="Recognize text in", xalign=0, margin_start=8, margin_top=4, margin_bottom=6)
        heading.add_css_class("heading")
        box.append(heading)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        selected = set(self.settings["languages"])
        codes = self.app.engine.languages()
        for code in codes:
            check = Gtk.CheckButton(label=language_name(code), active=code in selected)
            check.add_css_class("lang-check")
            check.connect("toggled", self._on_language_toggled, code)
            inner.append(check)
        if len(codes) > 8:
            # Only scroll when the list is long; a scroller reserves room for its
            # scrollbar, which leaves a gap under a short list.
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, height_request=300,
                                          child=inner)
            box.append(scroller)
        else:
            box.append(inner)
        box.append(Gtk.Separator(margin_top=6, margin_bottom=4))
        more = Gtk.Button(action_name="app.languages", tooltip_text="Add or remove languages (Ctrl+L)")
        more.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Add Languages…"))
        more.add_css_class("flat")
        more.connect("clicked", lambda *_: self.lang_popover.popdown())
        box.append(more)
        self.lang_popover.set_child(box)

    def _on_language_toggled(self, check, code):
        if not self.settings.set_language(code, check.get_active()):
            check.set_active(True)

    def _update_language_label(self):
        installed = set(self.app.engine.languages())
        langs = [lang for lang in self.settings["languages"] if lang in installed]
        if not langs:
            langs = ["eng"] if "eng" in installed else []
        names = [language_name(code) for code in langs]
        self.lang_label.set_label(names[0] if names else "None")
        extra = len(names) - 1
        self.lang_badge.set_label(f"+{extra}" if extra > 0 else "+1")
        self.lang_badge.set_opacity(1 if extra > 0 else 0)
        self.lang_button.set_tooltip_text("Recognizing: " + ", ".join(names) if names
                                          else "No recognition language")

    # =============================================================== welcome
    def _build_welcome(self):
        # Sky: who we are and what to do. Water: recent results.
        # HorizonLayout places both against the lake (see horizon.py).
        sky = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.flight_layer = FlightLayer()
        self.lake = LakeScene(dive_every=7.0)
        try:
            # The icon, drawn from its parts: tile, selection and bird.
            icon = StartIcon(str(ICON_DIR / "hicolor" / "scalable" / "apps" / "kingfisher.svg"), 80,
                             lambda: self.flight_layer, lambda: self.lake, lambda: [self.recent_box.get_child()])
        except (OSError, AttributeError, GLib.Error):
            icon = Gtk.Image(icon_name=self.app.icon_name, pixel_size=80, halign=Gtk.Align.CENTER)
        sky.append(icon)
        title = Gtk.Label(label="Read text from your screen", wrap=True,
                          justify=Gtk.Justification.CENTER, margin_top=16)
        title.add_css_class("welcome-title")
        sky.append(title)
        text = Gtk.Label(label="Draw a box around anything you can see. Nothing leaves this computer.",
                         wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=60, margin_top=6)
        text.add_css_class("welcome-text")
        sky.append(text)

        # The capture dock: the light sibling of the selection toolbar.
        dock = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER, margin_top=24)
        dock.add_css_class("capture-dock")
        area = Gtk.Button(action_name="app.capture-area", tooltip_text="Select an area (Ctrl+N)")
        area.update_property([Gtk.AccessibleProperty.LABEL], ["Select Area"])
        content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        content.append(SelectGlyph(area))
        content.append(Gtk.Label(label="Select Area"))
        area.set_child(content)
        for css in ("suggested-action", "kf-primary", "dock-primary"):
            area.add_css_class(css)
        dock.append(area)
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.add_css_class("dock-separator")
        dock.append(separator)
        # (label, action, tooltip, glyph, width below which only the icon shows)
        self._dock_labels = []
        for label, action, tooltip, glyph, compact_below in (
                ("Full Screen", "app.capture-screen", "Read the whole screen (Ctrl+Shift+N)", ScreenGlyph, 440),
                ("Open", "app.open-image", "Open an image file (Ctrl+O)", OpenGlyph, 560),
                ("Paste", "app.paste-image", "Read an image from the clipboard (Ctrl+Shift+V)", PasteGlyph, 560)):
            button = Gtk.Button(action_name=action, tooltip_text=tooltip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            box = Gtk.Box(spacing=7, halign=Gtk.Align.CENTER)
            box.append(glyph(button))
            name = Gtk.Label(label=label)
            box.append(name)
            self._dock_labels.append((name, compact_below))
            button.set_child(box)
            button.add_css_class("dock-button")
            dock.append(button)
        sky.append(dock)

        # "Press Ctrl + Print in any app to start", or a way to set a shortcut up.
        self.hint_keys = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, margin_top=14)
        self.hint_keys.add_css_class("welcome-hint")
        sky.append(self.hint_keys)
        self.hint_setup = Gtk.Button(label="Set Up a Keyboard Shortcut", action_name="app.preferences",
                                     halign=Gtk.Align.CENTER, margin_top=10)
        self.hint_setup.add_css_class("flat")
        self.hint_setup.add_css_class("text-link")
        sky.append(self.hint_setup)

        recent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head = Gtk.Box()
        label = Gtk.Label(label="Recent", xalign=0, hexpand=True)
        label.add_css_class("heading")
        label.add_css_class("recent-heading")
        head.append(label)
        show_all = Gtk.Button(label="Show All")
        show_all.add_css_class("flat")
        show_all.add_css_class("text-link")
        show_all.connect("clicked", lambda *_: self.show_history())
        head.append(show_all)
        recent.append(head)
        self.recent_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.recent_list.add_css_class("boxed-list")
        self.recent_list.add_css_class("glass-list")
        recent.append(self.recent_list)

        sky_clamp = Adw.Clamp(maximum_size=600, tightening_threshold=440, child=sky)
        self.recent_box = Adw.Clamp(maximum_size=600, tightening_threshold=440, child=recent)
        layout = HorizonLayout(sky_clamp, self.recent_box, self.recent_list, on_width=self._fit_dock)
        layout.add_css_class("welcome-column")
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=layout)
        page = Gtk.Overlay()
        page.set_child(self.lake)
        page.add_overlay(scroller)
        page.add_overlay(self.flight_layer)
        return page

    def _fit_dock(self, width):
        """In narrow windows the secondary dock buttons show only their icon
        (their tooltips and accessible names keep the full label)."""
        for name, compact_below in self._dock_labels:
            name.set_visible(width >= compact_below)

    def _populate_recent(self):
        while (child := self.recent_list.get_first_child()) is not None:
            self.recent_list.remove(child)
        entries = self.app.history.entries()[:RECENT_ON_WELCOME] if self.settings["history_enabled"] else []
        for entry in entries:
            row = Adw.ActionRow(title=GLib.markup_escape_text(entry.title[:120]),
                                subtitle=history_meta(entry), activatable=True)
            row.set_title_lines(1)
            row.add_prefix(Gtk.Image(icon_name=self._entry_icon(entry)))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, e=entry: self.show_history_entry(e))
            self.recent_list.append(row)
        self.recent_box.set_visible(bool(entries))

    def update_global_hint(self, binding_label):
        while (child := self.hint_keys.get_first_child()) is not None:
            self.hint_keys.remove(child)
        if binding_label:
            self.hint_keys.append(Gtk.Label(label="Press"))
            self.hint_keys.append(keycaps(binding_label))
            self.hint_keys.append(Gtk.Label(label="in any app to start"))
        elif IS_SNAP:
            # The sandbox cannot add a GNOME shortcut; the top bar icon takes
            # its place as the way into a capture from another app.
            self.hint_keys.append(Gtk.Label(label="Use the Kingfisher icon in the top bar "
                                                  "to start from any app"))
        self.hint_keys.set_visible(bool(binding_label) or IS_SNAP)
        self.hint_setup.set_visible(not binding_label and not IS_SNAP)

    # ============================================================ missing
    def _build_missing(self):
        page = Adw.StatusPage(icon_name="dialog-warning-symbolic", title="One Component Is Missing",
                              description="Kingfisher reads text with Tesseract, which runs on this "
                                          "computer. Install it once and everything works offline.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        install = InstallButton(self.app.software_installer, ["tesseract-ocr", "tesseract-ocr-eng"],
                                self.refresh_engine_state, self.toast, label="Install Tesseract")
        install.set_halign(Gtk.Align.CENTER)
        box.append(install)
        retry = Gtk.Button(label="Check Again", halign=Gtk.Align.CENTER)
        retry.add_css_class("flat")
        retry.connect("clicked", lambda *_: self.refresh_engine_state())
        box.append(retry)
        page.set_child(box)
        return page

    def refresh_engine_state(self):
        self._update_language_label()
        if not self.app.engine.available:
            self.stack.set_visible_child_name("missing")
        elif self.stack.get_visible_child_name() == "missing":
            self.stack.set_visible_child_name("welcome")
        self.lang_button.set_sensitive(self.app.engine.available)

    # =============================================================== busy
    def _build_busy(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, margin_bottom=60)
        frame = Gtk.Overlay(halign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
        frame.add_css_class("capture-thumb")
        self.busy_picture = Gtk.Picture(can_shrink=True, keep_aspect_ratio=False)
        frame.set_child(self.busy_picture)
        frame.add_overlay(ScanSweep())
        box.append(frame)
        title = Gtk.Label(label="Reading text…", margin_top=26)
        title.add_css_class("busy-title")
        box.append(title)
        self.busy_detail = Gtk.Label(margin_top=4)
        self.busy_detail.add_css_class("busy-detail")
        box.append(self.busy_detail)
        cancel = Gtk.Button(label="Cancel", halign=Gtk.Align.CENTER, margin_top=18)
        cancel.add_css_class("pill")
        cancel.add_css_class("kf-glass")
        cancel.connect("clicked", lambda *_: self.app.cancel_ocr())
        box.append(cancel)
        page = Gtk.Overlay()
        page.set_child(LakeScene(dive_every=1.6))
        page.add_overlay(box)
        return page

    def show_busy(self, image):
        # A picture's natural size is its texture's, so scale the pixels themselves.
        scale = min(BUSY_MAX[0] / image.width, BUSY_MAX[1] / image.height, 2.0)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        thumb = image.resize(size, Image.LANCZOS) if scale < 1 else image   # Pillow 9.0 on Ubuntu 22.04
        self.busy_picture.set_paintable(pil_to_texture(thumb))
        self.busy_picture.set_size_request(*size)
        installed = set(self.app.engine.languages())
        codes = [c for c in self.settings["languages"] if c in installed] or ["eng"]
        langs = ", ".join(language_name(c) for c in codes)
        self.busy_detail.set_label(f"{image.width} × {image.height} px · {langs}")
        note_activity()
        current = self.stack.get_visible_child_name()
        if current not in ("busy", "countdown"):
            self._before_busy = current
        self.stack.set_visible_child_name("busy")

    def hide_busy(self):
        """Leave the busy page after a cancel or an error."""
        self.busy_picture.set_paintable(None)
        if self.stack.get_visible_child_name() == "busy":
            # Back to exactly what was on screen before reading started.
            self.stack.set_visible_child_name(self._before_busy or "welcome")
            self.refresh_engine_state()

    # ========================================================== countdown
    def _build_countdown(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        ring = Gtk.Box(halign=Gtk.Align.CENTER)
        ring.add_css_class("count-ring")
        self.count_label = Gtk.Label(label="3", hexpand=True, vexpand=True)
        self.count_label.add_css_class("countdown")
        ring.append(self.count_label)
        box.append(ring)
        title = Gtk.Label(label="Get the screen ready", margin_top=24)
        title.add_css_class("busy-title")
        box.append(title)
        hint = Gtk.Label(label="This window steps aside before the capture.", margin_top=4)
        hint.add_css_class("busy-detail")
        box.append(hint)
        cancel = Gtk.Button(label="Cancel", halign=Gtk.Align.CENTER, margin_top=18)
        cancel.add_css_class("pill")
        cancel.connect("clicked", lambda *_: self.stop_countdown())
        box.append(cancel)
        return box

    def _set_count(self, value):
        self.count_label.set_label(str(value))
        play_once(self.count_label, "tick", 420)

    def start_countdown(self, seconds, on_done):
        self.stop_countdown(restore=False)
        current = self.stack.get_visible_child_name()
        self._previous_page = current if current not in ("busy", "countdown") else "welcome"
        state = {"left": seconds}
        self._set_count(seconds)
        self.stack.set_visible_child_name("countdown")

        def tick():
            state["left"] -= 1
            if state["left"] <= 0:
                self._countdown_id = 0
                self.stack.set_visible_child_name(self._previous_page)
                on_done()
                return GLib.SOURCE_REMOVE
            self._set_count(state["left"])
            return GLib.SOURCE_CONTINUE

        self._countdown_id = GLib.timeout_add_seconds(1, tick)

    def stop_countdown(self, restore=True):
        if self._countdown_id:
            GLib.source_remove(self._countdown_id)
            self._countdown_id = 0
            if restore:
                self.stack.set_visible_child_name(self._previous_page)

    # ============================================================== result
    def _build_result(self):
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True,
                               shrink_start_child=False, shrink_end_child=False,
                               resize_start_child=True, resize_end_child=True)
        self.paned.add_css_class("result-paned")

        # ---------- preview card
        self.preview_card = preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        preview.add_css_class("pane-card")
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("card-toolbar")
        title = Gtk.Label(label="Capture", xalign=0, hexpand=True)
        title.add_css_class("heading")
        bar.append(title)
        self.dims_label = Gtk.Label(valign=Gtk.Align.CENTER)
        self.dims_label.add_css_class("meta")
        bar.append(self.dims_label)
        self.boxes_toggle = Gtk.ToggleButton(icon_name="view-reveal-symbolic",
                                             tooltip_text="Highlight detected words",
                                             active=self.settings["show_boxes"])
        self.boxes_toggle.add_css_class("flat")
        self.boxes_toggle.connect("toggled", self._on_boxes_toggled)
        bar.append(self.boxes_toggle)
        preview.append(bar)

        frame = Gtk.Overlay(vexpand=True, hexpand=True)
        frame.add_css_class("preview-frame")
        self.picture = Gtk.Picture(can_shrink=True, keep_aspect_ratio=True,
                                   halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        frame.set_child(self.picture)
        self.boxes_area = Gtk.DrawingArea(can_target=False)
        self.boxes_area.set_draw_func(self._draw_boxes)
        frame.add_overlay(self.boxes_area)
        preview.append(frame)

        # ---------- text card
        self.text_card = text_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text_card.add_css_class("pane-card")
        bar = Gtk.Box(spacing=10)
        bar.add_css_class("card-toolbar")
        self.modes = modes = Gtk.Box(valign=Gtk.Align.CENTER)
        modes.add_css_class("segmented")
        self.mode_lines = Gtk.ToggleButton(label="As on Screen",
                                           tooltip_text="Keep line breaks, indentation and columns")
        self.mode_paras = Gtk.ToggleButton(label="Paragraphs", group=self.mode_lines,
                                           tooltip_text="Join wrapped lines into flowing paragraphs")
        (self.mode_paras if self.settings["text_format"] == "paragraphs" else self.mode_lines).set_active(True)
        self.mode_lines.connect("toggled", self._on_format_toggled)
        self.mode_paras.connect("toggled", self._on_format_toggled)
        modes.append(self.mode_lines)
        modes.append(self.mode_paras)
        bar.append(modes)
        # Shown instead of the layout switch when the result is only codes.
        self.kind_box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER, visible=False)
        self.kind_icon = Gtk.Image()
        self.kind_box.append(self.kind_icon)
        self.kind_label = Gtk.Label()
        self.kind_label.add_css_class("heading")
        self.kind_box.append(self.kind_label)
        bar.append(self.kind_box)
        bar.append(Gtk.Box(hexpand=True))
        self.conf_dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.conf_dot.add_css_class("conf-dot")
        bar.append(self.conf_dot)
        self.stats_label = Gtk.Label()
        self.stats_label.add_css_class("meta")
        bar.append(self.stats_label)
        text_card.append(bar)

        self.detected = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1,
                                    max_children_per_line=4,
                                    column_spacing=8, row_spacing=8, homogeneous=False,
                                    valign=Gtk.Align.START, visible=False)
        self.detected.add_css_class("detected-strip")
        text_card.append(self.detected)

        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.textview = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=20, right_margin=20,
                                     top_margin=16, bottom_margin=16, pixels_inside_wrap=3,
                                     pixels_below_lines=2)
        self.textview.add_css_class("result-text")
        self.buffer = self.textview.get_buffer()
        self.buffer.connect("changed", self._on_buffer_changed)
        scroller.set_child(self.textview)
        text_card.append(scroller)

        actions = Gtk.Box(spacing=8)
        actions.add_css_class("card-footer")
        self.counts_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.counts_label.add_css_class("meta")
        actions.append(self.counts_label)
        save = Gtk.Button(action_name="win.save", tooltip_text="Save as text file (Ctrl+S)")
        save.update_property([Gtk.AccessibleProperty.LABEL], ["Save as text file"])
        save_content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        save_content.append(SaveGlyph(save))
        save_content.append(Gtk.Label(label="Save"))
        save.set_child(save_content)
        save.add_css_class("pill")
        actions.append(save)
        # Save and Copy are a pair: the same width, whatever the labels.
        pair = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        pair.add_widget(save)
        self.copy_button = Gtk.Button(action_name="win.copy",
                                      tooltip_text="Copy the text, or just the selection (Ctrl+Shift+C)")
        self.copy_button.update_property([Gtk.AccessibleProperty.LABEL], ["Copy text"])
        pair.add_widget(self.copy_button)
        # Both labels live in one stack so the button keeps its width when it
        # changes to "Copied".
        self.copy_state = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                    transition_duration=150, hhomogeneous=True, vhomogeneous=True)
        copy_content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        copy_content.append(CopyGlyph(self.copy_button))
        copy_content.append(Gtk.Label(label="Copy"))
        self.copy_state.add_named(copy_content, "copy")
        copied_content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.copied_check = CheckGlyph()
        copied_content.append(self.copied_check)
        copied_content.append(Gtk.Label(label="Copied"))
        self.copy_state.add_named(copied_content, "copied")
        self.copy_button.set_child(self.copy_state)
        self.copy_button.add_css_class("pill")
        self.copy_button.add_css_class("suggested-action")
        self.copy_button.add_css_class("kf-primary")
        actions.append(self.copy_button)
        text_card.append(actions)

        self.paned.set_start_child(preview)
        self.paned.set_end_child(text_card)
        self.paned.set_position(int(self.settings["window_width"] * 0.42))
        wrapper = Gtk.Box()
        wrapper.add_css_class("result-page")
        self.paned.set_hexpand(True)
        wrapper.append(self.paned)
        return wrapper

    def _show_result_page(self):
        self.stack.set_visible_child_name("result")
        play_once(self.preview_card, "kf-enter", 300)
        play_once(self.text_card, "kf-enter", 370)
        self.text_card.add_css_class("later")

    def show_result(self, result, image):
        self.result = result
        self.image = image
        self._history_codes = None
        self._result_origin = "welcome"
        self.picture.set_paintable(pil_to_texture(image))
        self.preview_card.set_visible(True)
        self.dims_label.set_label(f"{image.width} × {image.height}")
        self.dims_label.set_visible(True)
        self.boxes_toggle.set_sensitive(True)
        if not result.words and not result.codes:
            self.stack.set_visible_child_name("empty")
            return
        self._history_entry = None
        self._set_formats_available(bool(result.words), [c.kind for c in result.codes])
        self._render_text()
        if result.words:
            conf = result.confidence
            level = "high" if conf >= 85 else "mid" if conf >= 65 else "low"
            self._set_stats(level, f"{conf:.0f}% confidence · {result.elapsed:.1f} s",
                            {"high": "Recognition confidence is high",
                             "mid": "Some words may be wrong",
                             "low": "Low confidence: check the text carefully"}[level])
        else:
            count = len(result.codes)
            self._set_stats(None, f"{count} code{'s' if count != 1 else ''} · {result.elapsed:.1f} s")
        self.boxes_area.queue_draw()
        self._show_result_page()
        self.textview.grab_focus()

    def _set_stats(self, level, text, tooltip=None):
        for c in ("high", "mid", "low"):
            self.conf_dot.remove_css_class(c)
        if level:
            self.conf_dot.add_css_class(level)
        self.conf_dot.set_visible(level is not None)
        self.stats_label.set_label(text)
        self.stats_label.set_tooltip_text(tooltip)

    def _render_text(self, fmt=None):
        fmt = fmt or ("paragraphs" if self.mode_paras.get_active() else "lines")
        self.textview.set_monospace(fmt == "lines" and looks_tabular(self.result.text("lines")))
        text = self.result.text(fmt) if self.result.words else "\n".join(c.data for c in self.result.codes)
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_start_iter())

    def _set_formats_available(self, available, code_kinds=()):
        # Offer the switch only when it can actually do something; a greyed-out
        # control promises a feature and then refuses it. Results that are only
        # QR codes or barcodes say what they are in that spot instead.
        self.modes.set_visible(available)
        self.kind_box.set_visible(not available and bool(code_kinds))
        if code_kinds and not available:
            kinds = list(code_kinds)
            self.kind_icon.set_from_icon_name(code_icon(kinds[0]))
            self.kind_label.set_label(kinds[0] if len(kinds) == 1 else f"{len(kinds)} codes")

    def _current_format(self):
        return "paragraphs" if self.mode_paras.get_active() else "lines"

    def _render_history_text(self):
        entry = self._history_entry
        fmt = self._current_format() if entry.has_formats else "lines"
        text = entry.text_in(fmt) if (entry.text or entry.paragraphs) else "\n".join(c[1] for c in entry.codes)
        self.textview.set_monospace(fmt == "lines" and entry.has_formats and looks_tabular(entry.text))
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_start_iter())

    def current_text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    def _on_buffer_changed(self, *_):
        text = self.current_text()
        words = len(text.split())
        lines = text.count("\n") + 1 if text else 0
        self.counts_label.set_label(f"{words} words · {len(text)} characters · {lines} lines")
        # Re-detect links as the user edits, without doing it on every keystroke.
        if self._detect_id:
            GLib.source_remove(self._detect_id)
        self._detect_id = GLib.timeout_add(250, self._refresh_detected)

    # ============================================================= detected
    def _refresh_detected(self):
        self._detect_id = 0
        items = []
        if self.result is not None:
            items += [("code", c.kind, c.data) for c in self.result.codes]
        elif self._history_codes:
            items += [("code", kind, data) for kind, data in self._history_codes]
        if self.settings["detect_links"]:
            items += [(e.kind, None, e) for e in find_entities(self.current_text())]
        # Rebuild only when the set changes, so chips don't flicker while typing.
        keys = [(kind, label, getattr(p, "text", p)) for kind, label, p in items]
        if keys == self._chip_keys:
            return GLib.SOURCE_REMOVE
        self._chip_keys = keys
        while (child := self.detected.get_first_child()) is not None:
            self.detected.remove(child)
        for item in items:
            self.detected.append(self._make_chip(*item))
        self.detected.set_visible(bool(items))
        return GLib.SOURCE_REMOVE

    def _make_chip(self, kind, label, payload):
        if kind == "code":
            value = payload
            entity = next(iter(find_entities(value)), None)
            uri = entity.uri if entity and entity.text == value else None
            icon = code_icon(label)
            tooltip = f"{label}: {value}"
            action_label = "Open" if uri else "Copy"
        else:
            value, uri = payload.text, payload.uri
            icon, verb = {"url": ("kingfisher-link-symbolic", "Open link"),
                          "email": ("mail-unread-symbolic", "Write email"),
                          "phone": ("call-start-symbolic", "Call")}[kind]
            if kind == "phone" and Gio.AppInfo.get_default_for_uri_scheme("tel") is None:
                uri = None
            tooltip = f"{verb if uri else 'Copy'}: {value}"
            action_label = verb if uri else "Copy"

        chip = Gtk.Box(spacing=1)
        chip.add_css_class("chip")
        play_once(chip, "kf-fade", 240)
        if kind == "code":
            chip.add_css_class("code")
        main = Gtk.Button(tooltip_text=tooltip)
        content = Gtk.Box(spacing=7)
        content.append(Gtk.Image(icon_name=icon))
        text = Gtk.Label(label=value, ellipsize=Pango.EllipsizeMode.END, max_width_chars=24)
        content.append(text)
        main.set_child(content)
        main.update_property([Gtk.AccessibleProperty.LABEL], [f"{action_label} {value}"])
        main.connect("clicked", lambda *_: self._open_uri(uri) if uri else
                     self.copy_to_clipboard(value, "Copied to clipboard"))
        chip.append(main)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy")
        copy.update_property([Gtk.AccessibleProperty.LABEL], [f"Copy {value}"])
        copy.connect("clicked", lambda *_: self.copy_to_clipboard(value, "Copied to clipboard"))
        chip.append(copy)
        return Gtk.FlowBoxChild(child=chip, focusable=False)

    def _open_uri(self, uri):
        try:
            Gtk.show_uri(self, uri, Gdk.CURRENT_TIME)
        except GLib.Error as err:
            self.toast(f"Couldn't open it: {err.message}")

    def _on_format_toggled(self, button):
        if not button.get_active():
            return
        fmt = "paragraphs" if button is self.mode_paras else "lines"
        self.settings["text_format"] = fmt
        if self.result and self.result.words:
            self._render_text(fmt)
            self._refresh_detected()
        elif self._history_entry is not None and self._history_entry.has_formats:
            self._render_history_text()
            self._refresh_detected()

    def _on_boxes_toggled(self, button):
        self.settings["show_boxes"] = button.get_active()
        self.boxes_area.queue_draw()

    def _draw_boxes(self, area, cr, width, height):
        if not (self.boxes_toggle.get_active() and self.result and self.image):
            return
        iw, ih = self.image.width, self.image.height
        scale = min(width / iw, height / ih)
        ox, oy = (width - iw * scale) / 2, (height - ih * scale) / 2
        for w in self.result.words:
            if w.conf >= 85:
                color = (0.11, 0.45, 0.91)    # kingfisher blue
            elif w.conf >= 65:
                color = (1.0, 0.62, 0.10)     # gold
            else:
                color = (0.88, 0.11, 0.14)
            x, y = ox + w.left * scale, oy + w.top * scale
            cr.rectangle(x - 1, y - 1, w.width * scale + 2, w.height * scale + 2)
            cr.set_source_rgba(*color, 0.14)
            cr.fill_preserve()
            cr.set_source_rgba(*color, 0.85)
            cr.set_line_width(1.2)
            cr.stroke()

    # =============================================================== empty
    def _build_empty(self):
        page = Adw.StatusPage(icon_name="edit-find-symbolic", title="No Text Found",
                              description="Try a tighter selection around the text, add its language "
                                          "from the header bar, or choose <b>Sparse text</b> in "
                                          "Preferences for scattered labels.")
        box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        again = Gtk.Button(label="Select Again", action_name="app.capture-area")
        again.add_css_class("pill")
        again.add_css_class("suggested-action")
        box.append(again)
        prefs = Gtk.Button(label="Preferences", action_name="app.preferences")
        prefs.add_css_class("pill")
        box.append(prefs)
        page.set_child(box)
        return page

    # ============================================================= actions
    def _install_actions(self):
        copy = Gio.SimpleAction.new("copy", None)
        copy.connect("activate", lambda *_: self.copy_result())
        self.add_action(copy)
        save = Gio.SimpleAction.new("save", None)
        save.connect("activate", lambda *_: self.save_result())
        self.add_action(save)
        for name, callback in (("home", self.go_home), ("history", self.show_history)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
        undo = Gio.SimpleAction.new("undo-history", GLib.VariantType.new("s"))
        undo.connect("activate", lambda _a, token: self._undo_history_change(token.get_string()))
        self.add_action(undo)
        # Esc leaves the reading and countdown pages (same as their Cancel).
        escape = Gtk.EventControllerKey()
        escape.connect("key-pressed", self._on_escape)
        self.add_controller(escape)
        # Any input keeps the ambient scene moving (it rests when you step away).
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: note_activity())
        self.add_controller(motion)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda *_: note_activity() or False)
        self.add_controller(keys)
        back = Gtk.GestureClick(button=8)
        back.connect("pressed", lambda *_: self.go_home())
        self.add_controller(back)

    def _on_escape(self, _ctrl, keyval, _code, _state):
        if keyval != Gdk.KEY_Escape:
            return False
        page = self.stack.get_visible_child_name()
        if page == "busy":
            self.app.cancel_ocr()
            return True
        if page == "countdown":
            self.stop_countdown()
            return True
        return False

    def go_home(self):
        """Go back one level: result → where it was opened from; history → start."""
        page = self.stack.get_visible_child_name()
        if page in ("result", "empty"):
            if self._result_origin == "history":
                self._populate_history()
                self.stack.set_visible_child_name("history")
            else:
                self.stack.set_visible_child_name("welcome")
            self._result_origin = "welcome"
        elif page == "history":
            self.stack.set_visible_child_name("welcome")
        self.refresh_engine_state()   # back to "missing" if Tesseract went away

    def copy_result(self, silent=False):
        if self.stack.get_visible_child_name() != "result":
            return
        bounds = self.buffer.get_selection_bounds()
        text = self.buffer.get_text(*bounds, False) if bounds else self.current_text()
        if text.strip():
            self.copy_to_clipboard(text, None if silent else
                                   ("Selection copied" if bounds else "Text copied to clipboard"))
            self._flash_copied()

    def _flash_copied(self):
        self.copy_state.set_visible_child_name("copied")
        self.copied_check.play()
        self.copy_button.add_css_class("copied")
        if self._copied_id:
            GLib.source_remove(self._copied_id)

        def restore():
            self._copied_id = 0
            self.copy_state.set_visible_child_name("copy")
            self.copy_button.remove_css_class("copied")
            return GLib.SOURCE_REMOVE
        self._copied_id = GLib.timeout_add(1600, restore)

    def copy_to_clipboard(self, text, toast=None):
        set_clipboard_text(self.get_clipboard(), text)
        if toast:
            self.toast(toast)

    def save_result(self):
        if self.stack.get_visible_child_name() != "result":
            return
        dialog = Gtk.FileChooserNative(title="Save Text", transient_for=self, modal=True,
                                       action=Gtk.FileChooserAction.SAVE, accept_label="_Save")
        dialog.set_current_name("extracted-text.txt")
        text_filter = Gtk.FileFilter(name="Plain text")
        text_filter.add_mime_type("text/plain")
        dialog.add_filter(text_filter)
        text = self.current_text()

        def on_response(dlg, response):
            if response == Gtk.ResponseType.ACCEPT:
                gfile = dlg.get_file()
                try:
                    gfile.replace_contents(text.encode("utf-8") + b"\n", None, False,
                                           Gio.FileCreateFlags.REPLACE_DESTINATION, None)
                    self.toast(f"Saved to {gfile.get_basename()}")
                except GLib.Error as err:
                    self.toast(f"Couldn't save: {err.message}")
            dlg.destroy()

        dialog.connect("response", on_response)
        self._save_dialog = dialog
        dialog.show()

    def toast(self, message, timeout=2):
        toast = Adw.Toast(title=GLib.markup_escape_text(message), timeout=timeout)
        self.toasts.add_toast(toast)
        fit_toasts(self.toasts)

    # ============================================================ plumbing
    def _build_drop_zone(self):
        self.drop_zone = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                      transition_duration=150, can_target=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        box.add_css_class("drop-zone")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True,
                        valign=Gtk.Align.CENTER)
        inner.append(Gtk.Image(icon_name="kingfisher-extract-symbolic", pixel_size=48))
        inner.append(Gtk.Label(label="Drop an image to read its text"))
        box.append(inner)
        self.drop_zone.set_child(box)
        return self.drop_zone

    def _install_drop_target(self):
        target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        target.connect("enter", self._on_drag_enter)
        target.connect("leave", lambda *_: self.drop_zone.set_reveal_child(False))
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_drag_enter(self, *_):
        self.drop_zone.set_reveal_child(True)
        return Gdk.DragAction.COPY

    def _on_drop(self, _target, value, _x, _y):
        self.drop_zone.set_reveal_child(False)
        if isinstance(value, Gio.File) and value.get_path():
            self.app.ocr_file(value.get_path())
            return True
        return False

    def _on_resize(self, *_):
        width = self.get_default_size()[0]
        narrow = 0 < width < NARROW_WIDTH
        orientation = Gtk.Orientation.VERTICAL if narrow else Gtk.Orientation.HORIZONTAL
        if self.paned.get_orientation() != orientation:
            self.paned.set_orientation(orientation)
            self.paned.set_position(int((self.get_default_size()[1] - 60) * 0.4) if narrow else int(width * 0.42))

    def _on_close(self, *_):
        self.stop_countdown(restore=False)   # a closed window must not capture later
        for source in (self._detect_id, self._copied_id):
            if source:
                GLib.source_remove(source)
        self._detect_id = self._copied_id = 0
        width, height = self.get_default_size()
        if width > 0 and height > 0 and not self.is_maximized():
            self.settings["window_width"] = width
            self.settings["window_height"] = height
        self.app.history.disconnect(self._on_history_changed)
        self.settings.disconnect(self._on_setting_changed)
        return False

    def _on_setting_changed(self, key, _value):
        if key == "detect_links" and self.stack.get_visible_child_name() == "result":
            self._chip_keys = None
            self._refresh_detected()
        if key == "languages":
            self._update_language_label()
        if key == "history_enabled":
            self._populate_recent()
        if key == "text_format":
            target = self.mode_paras if _value == "paragraphs" else self.mode_lines
            if not target.get_active():
                target.set_active(True)
        rerun = key in ("languages", "layout", "enhance", "model")
        if rerun and self.image is not None and self.stack.get_visible_child_name() in ("result", "empty"):
            self.app.run_ocr(self.image)
