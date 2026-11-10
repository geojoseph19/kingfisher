# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent user preferences stored as JSON under XDG_CONFIG_HOME."""

import json
import os
from pathlib import Path

from gi.repository import GLib

CONFIG_DIR = Path(GLib.get_user_config_dir()) / "kingfisher"
CONFIG_FILE = CONFIG_DIR / "settings.json"

DEFAULTS = {
    # Recognition
    "languages": ["eng"],
    "layout": "auto",            # auto | block | line | sparse
    "enhance": True,             # upscale + contrast normalisation for small screen text
    "model": "fast",             # fast (system tessdata) | best (tessdata_best, ~3x slower)
    "read_codes": True,          # QR codes and barcodes
    "detect_links": True,        # links, emails and phone numbers
    "history_enabled": True,     # keep the text of recent results
    "text_format": "lines",      # lines (as on screen) | paragraphs (reflowed)
    # Capture
    "confirm_on_release": False,  # extract as soon as the mouse is released
    "auto_copy": True,           # copy result to clipboard automatically
    "quick_mode": "window",      # window | copy  (behaviour of the global shortcut)
    "show_magnifier": False,
    "show_tray": True,           # icon in the top bar; keeps Kingfisher running in the background
    "ui_version": 0,
    "shortcut_key": "<Shift><Super>t",
    "last_selection": [],         # restored next time, like GNOME's screenshot tool
    "screen_access_explained": False,  # snap: the note before GNOME's screen-sharing dialog was shown
    # Window
    "color_scheme": "default",   # default (follow the system) | light | dark
    "window_width": 1080,
    "window_height": 700,
    "show_boxes": False,
}


def _valid(key, value):
    """Whether a stored value can be used; anything else falls back to the
    default, so a damaged or hand-edited file never stops the app."""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if not isinstance(value, type(default)):
        return False
    if key == "languages":
        return bool(value) and all(isinstance(v, str) and v for v in value)
    if key == "last_selection":
        return value == [] or (len(value) == 4 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))
    return True


def _read_file():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {k: v for k, v in stored.items() if k in DEFAULTS and _valid(k, v)}


class Settings:
    def __init__(self):
        self._data = dict(DEFAULTS)
        self._data.update(_read_file())
        self._dirty = set()       # keys this process changed since its last save
        self._listeners = []
        self._migrate()

    def _migrate(self):
        # v2: the cursor-following magnifier felt distracting; start with it off.
        if self._data["ui_version"] < 2:
            self._data["show_magnifier"] = False
            self._data["ui_version"] = 2
            self._dirty |= {"show_magnifier", "ui_version"}
            self._save()

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._dirty.add(key)
        self._save()
        for callback in list(self._listeners):
            callback(key, value)

    def set_language(self, code, enabled):
        """Add or remove a recognition language. At least one must stay
        selected; returns False when the change was refused."""
        langs = [lang for lang in self._data["languages"] if lang != code]
        if enabled:
            langs.append(code)
        if not langs:
            return False
        self["languages"] = langs
        return True

    def connect(self, callback):
        self._listeners.append(callback)

    def disconnect(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _save(self):
        """Write only what this process changed on top of what is on disk, so
        a change made meanwhile by another Kingfisher process (for example
        ``kingfisher --enable-shortcut``) is kept, not overwritten."""
        on_disk = _read_file()
        for key, value in on_disk.items():
            if key not in self._dirty:
                self._data[key] = value
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, CONFIG_FILE)
            self._dirty.clear()
        except OSError:
            pass
