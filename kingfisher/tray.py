# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Top-bar (system tray) icon via the StatusNotifierItem D-Bus protocol.

Ubuntu shows these through its AppIndicator extension. The protocol is plain
D-Bus, so it works from a GTK 4 process; the menu is served with libdbusmenu.
"""

import os

import gi
from gi.repository import Gio, GLib

try:
    gi.require_version("Dbusmenu", "0.4")
    from gi.repository import Dbusmenu
except (ValueError, ImportError):
    Dbusmenu = None

WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
# Must stay below /StatusNotifierItem: the snap's unity7 interface only lets the
# tray host read a menu served at /StatusNotifierItem/menu.
MENU_PATH = "/StatusNotifierItem/menu"

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""


def supported():
    """Whether this system can show the icon at all (the menu library exists)."""
    return Dbusmenu is not None


def available():
    """True when a tray host (e.g. Ubuntu's AppIndicator extension) is running."""
    if Dbusmenu is None:
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(WATCHER_BUS, WATCHER_PATH, "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (WATCHER_BUS, "IsStatusNotifierHostRegistered")),
                              GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 1000, None)
        return bool(reply.unpack()[0])
    except GLib.Error:
        return False


def watch_host(callback):
    """Call ``callback`` whenever a tray host appears (e.g. after the screen is
    unlocked, or when the AppIndicator extension finishes loading at login).
    Returns a watch id for ``unwatch_host``."""
    return Gio.bus_watch_name(Gio.BusType.SESSION, WATCHER_BUS, Gio.BusNameWatcherFlags.NONE,
                              lambda *_: callback(), None)


def unwatch_host(watch_id):
    Gio.bus_unwatch_name(watch_id)


class TrayIcon:
    """``actions`` is a list of (label, callback) tuples; ``None`` adds a separator.
    The first action also runs on a left click where the host supports it.

    ``icon_path`` is an absolute path to the icon file. Ubuntu's AppIndicator
    extension looks theme icons up as "<name>-panel", so a file path is the
    portable choice."""

    def __init__(self, icon_path, title, actions):
        self.icon_path = icon_path
        self.title = title
        self.actions = actions
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.service = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._reg_id = 0
        self._name_id = 0
        self._watch_id = 0
        self._menu = None

    # ------------------------------------------------------------ lifecycle
    def show(self):
        if self._reg_id:
            return
        node = Gio.DBusNodeInfo.new_for_xml(ITEM_XML)
        self._reg_id = self.bus.register_object(
            ITEM_PATH, node.interfaces[0], self._on_call, self._on_get, None)
        self._menu = self._build_menu()
        self._name_id = Gio.bus_own_name_on_connection(
            self.bus, self.service, Gio.BusNameOwnerFlags.NONE, None, None)
        # Re-register whenever the watcher (re)appears, e.g. after a shell restart.
        self._watch_id = Gio.bus_watch_name_on_connection(
            self.bus, WATCHER_BUS, Gio.BusNameWatcherFlags.NONE,
            lambda *_: self._register(), None)

    def hide(self):
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
            self._name_id = 0
        if self._reg_id:
            self.bus.unregister_object(self._reg_id)
            self._reg_id = 0
        self._menu = None

    @property
    def visible(self):
        return bool(self._reg_id)

    def _register(self):
        self.bus.call(WATCHER_BUS, WATCHER_PATH, WATCHER_BUS, "RegisterStatusNotifierItem",
                      GLib.Variant("(s)", (self.service,)), None, Gio.DBusCallFlags.NONE,
                      -1, None, None)

    # ----------------------------------------------------------------- menu
    def _build_menu(self):
        server = Dbusmenu.Server.new(MENU_PATH)
        root = Dbusmenu.Menuitem.new()
        for entry in self.actions:
            item = Dbusmenu.Menuitem.new()
            if entry is None:
                item.property_set(Dbusmenu.MENUITEM_PROP_TYPE, Dbusmenu.CLIENT_TYPES_SEPARATOR)
            else:
                label, callback = entry
                item.property_set(Dbusmenu.MENUITEM_PROP_LABEL, label)
                item.connect("item-activated", lambda *_a, cb=callback: GLib.idle_add(lambda: cb() and False,
                                                                                    priority=GLib.PRIORITY_DEFAULT))
            root.child_append(item)
        server.set_root(root)
        return server

    # -------------------------------------------------------------- D-Bus
    def _on_get(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "kingfisher"),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", self.icon_path),
            "IconThemePath": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], self.title, "Extract text from the screen")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(prop)

    def _on_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        if method in ("Activate", "SecondaryActivate") and self.actions and self.actions[0]:
            GLib.idle_add(lambda: self.actions[0][1]() and False, priority=GLib.PRIORITY_DEFAULT)
        invocation.return_value(None)
