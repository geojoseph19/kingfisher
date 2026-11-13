# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Kingfisher — offline screen text extraction for the GNOME desktop."""

import os
from pathlib import Path

# Reverse-DNS application ID, matching the GitHub repository. It must never
# change after a release: settings, the autostart entry, the desktop file and
# the AppStream ID derive from it. Every installer and template reads it here.
APP_ID = "io.github.geojoseph19.Kingfisher"
APP_NAME = "Kingfisher"
VERSION = "1.0.3"
WEBSITE = "https://github.com/geojoseph19/kingfisher"
ISSUES_URL = WEBSITE + "/issues"
CONTRIBUTING_URL = WEBSITE + "/blob/main/CONTRIBUTING.md"
CONTRIBUTORS_URL = WEBSITE + "/graphs/contributors"
SPONSOR_URL = ""            # a sponsor page, once one exists; the About window shows it only when set
DEVELOPER = "Geo Joseph"
DEVELOPER_URL = "https://github.com/geojoseph19"
COPYRIGHT = "© 2026 Geo Joseph"

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
ICON_DIR = DATA_DIR / "icons"
APP_ICON = "kingfisher"  # icon name shipped in ICON_DIR, installed system-wide as APP_ID

# True inside the strictly confined snap. The sandbox keeps GNOME's shortcut
# settings and the system's package manager out of reach, so the snap ships a
# fixed set of recognition languages. SNAP alone is not enough: classic snaps
# such as editors and IDEs leak it into the terminals they open.
IS_SNAP = os.environ.get("SNAP_NAME") == "kingfisher" and bool(os.environ.get("SNAP"))


def real_home():
    """The user's home folder. Inside the snap, HOME is a private folder under
    ~/snap/kingfisher, which is not what people know as their home."""
    return Path(os.environ.get("SNAP_REAL_HOME") or Path.home()) if IS_SNAP else Path.home()


def display_path(path, home=None):
    """A path as people write it, with the home folder shortened to "~".

    The UI never shows a full home path: it would put the user's account name
    into every screenshot and bug report."""
    path = Path(path)
    home = Path(home) if home is not None else real_home()
    try:
        return "~/" + str(path.relative_to(home)) if path != home else "~"
    except ValueError:
        return str(path)
