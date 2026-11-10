# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Screen capture through the ScreenCast portal, for the snap on Wayland.

Older desktop portals (xdg-desktop-portal before 1.15, as on Ubuntu 22.04)
ask before every screenshot a sandboxed app takes. Their screen-sharing
dialog, though, can remember the user's choice ("Remember this selection"):
the portal then hands out a restore token, and a later session started with
that token needs no dialog. A capture opens a session, reads one frame from
each shared monitor over PipeWire and closes the session again, so once the
user has agreed it takes a fraction of a second.

The token is a capability: it lets this app, and only this app (the portal
checks the app ID), see the screen without asking. It is stored readable by
the user alone, is replaced after every use as the portal requires, and
"Forget" in Preferences deletes it.
"""

import os
import tempfile
import threading
import time
from pathlib import Path

from gi.repository import Gio, GLib
from PIL import Image

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

SOURCE_MONITOR = 1
CURSOR_HIDDEN = 1
PERSIST_UNTIL_REVOKED = 2
RESPONSE_CANCELLED = 1

DIALOG_TIMEOUT = 90       # seconds; the first capture waits for the user
FRAME_TIMEOUT = 3         # seconds per frame from PipeWire
FRAME_ATTEMPTS = 3        # a stream may start with an empty (black) frame
INTERACTIVE_SECONDS = 1.0 # a selection that took longer than this waited for the user
SETTLE_SECONDS = 0.6      # then GNOME's dialog is still fading out: wait before reading

_counter = iter(range(1, 1 << 62))
_support = None           # cached result of available()


def state_dir():
    common = os.environ.get("SNAP_USER_COMMON")
    if common:
        # Shared by every revision of the snap, so an update keeps the grant.
        return Path(common)
    return Path(GLib.get_user_data_dir()) / "kingfisher"


def token_path():
    return state_dir() / "screencast-token"


def load_token():
    try:
        token = token_path().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    # Tokens are short opaque strings (a UUID in practice); ignore anything else.
    return token if 0 < len(token) <= 256 and token.isprintable() else ""


def has_token():
    return bool(load_token())


def save_token(token):
    """Store the token atomically and readable by the user only."""
    path = token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".screencast-token-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(token)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError:
        pass   # without a stored token the next capture just asks again


def forget():
    """Drop the saved grant; the next capture shows the dialog again."""
    try:
        token_path().unlink(missing_ok=True)
    except OSError:
        pass


def _gst():
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        return None
    ok, _argv = Gst.init_check(None)
    return Gst if ok else None


def available():
    """Whether this route can work here: the portal can remember the choice
    (ScreenCast version 4) and GStreamer can read PipeWire streams."""
    global _support
    if _support is None:
        gst = _gst()
        _support = (_portal_version() >= 4 and gst is not None
                    and gst.ElementFactory.find("pipewiresrc") is not None)
    return _support


def _portal_property(name):
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(PORTAL_BUS, PORTAL_PATH, "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (SCREENCAST_IFACE, name)),
                              GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
        return reply.unpack()[0]
    except GLib.Error:
        return None


def _portal_version():
    version = _portal_property("version")
    return version if isinstance(version, int) else 0


# ------------------------------------------------------------------ compose
def compose(streams, monitors=()):
    """One image of the whole desktop from per-monitor frames.

    ``streams``: (x, y, width, height, image) per shared monitor, position and
    size in logical (compositor) coordinates, image in device pixels.
    ``monitors``: logical (x, y, width, height) of every monitor, so the
    result covers the same area the selection overlay maps onto. Areas of
    monitors the user did not share stay black.

    The result uses one scale for the whole desktop (the largest of the
    streams), which is what the overlay's layout expects.
    """
    if not streams:
        raise ValueError("no frames")
    rects = [tuple(m) for m in monitors] or [s[:4] for s in streams]
    rects += [s[:4] for s in streams]
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    scale = max(img.width / max(w, 1) for _x, _y, w, _h, img in streams)

    if len(streams) == 1 and len(monitors) <= 1:
        return streams[0][4]   # the common case: one screen, nothing to stitch
    canvas = Image.new("RGB", (round((right - left) * scale), round((bottom - top) * scale)))
    for x, y, w, h, frame in streams:
        size = (round(w * scale), round(h * scale))
        tile = frame if frame.size == size else frame.resize(size, Image.LANCZOS)
        canvas.paste(tile, (round((x - left) * scale), round((y - top) * scale)))
    return canvas


def _gdk_monitors():
    try:
        import gi
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display is None:
            return []
        monitors = display.get_monitors()
        rects = []
        for i in range(monitors.get_n_items()):
            g = monitors.get_item(i).get_geometry()
            rects.append((g.x, g.y, g.width, g.height))
        return rects
    except (ImportError, ValueError):
        return []


# ------------------------------------------------------------------ capture
class _Capture:
    """One capture: a portal session from start to close, then the frames.

    ``on_done(image, error, cancelled)`` runs once on the main loop."""

    def __init__(self, on_done):
        self.on_done = on_done
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.session = None
        self.sub = 0
        self.timeout = 0
        self.finished = False
        self.asked_at = 0.0
        self.waiting_for_user = False   # GNOME's dialog may be on screen

    # -------------------------------------------------------------- steps
    def start(self):
        self.timeout = GLib.timeout_add_seconds(DIALOG_TIMEOUT, self._on_timeout)
        self._request("CreateSession", lambda handle: GLib.Variant("(a{sv})", ({
            "handle_token": GLib.Variant("s", handle),
            "session_handle_token": GLib.Variant("s", f"kingfisher{os.getpid()}_{next(_counter)}"),
        },)), self._on_session)

    def _on_session(self, results):
        self.session = results.get("session_handle")
        if not self.session:
            self._fail("The screen-sharing service did not start a session.")
            return
        options = {
            "types": GLib.Variant("u", SOURCE_MONITOR),
            "multiple": GLib.Variant("b", True),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        cursor_modes = _portal_property("AvailableCursorModes")
        if isinstance(cursor_modes, int) and cursor_modes & CURSOR_HIDDEN:
            options["cursor_mode"] = GLib.Variant("u", CURSOR_HIDDEN)
        token = load_token()
        if token:
            options["restore_token"] = GLib.Variant("s", token)

        def args(handle):
            opts = dict(options, handle_token=GLib.Variant("s", handle))
            return GLib.Variant("(oa{sv})", (self.session, opts))
        self.asked_at = time.monotonic()
        self.waiting_for_user = True
        self._request("SelectSources", args, self._on_selected)

    def _on_selected(self, _results):
        self._request("Start", lambda handle: GLib.Variant("(osa{sv})", (
            self.session, "", {"handle_token": GLib.Variant("s", handle)})), self._on_started)

    def _on_started(self, results):
        self.waiting_for_user = False
        # Each token works once: keep the new one, or none if the user did
        # not choose to remember the selection.
        token = results.get("restore_token")
        if isinstance(token, str) and token:
            save_token(token)
        else:
            forget()
        streams = []
        for node, props in results.get("streams", []):
            pos = props.get("position", (0, 0))
            size = props.get("size", (0, 0))
            streams.append((node, pos[0], pos[1], size[0], size[1]))
        if not streams:
            self._fail("No screen was shared.")
            return
        # The user has answered; what is left is opening the stream and
        # reading the frames, which gets a budget of its own.
        if self.timeout:
            GLib.source_remove(self.timeout)
        budget = SETTLE_SECONDS + 5 + FRAME_TIMEOUT * FRAME_ATTEMPTS * len(streams)
        self.timeout = GLib.timeout_add_seconds(int(budget) + 1, self._on_timeout)
        self.bus.call_with_unix_fd_list(
            PORTAL_BUS, PORTAL_PATH, SCREENCAST_IFACE, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, 5000, None, None,
            lambda bus, res: self._on_remote(bus, res, streams))

    def _on_remote(self, bus, result, streams):
        if self.finished:
            return
        try:
            reply, fds = bus.call_with_unix_fd_list_finish(result)
            fd = fds.get(reply.unpack()[0])
        except GLib.Error as err:
            self._fail(f"Could not open the screen stream: {err.message}")
            return
        monitors = _gdk_monitors()
        # If GNOME's dialog was on screen, it is still fading out: a frame
        # read now would freeze it into the picture.
        settle = SETTLE_SECONDS if time.monotonic() - self.asked_at > INTERACTIVE_SECONDS else 0
        threading.Thread(target=self._read_frames, args=(fd, streams, monitors, settle),
                         daemon=True).start()

    def _read_frames(self, fd, streams, monitors, settle=0):
        """Worker thread: one frame per stream, then back to the main loop."""
        try:
            if settle:
                time.sleep(settle)
            frames = [(x, y, w, h, _read_frame(fd, node)) for node, x, y, w, h in streams]
            image, error = compose(frames, monitors), None
        except (CaptureStreamError, GLib.Error, ValueError, OSError) as err:
            image, error = None, str(err) or "Could not read the screen."
        finally:
            os.close(fd)
        GLib.idle_add(self._deliver, image, error)

    def _deliver(self, image, error):
        if error:
            self._fail(error)
        else:
            self._finish(image, None, False)
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ plumbing
    def _request(self, method, make_args, on_response):
        """Call a portal method and continue in ``on_response(results)``."""
        handle = f"kingfisher{os.getpid()}_{next(_counter)}"
        path = f"/org/freedesktop/portal/desktop/request/{self.sender}/{handle}"

        def on_signal(_conn, _sender, _path, _iface, _signal, params):
            self._unsubscribe()
            if self.finished:
                return
            code, results = params.unpack()
            if code == RESPONSE_CANCELLED:
                self._finish(None, "Screen capture was cancelled.", True)
            elif code != 0:
                self._fail("The screen-sharing request was refused.")
            else:
                on_response(results)

        def on_called(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error as err:
                self._unsubscribe()
                self._fail(f"Screen sharing is unavailable: {err.message}")

        # Subscribe before calling so a fast response is never missed.
        self._unsubscribe()
        self.sub = self.bus.signal_subscribe(PORTAL_BUS, REQUEST_IFACE, "Response", path, None,
                                             Gio.DBusSignalFlags.NONE, on_signal)
        self.bus.call(PORTAL_BUS, PORTAL_PATH, SCREENCAST_IFACE, method, make_args(handle),
                      GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None, on_called)

    def _unsubscribe(self):
        if self.sub:
            self.bus.signal_unsubscribe(self.sub)
            self.sub = 0

    def _on_timeout(self):
        self.timeout = 0
        if self.waiting_for_user:
            # The dialog was left unanswered. Closing the session takes it
            # away; reported like a cancellation so that no other dialog
            # (the Screenshot portal's) follows it.
            self._finish(None, "Screen access was not given in time.", True)
        else:
            self._fail("The screen-sharing service did not respond.")
        return GLib.SOURCE_REMOVE

    def _fail(self, message):
        self._finish(None, message, False)

    def _finish(self, image, error, cancelled):
        if self.finished:
            return
        self.finished = True
        self._unsubscribe()
        if self.timeout:
            GLib.source_remove(self.timeout)
            self.timeout = 0
        if self.session:
            # Ends the stream and GNOME's screen-sharing indicator.
            self.bus.call(PORTAL_BUS, self.session, SESSION_IFACE, "Close", None, None,
                          Gio.DBusCallFlags.NONE, 2000, None, None)
            self.session = None
        self.on_done(image, error, cancelled)


class CaptureStreamError(Exception):
    pass


def _read_frame(fd, node):
    """Pull one non-empty frame of a PipeWire node as an RGB image.

    pipewiresrc duplicates ``fd`` for its own connection, so the caller keeps
    ownership of it."""
    Gst = _gst()
    if Gst is None:
        raise CaptureStreamError("GStreamer is not available.")
    pipeline = Gst.parse_launch(
        f"pipewiresrc fd={int(fd)} path={int(node)} always-copy=true ! videoconvert ! "
        "video/x-raw,format=RGBx ! appsink name=sink sync=false max-buffers=1 drop=true")
    sink = pipeline.get_by_name("sink")
    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise CaptureStreamError("Could not start the screen stream.")
        image = None
        for _attempt in range(FRAME_ATTEMPTS):
            sample = sink.emit("try-pull-sample", FRAME_TIMEOUT * Gst.SECOND)
            if sample is None:
                break
            image = _sample_to_image(Gst, sample)
            if image.getbbox() is not None:   # not entirely black
                return image
        if image is None:
            raise CaptureStreamError("The screen stream sent no picture.")
        return image   # a genuinely black screen
    finally:
        pipeline.set_state(Gst.State.NULL)


def _sample_to_image(Gst, sample):
    caps = sample.get_caps().get_structure(0)
    width, height = caps.get_value("width"), caps.get_value("height")
    buf = sample.get_buffer()
    ok, info = buf.map(Gst.MapFlags.READ)
    if not ok:
        raise CaptureStreamError("Could not read the screen stream.")
    try:
        data = bytes(info.data)
    finally:
        buf.unmap(info)
    stride = len(data) // height
    # Newer Pillow keeps the padding byte as an "RGBX" image; the rest of the
    # app (and PNG) expects plain RGB.
    return Image.frombuffer("RGBX", (width, height), data, "raw", "RGBX", stride, 1).convert("RGB")


def capture(on_done):
    """Capture every shared monitor. ``on_done(image, error, cancelled)``."""
    try:
        _Capture(on_done).start()
    except GLib.Error as err:
        message = f"Screen sharing is unavailable: {err.message}"

        def report():
            on_done(None, message, False)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(report)
