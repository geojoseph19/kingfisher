# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Toasts that fit their text."""

from gi.repository import Gtk


def fit_toasts(container):
    """libadwaita 1.1 gives every toast a fixed-width slot, which leaves a wide
    gap between short messages and the close button. Centred, a toast takes
    its natural width inside that slot instead."""
    pending = [container]
    while pending:
        widget = pending.pop()
        if widget.get_css_name() == "toast":
            widget.set_halign(Gtk.Align.CENTER)
            continue
        child = widget.get_first_child()
        while child is not None:
            pending.append(child)
            child = child.get_next_sibling()
