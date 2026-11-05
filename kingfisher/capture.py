# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Full-screen capture on GNOME (Wayland and X11).

Preferred: ``gnome-screenshot``. GNOME Shell only lets a few trusted tools take
screenshots silently, and gnome-screenshot is one of them, so capture is fast
and needs no confirmation. It writes to a private file in $XDG_RUNTIME_DIR
that is deleted as soon as it is loaded.

Fallback: the XDG desktop portal. On Ubuntu 22.04 it asks "Share this
screenshot?" on every capture, which is slow and steals focus.
"""

import itertools
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from gi.repository import Gio, GLib
from PIL import Image

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.Screenshot"
REQUEST_IFACE = "org.freedesktop.portal.Request"

_tokens = itertools.count(1)
SCREENSHOT_TIMEOUT = 10   # seconds before gnome-screenshot is given up on
# File names the GNOME portal uses when it saves into Pictures by mistake.
PORTAL_NAME = re.compile(r"^Screenshot(-\d+)?\.png$")


class CaptureError(Exception):
    pass


class CaptureFailure(str):
    """Error message passed to callbacks; ``retryable`` is False when the
    user explicitly cancelled."""

    def __new__(cls, message, retryable=True):
        obj = super().__new__(cls, message)
        obj.retryable = retryable
        return obj


def silent_capture_available():
    return shutil.which("gnome-screenshot") is not None


def capture_screen(callback):
    """Take a screenshot of every monitor.

    ``callback(image, error)`` is invoked on the main loop with either a
    ``PIL.Image`` or a ``CaptureFailure`` message.
    """
    if silent_capture_available():
        def on_silent(image, error):
            if image is not None:
                callback(image, None)
            else:
                _capture_portal(callback)
        _capture_gnome_screenshot(on_silent)
    else:
        _capture_portal(callback)


def _private_dir():
    """A folder only this user can read, for the moment a capture is on disk.
    Leftovers from a crash are removed the next time it is used."""
    folder = Path(GLib.get_user_runtime_dir()) / "kingfisher"
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    for stale in folder.glob("capture-*.png"):
        try:
            if time.time() - stale.stat().st_mtime > 60:
                stale.unlink()
        except OSError:
            pass
    return folder


def _capture_gnome_screenshot(callback):
    try:
        path = str(_private_dir() / f"capture-{os.getpid()}-{next(_tokens)}.png")
        proc = Gio.Subprocess.new(
            ["gnome-screenshot", "-f", path],
            Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
    except (GLib.Error, OSError):
        GLib.idle_add(callback, None, CaptureFailure("gnome-screenshot could not start"))
        return

    # Never wait forever: a stuck gnome-screenshot falls back to the portal.
    watchdog = GLib.timeout_add_seconds(SCREENSHOT_TIMEOUT, lambda: proc.force_exit() and False)

    def on_exit(p, result):
        GLib.source_remove(watchdog)
        try:
            p.wait_check_finish(result)
            with Image.open(path) as img:
                image = img.convert("RGB")
                image.load()
            callback(image, None)
        except (GLib.Error, OSError):
            callback(None, CaptureFailure("gnome-screenshot failed"))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    proc.wait_check_async(None, on_exit)


def _capture_portal(callback):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as err:
        GLib.idle_add(callback, None, CaptureFailure(f"Cannot reach the session bus: {err.message}"))
        return

    started = time.time()
    before = _portal_files()
    token = f"kingfisher{os.getpid()}_{next(_tokens)}"
    sender = bus.get_unique_name()[1:].replace(".", "_")
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    state = {"sub": 0, "timeout": 0, "done": False}

    def unsubscribe():
        if state["sub"]:
            bus.signal_unsubscribe(state["sub"])
            state["sub"] = 0
        return GLib.SOURCE_REMOVE

    def finish(image, error):
        if error:
            _sweep(before, started, time.time())
        if state["timeout"]:
            GLib.source_remove(state["timeout"])
            state["timeout"] = 0
        if state["done"]:
            return
        state["done"] = True
        callback(image, error)

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        unsubscribe()
        code, results = params.unpack()
        uri = results.get("uri", "")
        if state["done"]:
            # We already gave up waiting; never leave the file behind.
            _discard(uri)
            if not uri:
                _sweep(before, started, time.time())
            return
        if code != 0:
            _discard(uri)
            # 1 = the user cancelled the "Share screenshot" dialog.
            finish(None, CaptureFailure("Screen capture was cancelled.", retryable=code != 1))
            return
        try:
            finish(_load_and_remove(uri), None)
        except CaptureError as err:
            finish(None, CaptureFailure(str(err)))

    def on_timeout():
        state["timeout"] = 0
        finish(None, CaptureFailure("The screenshot service did not respond.", retryable=False))
        # Keep listening a little longer so a late file still gets removed.
        GLib.timeout_add_seconds(60, unsubscribe)
        return GLib.SOURCE_REMOVE

    # Subscribe before calling so a fast response is never missed.
    state["sub"] = bus.signal_subscribe(
        PORTAL_BUS, REQUEST_IFACE, "Response", request_path, None,
        Gio.DBusSignalFlags.NONE,
        on_response,
    )
    state["timeout"] = GLib.timeout_add_seconds(20, on_timeout)

    options = {
        "handle_token": GLib.Variant("s", token),
        "interactive": GLib.Variant("b", False),
    }

    def on_called(conn, result):
        try:
            conn.call_finish(result)
        except GLib.Error as err:
            unsubscribe()
            finish(None, CaptureFailure(f"Screenshot portal unavailable: {err.message}", retryable=False))

    bus.call(
        PORTAL_BUS, PORTAL_PATH, PORTAL_IFACE, "Screenshot",
        GLib.Variant("(sa{sv})", ("", options)),
        GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None, on_called,
    )


def _pictures_dir():
    path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    return Path(path) if path else Path.home() / "Pictures"


def _portal_files():
    try:
        return {p.name for p in _pictures_dir().glob("*.png")}
    except OSError:
        return set()


def _sweep(before, started, ended):
    """GNOME 42's portal sometimes saves the image into Pictures even though
    the request failed or was cancelled, without saying where. Remove that
    one file, and nothing else: it must use the portal's own name pattern
    ("Screenshot.png", "Screenshot-1.png", ...), be new since the request
    started, and have been written while it ran. GNOME's own screenshots use
    dated names in Pictures/Screenshots, so they never match."""
    try:
        candidates = []
        for path in _pictures_dir().iterdir():
            if not PORTAL_NAME.match(path.name) or path.name in before or not path.is_file():
                continue
            mtime = path.stat().st_mtime
            if started - 1 <= mtime <= ended + 2:
                candidates.append((mtime, path))
        if candidates:
            max(candidates)[1].unlink()
    except OSError:
        pass


def _discard(uri):
    parsed = urlparse(uri or "")
    if parsed.scheme == "file":
        try:
            os.remove(unquote(parsed.path))
        except OSError:
            pass


def _load_and_remove(uri):
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise CaptureError("The screenshot service returned an unexpected location.")
    path = unquote(parsed.path)
    try:
        with Image.open(path) as img:
            image = img.convert("RGB")
            image.load()
    except OSError as err:
        raise CaptureError(f"Could not read the screenshot: {err}") from err
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return image
