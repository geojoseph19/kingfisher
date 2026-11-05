# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys

# GNOME's Wayland session will not raise a freshly opened full-screen window
# from a background process (it shows "… is ready" instead), which breaks the
# selection overlay when started from a keyboard shortcut. X11 windows via
# XWayland are raised normally. Set KINGFISHER_BACKEND=wayland to opt out.
if (os.environ.get("XDG_SESSION_TYPE") == "wayland" and os.environ.get("DISPLAY")
        and os.environ.get("KINGFISHER_BACKEND", "x11") == "x11"):
    os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_foreign("cairo")


def main():
    from gi.repository import GLib

    from . import APP_ID
    from .application import KingfisherApplication
    GLib.set_prgname(APP_ID)
    return KingfisherApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
