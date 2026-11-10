# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""System-wide keyboard shortcut via GNOME's custom keybindings."""

import os
import shlex
import shutil
import sys
from pathlib import Path

from gi.repository import Gdk, Gio, Gtk

from . import IS_SNAP

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
BINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/kingfisher/"
DEFAULT_BINDING = "<Shift><Super>t"


def supported():
    if IS_SNAP:
        return False   # the sandbox cannot change the host's GNOME settings
    source = Gio.SettingsSchemaSource.get_default()
    return bool(source and source.lookup(MEDIA_KEYS, True) and source.lookup(CUSTOM_SCHEMA, True))


def unavailable_reason():
    if IS_SNAP:
        return ("The Snap Store version can't change system settings. To add a shortcut, open "
                "Settings › Keyboard › View and Customize Shortcuts › Custom Shortcuts "
                "and use the command "
                "“kingfisher --capture”.")
    return "GNOME custom shortcuts are not available on this desktop."


def launch_command():
    launcher = os.environ.get("KINGFISHER_LAUNCHER") or shutil.which("kingfisher")
    if launcher:
        return f"{shlex.quote(launcher)} --capture"
    package_root = Path(__file__).resolve().parent.parent
    return f"env PYTHONPATH={shlex.quote(str(package_root))} {shlex.quote(sys.executable)} -m kingfisher --capture"


def _media():
    return Gio.Settings.new(MEDIA_KEYS)


def _custom():
    return Gio.Settings.new_with_path(CUSTOM_SCHEMA, BINDING_PATH)


def is_enabled():
    return supported() and BINDING_PATH in _media().get_strv("custom-keybindings")


def binding():
    return _custom().get_string("binding") if is_enabled() else None


def accel_label(accel):
    """Human-readable label for an accelerator string, e.g. "Ctrl+Print"."""
    ok, key, mods = Gtk.accelerator_parse(accel or "")
    return Gtk.accelerator_get_label(key, mods) if ok and key else accel


def binding_label():
    accel = binding()
    return accel_label(accel) if accel else None


def enable(accel=DEFAULT_BINDING):
    if not supported():
        raise RuntimeError(unavailable_reason())
    custom = _custom()
    custom.set_string("name", "Kingfisher — Extract Text from Screen")
    custom.set_string("command", launch_command())
    custom.set_string("binding", accel)
    media = _media()
    paths = media.get_strv("custom-keybindings")
    if BINDING_PATH not in paths:
        media.set_strv("custom-keybindings", paths + [BINDING_PATH])
    Gio.Settings.sync()


def disable():
    if not supported():
        return
    media = _media()
    paths = [p for p in media.get_strv("custom-keybindings") if p != BINDING_PATH]
    media.set_strv("custom-keybindings", paths)
    custom = _custom()
    for key in ("name", "command", "binding"):
        custom.reset(key)
    Gio.Settings.sync()


# --------------------------------------------------------------- conflicts
CONFLICT_SCHEMAS = {
    "org.gnome.settings-daemon.plugins.media-keys": "Media keys",
    "org.gnome.shell.keybindings": "GNOME Shell",
    "org.gnome.desktop.wm.keybindings": "Windows",
    "org.gnome.mutter.keybindings": "Windows",
    "org.gnome.mutter.wayland.keybindings": "System",
}


def _parse(accel):
    ok, key, mods = Gtk.accelerator_parse(accel or "")
    if not ok or not key:
        return None
    return Gdk.keyval_to_lower(key), int(mods)


def find_conflict(accel):
    """Return (settings, key, description) of an existing GNOME binding using
    ``accel``, or None."""
    target = _parse(accel)
    if target is None:
        return None
    source = Gio.SettingsSchemaSource.get_default()
    for schema_id, area in CONFLICT_SCHEMAS.items():
        schema = source.lookup(schema_id, True) if source else None
        if schema is None:
            continue
        settings = Gio.Settings.new(schema_id)
        for key in schema.list_keys():
            vtype = schema.get_key(key).get_value_type().dup_string()
            if vtype == "as":
                values = settings.get_strv(key)
            elif vtype == "s" and key not in ("custom-keybindings",):
                values = [settings.get_string(key)]
            else:
                continue
            if any(_parse(v) == target for v in values):
                label = key.replace("-", " ").capitalize()
                return settings, key, f"“{label}” ({area})"
    if supported():
        for path in _media().get_strv("custom-keybindings"):
            if path == BINDING_PATH:
                continue
            custom = Gio.Settings.new_with_path(CUSTOM_SCHEMA, path)
            if _parse(custom.get_string("binding")) == target:
                return custom, "binding", f"“{custom.get_string('name')}” (custom shortcut)"
    return None


def release_conflict(conflict, accel):
    """Remove ``accel`` from another GNOME binding so Kingfisher can use it."""
    settings, key, _desc = conflict
    target = _parse(accel)
    if settings.get_value(key).get_type_string() == "as":
        settings.set_strv(key, [v for v in settings.get_strv(key) if _parse(v) != target])
    else:
        settings.set_string(key, "")
    Gio.Settings.sync()
