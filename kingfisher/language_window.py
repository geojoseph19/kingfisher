# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Languages window: turn languages on or off, and add new ones in a click."""

from gi.repository import Adw, Gdk, GLib, Gtk

from . import IS_SNAP
from .clipboard import set_clipboard_text
from .effects import spinner
from .languages import (
    POPULAR,
    LanguagePack,
    friendly_error,
    human_size,
    install_command,
    suggested_codes,
)
from .ocr import language_name
from .toasts import fit_toasts


class LanguagesWindow(Adw.Window):
    def __init__(self, app, parent, installer):
        super().__init__(application=app, transient_for=parent, modal=True, title="Languages",
                         default_width=560, default_height=680)
        self.app = app
        self.settings = app.settings
        self.installer = installer
        self.packs = {}            # code -> LanguagePack
        self.busy_code = None
        self.rows = {}             # code -> row, for search filtering
        self.groups = []

        self.toasts = Adw.ToastOverlay()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        root.append(header)
        self.search = Gtk.SearchEntry(placeholder_text="Search languages", hexpand=True)
        self.search.connect("search-changed", lambda *_: self._apply_filter())
        search_box = Adw.Clamp(maximum_size=520, child=self.search, margin_top=12, margin_bottom=6,
                               margin_start=12, margin_end=12)
        root.append(search_box)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        loading.append(spinner(width_request=28, height_request=28))
        note = Gtk.Label(label="Finding languages…")
        note.add_css_class("dim-label")
        loading.append(note)
        self.stack.add_named(loading, "loading")

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                margin_top=6, margin_bottom=24, margin_start=12, margin_end=12)
        self.stack.add_named(Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            child=Adw.Clamp(maximum_size=520, child=self.list_box)), "list")
        root.append(self.stack)
        self.toasts.set_child(root)
        self.set_content(self.toasts)

        self.search.set_key_capture_widget(self)
        self.search.connect("stop-search", lambda *_: self._on_escape())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.settings.connect(self._on_setting)
        self.connect("close-request", lambda *_: self.settings.disconnect(self._on_setting) or False)
        self.refresh()

    # ------------------------------------------------------------- data
    def refresh(self):
        installed = self.app.engine.languages()
        if self.installer is None:
            self.packs = {c: LanguagePack(c, "", True) for c in installed}
            self._build()
            return
        if not self.packs:
            self.stack.set_visible_child_name("loading")

        def on_packs(packs, error):
            if packs is None:
                self.installer = None   # fall back to commands
                self.packs = {c: LanguagePack(c, "", True) for c in installed}
                if error:
                    self._toast(f"Couldn't list languages: {error}", timeout=4)
            else:
                self.packs = {p.code: p for p in packs}
                # Languages present on disk but not from a package (e.g. user models).
                for code in installed:
                    if code not in self.packs:
                        self.packs[code] = LanguagePack(code, "", True)
                    else:
                        self.packs[code].installed = True
            self._build()

        self.installer.list_packs(on_packs)

    # ------------------------------------------------------------ layout
    def _build(self):
        while (child := self.list_box.get_first_child()) is not None:
            self.list_box.remove(child)
        self.rows, self.groups = {}, []
        selected = set(self.settings["languages"])
        installed = sorted((p for p in self.packs.values() if p.installed),
                           key=lambda p: (p.code != "eng", language_name(p.code).lower()))
        missing = {p.code: p for p in self.packs.values() if not p.installed}

        group = self._group("Installed",
                            "Turned-on languages are used for every capture. Each one adds a "
                            "little time, so keep only those you read.")
        for pack in installed:
            group.add(self._installed_row(pack, pack.code in selected))

        if self.installer is None:
            self.list_box.append(self._fallback())
            self.stack.set_visible_child_name("list")
            return

        suggested = [c for c in suggested_codes() if c in missing]
        if suggested:
            group = self._group("Suggested for You", "Languages from your system settings.")
            for code in suggested:
                group.add(self._missing_row(missing.pop(code), primary=True))
        popular = [c for c in POPULAR if c in missing]
        if popular:
            group = self._group("Popular")
            for code in popular:
                group.add(self._missing_row(missing.pop(code)))
        if missing:
            group = self._group("All Languages",
                                "Official Ubuntu language packs, about 1–2 MB each. "
                                "Your system asks for your password to add or remove one.")
            for pack in sorted(missing.values(), key=lambda p: language_name(p.code).lower()):
                group.add(self._missing_row(pack))
        self._apply_filter()
        self.stack.set_visible_child_name("list")

    def _group(self, title, description=None):
        group = Adw.PreferencesGroup(title=title, description=description)
        self.list_box.append(group)
        self.groups.append(group)
        return group

    def _remember_row(self, code, row):
        self.rows.setdefault(code, []).append(row)

    def _installed_row(self, pack, active):
        row = Adw.ActionRow(title=language_name(pack.code))
        row.set_subtitle("Turned on" if active else "Installed")
        # The remove button comes first and the switch last, so every switch
        # lines up at the right edge. Rows that can't be removed keep an
        # invisible stand-in of the same size to hold the column.
        removable = self.installer is not None and pack.package_id and pack.code != "eng"
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Remove from this computer")
        remove.add_css_class("flat")
        if removable:
            remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {language_name(pack.code)}"])
            remove.connect("clicked", lambda *_: self._remove(pack, row))
            remove.set_sensitive(self.busy_code is None)
            row.remove_button = remove
        else:
            # Invisible spacer that only holds the column: not clickable,
            # not focusable and hidden from screen readers.
            remove.set_opacity(0)
            remove.set_can_target(False)
            remove.set_can_focus(False)
            remove.set_tooltip_text(None)
            remove.update_state([Gtk.AccessibleState.HIDDEN], [True])
        row.add_suffix(remove)
        switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER,
                            tooltip_text="Use for recognition")
        switch.connect("notify::active", self._on_switch, pack.code, row)
        row.add_suffix(switch)
        row.set_activatable_widget(switch)
        self._remember_row(pack.code, row)
        return row

    def _missing_row(self, pack, primary=False):
        row = Adw.ActionRow(title=language_name(pack.code), subtitle=human_size(pack.size))
        add = Gtk.Button(label="Add", valign=Gtk.Align.CENTER, sensitive=self.busy_code is None)
        if primary:
            add.add_css_class("suggested-action")
        add.update_property([Gtk.AccessibleProperty.LABEL], [f"Add {language_name(pack.code)}"])
        add.connect("clicked", lambda *_: self._install(pack, row, add))
        row.add_suffix(add)
        row.add_button = add
        self._remember_row(pack.code, row)
        return row

    def _fallback(self):
        if IS_SNAP:
            return Adw.PreferencesGroup(
                title="More Languages",
                description="The Snap Store version includes the languages listed above. "
                            "More will come with app updates.")
        box = Adw.PreferencesGroup(
            title="Add Languages",
            description="Software installation isn't available from here. Add a language with "
                        "a terminal command, then reopen this window.")
        row = Adw.ActionRow(title="Example: Hindi and Spanish", subtitle=install_command(["hin", "spa"]))
        row.add_css_class("monospace")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Copy")
        copy.add_css_class("flat")
        copy.connect("clicked", lambda *_: self._copy(install_command(["hin", "spa"])))
        row.add_suffix(copy)
        box.add(row)
        return box

    def _apply_filter(self):
        query = self.search.get_text().strip().lower()
        for code, rows in self.rows.items():
            match = not query or _matches(query, code)
            for row in rows:
                row.set_visible(match)
        for group in self.groups:
            # A group with no visible rows hides itself.
            listbox = _group_list(group)
            visible = listbox is None or any(child.get_visible() for child in _children(listbox))
            group.set_visible(visible)

    # ------------------------------------------------------------ actions
    def _on_switch(self, switch, _pspec, code, row):
        if not self.settings.set_language(code, switch.get_active()):
            switch.set_active(True)
            self._toast("At least one language stays on", timeout=2)
            return
        row.set_subtitle("Turned on" if switch.get_active() else "Installed")

    def _install(self, pack, row, button):
        if self.busy_code is not None:
            return
        name = language_name(pack.code)
        self._set_busy(pack.code)
        status = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        status.append(spinner())
        # Fixed width so the row doesn't shift as the stage text changes.
        stage = Gtk.Label(label="Preparing…", width_chars=20, xalign=1)
        stage.add_css_class("dim-label")
        status.append(stage)
        row.remove(button)
        row.add_suffix(status)

        def progress(text):
            stage.set_label(text)

        def done(error):
            self._set_busy(None)
            if error:
                row.remove(status)
                row.add_suffix(button)
                self._toast(f"{name} wasn't added. {friendly_error(error)}", timeout=5)
                return
            pack.installed = True
            self.settings.set_language(pack.code, True)
            self._toast(f"{name} added and turned on", timeout=3)
            self.app.window_engine_changed()
            self._build()   # the pack list is already up to date; no need to ask again

        self.installer.install([pack], progress, done)

    def _remove(self, pack, row):
        if self.busy_code is not None:
            return
        name = language_name(pack.code)
        self._set_busy(pack.code)
        row.set_subtitle("Removing…")

        def done(error):
            self._set_busy(None)
            if error:
                row.set_subtitle("Installed")
                self._toast(f"{name} wasn't removed. {friendly_error(error)}", timeout=5)
                return
            pack.installed = False
            if not self.settings.set_language(pack.code, False):
                self.settings["languages"] = ["eng"]   # it was the only one turned on
            self._toast(f"{name} removed", timeout=3)
            self.app.window_engine_changed()
            self._build()

        self.installer.remove([pack], lambda _p: None, done)

    def _set_busy(self, code):
        """One change at a time: every Add and Remove waits while one runs."""
        self.busy_code = code
        for rows in self.rows.values():
            for row in rows:
                for button in (getattr(row, "add_button", None), getattr(row, "remove_button", None)):
                    if button is not None:
                        button.set_sensitive(code is None)

    def _toast(self, title, timeout=3):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(title), timeout=timeout))
        fit_toasts(self.toasts)

    def _copy(self, text):
        clipboard = self.get_clipboard()
        set_clipboard_text(clipboard, text)
        self._toast("Command copied", timeout=2)

    def _on_setting(self, key, _value):
        if key == "languages" and self.busy_code is None:
            selected = set(self.settings["languages"])
            for code, rows in self.rows.items():
                for row in rows:
                    for child in _suffixes(row):
                        if isinstance(child, Gtk.Switch) and child.get_active() != (code in selected):
                            child.set_active(code in selected)

    def _on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self._on_escape()
            return True
        return False

    def _on_escape(self):
        if self.search.get_text():
            self.search.set_text("")
        else:
            self.close()


def _matches(query, code):
    """Match the start of any word of the name ("hin" finds Hindi, not Chinese),
    or the language code."""
    words = language_name(code).lower().replace("(", " ").replace(")", " ").replace(",", " ").split()
    return code.startswith(query) or any(w.startswith(query) for w in words) \
        or language_name(code).lower().startswith(query)


def _children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _group_list(group):
    """The Gtk.ListBox inside an Adw.PreferencesGroup (no public accessor in 1.1)."""
    stack = [group]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.ListBox):
            return widget
        stack.extend(_children(widget))
    return None


def _suffixes(row):
    stack = [row]
    while stack:
        widget = stack.pop()
        yield widget
        stack.extend(_children(widget))
