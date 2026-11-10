# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Start Kingfisher (in the background, with its top-bar icon) when you log in."""

import os
import shutil
import sys
from pathlib import Path

from gi.repository import GLib

from . import APP_ID, IS_SNAP

AUTOSTART_FILE = Path(GLib.get_user_config_dir()) / "autostart" / f"{APP_ID}.desktop"


def is_enabled():
    return AUTOSTART_FILE.is_file()


def _desktop_quote(arg):
    """Quote one argument for a .desktop Exec key (the spec allows only
    double quotes, with \\, ", ` and $ escaped)."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def exec_value(args):
    """The value of an Exec key for ``args``. Two layers of escaping apply:
    the Exec quoting above, then the desktop file's own string escaping
    (backslashes doubled), and "%" is written as "%%" so it is not a field code."""
    line = " ".join(_desktop_quote(a) for a in args)
    return line.replace("\\", "\\\\").replace("%", "%%")


def _command():
    if IS_SNAP:
        # Inside the snap this file lands in ~/snap/kingfisher/current/.config/autostart.
        # snapd reads it at login through the app's "autostart" key and runs the
        # snap's own command with these arguments; the program name is ignored.
        return exec_value(["kingfisher", "--background"])
    launcher = os.environ.get("KINGFISHER_LAUNCHER") or shutil.which("kingfisher")
    if launcher:
        args = [launcher, "--background"]
    else:
        root = Path(__file__).resolve().parent.parent
        args = ["env", f"PYTHONPATH={root}", sys.executable, "-m", "kingfisher", "--background"]
    return exec_value(args)


def enable():
    AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART_FILE.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Kingfisher\n"
        "Comment=Screen text extractor (top-bar icon)\n"
        f"Icon={APP_ID}\n"
        f"Exec={_command()}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "NoDisplay=true\n",
        encoding="utf-8")


def disable():
    AUTOSTART_FILE.unlink(missing_ok=True)
