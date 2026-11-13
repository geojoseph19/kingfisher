# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Full-screen capture on GNOME (Wayland and X11).

Preferred: ``gnome-screenshot``. GNOME Shell only lets a few trusted tools take
screenshots silently, and gnome-screenshot is one of them, so capture is fast
and needs no confirmation. It writes to a private file in $XDG_RUNTIME_DIR
that is deleted as soon as it is loaded.

Fallback: the XDG desktop portal. On Ubuntu 22.04 it asks "Share this
screenshot?" on every capture, which is slow and steals focus.

Inside the snap, gnome-screenshot is out of reach and the route depends on
the session (see ``sandbox_route``): a direct grab on X11; on Wayland the
Screenshot portal where it asks only once (xdg-desktop-portal 1.15 and
later), otherwise the ScreenCast portal with a remembered selection
(``screencast.py``). Either way the user is asked once, not on every capture.

The Screenshot portal's question is not shown everywhere, though. GNOME Shell
only shows it for the focused application and compares the portal's name for
the app ("snap.kingfisher") with its own ("kingfisher_kingfisher"), which
never match for a snap, so on Ubuntu 24.04 the portal refuses at once without
asking anything. A refusal therefore moves the capture to the ScreenCast
route, which has a dialog of its own (``_capture_sandboxed``).
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

from . import IS_SNAP, real_home, screencast

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.Screenshot"
REQUEST_IFACE = "org.freedesktop.portal.Request"

_tokens = itertools.count(1)
SCREENSHOT_TIMEOUT = 10   # seconds before gnome-screenshot is given up on
# A capture that came back this fast cannot have waited for a dialog.
SILENT_SECONDS = 1.0
RETAKE_DELAY_MS = 400     # let GNOME's permission dialog finish closing
# Seconds to wait for the portal. Inside the snap the first request shows a
# permission dialog that people take time to read.
PORTAL_TIMEOUT = 90 if IS_SNAP else 20
ROUTES = ("x11", "screenshot", "screencast")
# File names the GNOME portal uses when it saves into Pictures by mistake.
PORTAL_NAME = re.compile(r"^Screenshot(-\d+)?\.png$")


REFUSED_MESSAGE = "This desktop did not allow Kingfisher to read the screen."


class CaptureError(Exception):
    pass


class CaptureFailure(str):
    """Error message passed to callbacks.

    ``retryable``: False when trying again at once cannot help (the user
    cancelled, or the desktop refused).
    ``refused``: the Screenshot portal turned the request down.
    ``needs_access``: nothing was captured because screen access has to be
    asked for first; start the capture again and it will be."""

    def __new__(cls, message, retryable=True, refused=False, needs_access=False):
        obj = super().__new__(cls, message)
        obj.retryable = retryable
        obj.refused = refused
        obj.needs_access = needs_access
        return obj


def silent_capture_available():
    # gnome-screenshot is not shipped inside the snap, and the sandbox cannot
    # run the host's binary.
    if IS_SNAP:
        return False
    return shutil.which("gnome-screenshot") is not None


_last_silent = False
_remembers_screenshots = None
_refused = False          # the Screenshot portal refused during this run


def last_capture_was_silent():
    """Whether the last capture finished without any dialog on screen, so the
    selection can appear at once instead of waiting for a dialog to close."""
    return _last_silent


def capture_screen(callback):
    """Take a screenshot of every monitor.

    ``callback(image, error)`` is invoked on the main loop with either a
    ``PIL.Image`` or a ``CaptureFailure`` message.
    """
    started = time.monotonic()

    def done(image, error, silent=None):
        global _last_silent
        _last_silent = silent if silent is not None else time.monotonic() - started < SILENT_SECONDS
        callback(image, error)

    if silent_capture_available():
        def on_silent(image, error):
            if image is not None:
                done(image, None, silent=True)   # gnome-screenshot never shows a dialog
            else:
                _capture_portal(done)
        _capture_gnome_screenshot(on_silent)
    elif IS_SNAP:
        _capture_sandboxed(done)
    else:
        _capture_portal(done)


# ------------------------------------------------------------------ sandbox
def x11_session():
    return bool(os.environ.get("DISPLAY")) and not os.environ.get("WAYLAND_DISPLAY")


