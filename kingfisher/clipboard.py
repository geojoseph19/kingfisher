# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Putting text on the clipboard."""

from gi.repository import Gdk, GObject


def set_clipboard_text(clipboard, text):
    """An explicit string value, so every app pastes it as plain text."""
    value = GObject.Value(GObject.TYPE_STRING, text)
    clipboard.set_content(Gdk.ContentProvider.new_for_value(value))
