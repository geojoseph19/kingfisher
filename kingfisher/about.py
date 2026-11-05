# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""The About window.

libadwaita 1.1 has no AdwAboutWindow, so this builds the same layout: the
app at the top, links, ways to support the project, and Credits and Legal as
sub-pages."""

from gi.repository import Adw, Gdk, GLib, Gtk

from . import (APP_NAME, CONTRIBUTING_URL, CONTRIBUTORS_URL, COPYRIGHT, DEVELOPER, DEVELOPER_URL, ISSUES_URL,
               SPONSOR_URL, VERSION, WEBSITE)

LICENSE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
TRADEMARKS_URL = WEBSITE + "/blob/main/TRADEMARKS.md"

# (name, what it does here, license, link)
BUILT_WITH = (
    ("Tesseract", "Text recognition", "Apache-2.0", "https://github.com/tesseract-ocr/tesseract"),
    ("ZBar", "QR code and barcode reading", "LGPL-2.1-or-later", "https://github.com/mchehab/zbar"),
    ("GTK", "User interface toolkit", "LGPL-2.1-or-later", "https://www.gtk.org"),
    ("libadwaita", "GNOME design components", "LGPL-2.1-or-later", "https://gnome.pages.gitlab.gnome.org/libadwaita/"),
    ("Pillow", "Image processing", "MIT-CMU", "https://python-pillow.org"),
)


class AboutWindow(Adw.Window):
    def __init__(self, parent, icon_name):
        super().__init__(transient_for=parent, modal=True, default_width=360, default_height=640,
                         title=f"About {APP_NAME}")
        self.add_css_class("about")
        self._leaflet = Adw.Leaflet(can_unfold=False, can_navigate_back=True)
        self._main = self._main_page(icon_name)
        self._leaflet.append(self._main)
        self._credits = self._leaflet.append(self._sub_page("Credits", self._credits_content()))
        self._legal = self._leaflet.append(self._sub_page("Legal", self._legal_content()))
        self.set_content(self._leaflet)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ---------------------------------------------------------------- pages
    def _main_page(self, icon_name):
        header = Adw.HeaderBar(show_end_title_buttons=True)
        header.add_css_class("flat")
        header.set_title_widget(Gtk.Label())                     # no title: the page says it all

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, halign=Gtk.Align.CENTER)
        top.append(Gtk.Image(icon_name=icon_name, pixel_size=128))
        name = Gtk.Label(label=APP_NAME, margin_top=12)
        name.add_css_class("title-1")
        top.append(name)
        dev = Gtk.Label(label=DEVELOPER, margin_top=6)
        dev.add_css_class("about-developer")
        top.append(dev)
        version = Gtk.Label(label=VERSION, halign=Gtk.Align.CENTER, margin_top=12)
        version.add_css_class("about-version")
        top.append(version)
        summary = Gtk.Label(label="Copy text from anything on your screen.", wrap=True,
                            justify=Gtk.Justification.CENTER, margin_top=16)
        summary.add_css_class("about-summary")
        top.append(summary)

        links = self._group()
        links.add(self._link_row("Website", WEBSITE))
        links.add(self._link_row("Report an Issue", ISSUES_URL))

        support = self._group("Support Kingfisher",
                              "Kingfisher is free and open source. If it saves you time, here is how you can help.")
        support.add(self._link_row("Star on GitHub", WEBSITE, "Helps more people find Kingfisher",
                                   "starred-symbolic"))
        support.add(self._link_row("Contribute", CONTRIBUTING_URL, "Report bugs, suggest ideas or improve the code",
                                   "system-users-symbolic"))
        if SPONSOR_URL:
            support.add(self._link_row("Sponsor", SPONSOR_URL, "Fund ongoing development",
                                       "emblem-favorite-symbolic"))

        more = self._group()
        more.add(self._page_row("Credits", lambda: self._open_page(self._credits)))
        more.add(self._page_row("Legal", lambda: self._open_page(self._legal)))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=6, margin_bottom=24,
                       margin_start=12, margin_end=12)
        for part in (top, links, support, more):
            body.append(part)
        return self._page(header, body)

    def _sub_page(self, title, content):
        header = Adw.HeaderBar(show_end_title_buttons=True)
        header.add_css_class("flat")
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        back.connect("clicked", lambda *_: self._leaflet.navigate(Adw.NavigationDirection.BACK))
        header.pack_start(back)
        header.set_title_widget(Adw.WindowTitle(title=title))
        return self._page(header, content)

    def _credits_content(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=12, margin_bottom=24,
                      margin_start=12, margin_end=12)
        created = self._group("Created by")
        created.add(self._link_row(DEVELOPER, DEVELOPER_URL))
        box.append(created)
        people = self._group("Contributors")
        people.add(self._link_row("Made better by its contributors", CONTRIBUTORS_URL,
                                  "Every bug report, idea and fix helps. Yours is welcome too."))
        box.append(people)
        built = self._group("Built with")
        for name, role, license_id, url in BUILT_WITH:
            built.add(self._link_row(name, url, f"{role} · {license_id}"))
        box.append(built)
        return box

    def _legal_content(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=24,
                      margin_start=18, margin_end=18)
        copyright_label = Gtk.Label(label=COPYRIGHT, xalign=0, wrap=True)
        copyright_label.add_css_class("heading")
        box.append(copyright_label)
        text = Gtk.Label(xalign=0, wrap=True, label=(
            f"{APP_NAME} is free software: you can redistribute it and/or modify it under the terms of the "
            "GNU General Public License as published by the Free Software Foundation, either version 3 of the "
            "License, or (at your option) any later version.\n\n"
            "It is distributed in the hope that it will be useful, but without any warranty; without even the "
            "implied warranty of merchantability or fitness for a particular purpose."))
        text.add_css_class("about-legal")
        box.append(text)
        group = self._group()
        group.add(self._link_row("GNU General Public License, version 3", LICENSE_URL))
        box.append(group)
        marks = Gtk.Label(xalign=0, wrap=True, margin_top=12, label=(
            f"The {APP_NAME} name and logo are trademarks. The license does not grant rights to use them."))
        marks.add_css_class("about-legal")
        box.append(marks)
        policy = self._group()
        policy.add(self._link_row("Trademark Policy", TRADEMARKS_URL))
        box.append(policy)
        return box

    def _open_page(self, page):
        """Show a sub-page. Only the open one takes part in navigation, so going
        back (button, Esc or swipe) always returns to the main page, never to the
        sibling page that happens to sit next to it."""
        for other in (self._credits, self._legal):
            other.set_navigatable(other is page)
        self._leaflet.set_visible_child(page.get_child())

    # ---------------------------------------------------------------- parts
    @staticmethod
    def _page(header, content):
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=Adw.Clamp(maximum_size=420, child=content))
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(header)
        page.append(scroller)
        return page

    @staticmethod
    def _group(title=None, description=None):
        group = Adw.PreferencesGroup()
        if title:
            group.set_title(GLib.markup_escape_text(title))
        if description:
            group.set_description(GLib.markup_escape_text(description))
        return group

    def _link_row(self, title, url, subtitle=None, icon=None):
        row = Adw.ActionRow(title=GLib.markup_escape_text(title), activatable=True)
        if subtitle:
            row.set_subtitle(GLib.markup_escape_text(subtitle))
        if icon:
            row.add_prefix(Gtk.Image(icon_name=icon))
        row.add_suffix(Gtk.Image(icon_name="kingfisher-external-link-symbolic"))
        row.set_tooltip_text(url)
        row.connect("activated", lambda *_: self._open(url))
        return row

    @staticmethod
    def _page_row(title, on_activate):
        row = Adw.ActionRow(title=title, activatable=True)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: on_activate())
        return row

    def _open(self, url):
        Gtk.show_uri(self, url, Gdk.CURRENT_TIME)

    def _on_key(self, _ctrl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            if self._leaflet.get_visible_child() is not self._main:
                self._leaflet.navigate(Adw.NavigationDirection.BACK)
            else:
                self.close()
            return True
        return False