def portal_remembers_screenshots():
    """Whether the Screenshot portal asks once and then remembers the answer.

    xdg-desktop-portal 1.15 added that, but changed no interface version a
    sandboxed app can read. The Settings portal reached version 2 in 1.17.1,
    the first marker there is. Systems with 1.15 or 1.16 are taken for older
    ones and use the ScreenCast route, which also asks only once."""
    global _remembers_screenshots
    if _remembers_screenshots is None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(PORTAL_BUS, PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
                                  GLib.Variant("(ss)", ("org.freedesktop.portal.Settings", "version")),
                                  GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
            version = reply.unpack()[0]
            _remembers_screenshots = isinstance(version, int) and version >= 2
        except GLib.Error:
            _remembers_screenshots = False
    return _remembers_screenshots


def choose_route(forced, x11, remembers_screenshots, screencast_works, screenshots_refused=False):
    """The capture route for the sandbox; kept pure so it can be tested."""
    if forced in ROUTES:
        return forced
    if x11:
        return "x11"
    if not screencast_works:
        return "screenshot"
    if remembers_screenshots and not screenshots_refused:
        return "screenshot"
    return "screencast"


def sandbox_route():
    """KINGFISHER_CAPTURE=x11|screenshot|screencast overrides the choice, for
    support and testing."""
    forced = os.environ.get("KINGFISHER_CAPTURE")
    if forced in ROUTES:
        return forced
    if x11_session():
        return "x11"
    remembers = portal_remembers_screenshots()
    refused = screenshots_refused()
    # GStreamer is only loaded when the ScreenCast route is a candidate.
    screencast_works = (refused or not remembers) and screencast.available()
    return choose_route(None, False, remembers, screencast_works, refused)


def needs_access_note():
    """Whether the next capture will show GNOME's screen-sharing dialog for
    the first time, which needs a word of explanation beforehand."""
    return IS_SNAP and not silent_capture_available() and sandbox_route() == "screencast" \
        and not screencast.has_token()


def access_state():
    """For Preferences, inside the snap: "open" when no permission is needed
    (X11), "granted" when captures run without a question, "ask" when the
    next capture asks first."""
    route = sandbox_route()
    if route == "x11":
        return "open"
    if route == "screencast":
        return "granted" if screencast.has_token() else "ask"
    return "granted" if screenshot_allowed_marker().exists() else "ask"


def reset_access():
    """Forget everything remembered about screen access, so that the next
    capture starts from the beginning and asks again."""
    global _refused
    _refused = False
    screencast.forget()
    for marker in (screenshot_allowed_marker(), screenshot_refused_marker()):
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass


def _capture_sandboxed(callback):
    route = sandbox_route()
    if route == "x11":
        image = _grab_x11()
        if image is not None:
            def deliver():
                callback(image, None, silent=True)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(deliver)
            return
        route = "screencast" if screencast.available() else "screenshot"
    if route == "screencast":
        _capture_screencast(callback)
        return

    def on_screenshot(image, error):
        if image is not None or not getattr(error, "refused", False):
            callback(image, error)
            return
        # Refused: without a question (see the module docstring), or because
        # "Deny" was chosen. The two cannot be told apart reliably (timing
        # says little on a slow machine), and the way on is the same.
        _remember_refusal()
        if sandbox_route() != "screencast":
            callback(None, error)   # there is no other route
        elif screencast.has_token():
            _capture_screencast(callback)
        else:
            # The ScreenCast route asks properly, after a word of
            # explanation that only the application can give.
            callback(None, CaptureFailure(str(error), retryable=False, needs_access=True))
    _capture_portal_once_asked(on_screenshot)


def _capture_screencast(callback):
    def on_done(image, error, cancelled):
        if image is not None:
            callback(image, None)
        elif cancelled:
            callback(None, CaptureFailure(error, retryable=False))
        elif screenshots_refused():
            # The Screenshot portal has already said no; its answer would
            # only hide what went wrong here.
            callback(None, CaptureFailure(error or "Could not read the screen.", retryable=False))
        else:
            # Anything else (no PipeWire, a refused session): ask the
            # Screenshot portal instead.
            _capture_portal(callback)
    screencast.capture(on_done)


def screenshot_allowed_marker():
    """Present once a Screenshot portal capture has succeeded."""
    return screencast.state_dir() / "screenshot-allowed"


def screenshot_refused_marker():
    """Present once the Screenshot portal has refused, so that later captures
    go straight to the ScreenCast route."""
    return screencast.state_dir() / "screenshot-refused"


def screenshots_refused():
    return _refused or screenshot_refused_marker().exists()


def _remember_refusal():
    global _refused
    _refused = True   # holds for this run even if the marker cannot be written
    marker = screenshot_refused_marker()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass


def _capture_portal_once_asked(callback):
    """The Screenshot portal, retaken once after the permission dialog.

    The portal takes the picture right after "Allow", while GNOME's dialog is
    still fading out, so the first picture can show it. The answer is
    remembered by then, so a second capture is silent and clean. Only the
    first successful capture is retaken: a slow machine must not take two
    pictures every time."""
    marker = screenshot_allowed_marker()
    first = not marker.exists()
    started = time.monotonic()

    def on_result(image, error):
        if image is None:
            # Denied, revoked or canceled: the dialog may show again next time.
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
            callback(image, error)
            return
        if first:
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            except OSError:
                pass
            if time.monotonic() - started >= SILENT_SECONDS and portal_remembers_screenshots():
                GLib.timeout_add(RETAKE_DELAY_MS, lambda: _capture_portal(callback) and False)
                return
        callback(image, error)
    _capture_portal(on_result)


def _grab_x11():
    """The whole X11 screen, read directly. Any X client may do this on X11,
    and the snap's x11 interface allows it."""
    try:
        from PIL import ImageGrab
        image = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY"))
        return image.convert("RGB")
    except (ImportError, OSError, ValueError):
        return None


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
            if code == 1:
                # The user cancelled the "Share screenshot" dialog.
                finish(None, CaptureFailure("Screen capture was cancelled.", retryable=False))
            elif IS_SNAP and portal_remembers_screenshots():
                # "Deny" was chosen, now or earlier, or the desktop refused
                # without asking. Trying again gives the same answer.
                finish(None, CaptureFailure(REFUSED_MESSAGE, retryable=False, refused=True))
            else:
                finish(None, CaptureFailure("Screen capture was cancelled."))
            return
        try:
            image, removed = _load_and_remove(uri)
        except CaptureError as err:
            finish(None, CaptureFailure(str(err)))
            return
        if not removed:
            # Portals before 1.18 hand sandboxed apps a read-only copy through
            # the document portal, so deleting it leaves the original in
            # Pictures. Remove that file, which this request just created.
            _sweep(before, started, time.time())
        finish(image, None)

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
    state["timeout"] = GLib.timeout_add_seconds(PORTAL_TIMEOUT, on_timeout)

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
    # Inside the snap the desktop launcher rewrites user-dirs.dirs with the
    # host's real (possibly translated) folders, so GLib's answer is right
    # there too. Only the fallback needs the real home.
    path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    return Path(path) if path else real_home() / "Pictures"


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
    """The screenshot at ``uri`` and whether its file could be deleted."""
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
            removed = True
        except OSError:
            removed = False
    return image, removed
