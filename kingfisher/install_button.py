# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""An "Install" button for optional system components (one click, no terminal).

Installs the distribution's packages through PackageKit; the system asks for
the administrator password. Where PackageKit is missing it offers to copy the
equivalent ``apt`` command instead.
"""

from gi.repository import Gtk

from .clipboard import set_clipboard_text
from .effects import spinner
from .languages import friendly_error, human_size


class InstallButton(Gtk.Stack):
    def __init__(self, installer_factory, packages, on_installed, notify, label="Install", on_size=None):
        """``installer_factory`` is called only when the button is first shown,
        so hidden buttons never talk to PackageKit."""
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE, valign=Gtk.Align.CENTER,
                         hhomogeneous=False)
        self.installer = None
        self._factory = installer_factory
        self.packages = list(packages)
        self.on_installed = on_installed
        self.notify = notify          # notify(message) shows a toast
        self.on_size = on_size        # on_size("12 KB") once the download size is known
        self._found = None
        self._looked_up = False
        self._install_when_found = False

        self.button = Gtk.Button(label=label)
        self.button.add_css_class("suggested-action")
        self.button.connect("clicked", lambda *_: self._install())
        self.add_named(self.button, "install")

        busy = Gtk.Box(spacing=8)
        busy.append(spinner())
        # Fixed width so nothing shifts as the stage text changes.
        self.stage = Gtk.Label(label="Preparing…", width_chars=20, xalign=1)
        self.stage.add_css_class("dim-label")
        busy.append(self.stage)
        self.add_named(busy, "busy")

        command = "sudo apt install " + " ".join(self.packages)
        copy = Gtk.Button(label="Copy Command", tooltip_text=command)
        copy.connect("clicked", lambda *_: self._copy(command))
        self.add_named(copy, "command")

        self.set_visible_child_name("install")
        self.connect("map", self._on_first_map)

    def _on_first_map(self, *_):
        if self._factory is None:
            return
        self.installer, self._factory = self._factory(), None
        if self.installer is None:
            self.set_visible_child_name("command")
        else:
            self.installer.find(self.packages, self._on_found)

    def size_text(self):
        if not self._found:
            return ""
        return human_size(sum(p.size for p in self._found if not p.installed))

    def _on_found(self, packs, _error):
        self._looked_up = True
        if packs is None:
            self.set_visible_child_name("command")
            return
        self._found = packs
        if self._install_when_found:
            self._install()
        size = self.size_text()
        if size:
            self.button.set_tooltip_text(f"Download about {size}")
            if self.on_size:
                self.on_size(size)

    def _install(self):
        if not self._looked_up:
            # Clicked before the lookup finished: install as soon as it does.
            self._install_when_found = True
            self.set_visible_child_name("busy")
            return
        self._install_when_found = False
        if not self._found:
            self.set_visible_child_name("install")
            self.notify("The package isn't available from your software sources")
            return
        missing = [p for p in self._found if not p.installed]
        if not missing:
            self.set_visible_child_name("install")
            self.on_installed()
            return
        self.set_visible_child_name("busy")

        def progress(text):
            self.stage.set_label(text)

        def done(error):
            # Never leave the spinner running, whatever happened.
            self.set_visible_child_name("install")
            if error:
                self.notify(f"Not installed. {friendly_error(error)}")
                return
            for pack in missing:
                pack.installed = True
            self.on_installed()

        self.installer.install(missing, progress, done)

    def _copy(self, text):
        set_clipboard_text(self.get_clipboard(), text)
        self.notify("Command copied. Run it in a terminal, then reopen this window")
