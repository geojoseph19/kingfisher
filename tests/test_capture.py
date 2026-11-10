# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gi.repository import GLib
from PIL import Image

from kingfisher import capture


class PortalSweepTests(unittest.TestCase):
    """The portal fallback may only ever remove its own stray file."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.pictures = Path(self.dir.name)
        self._original = capture._pictures_dir
        capture._pictures_dir = lambda: self.pictures

    def tearDown(self):
        capture._pictures_dir = self._original
        self.dir.cleanup()

    def test_pictures_folder_follows_user_dirs(self):
        # A translated folder ("Bilder", "Imágenes") must be respected, in the
        # snap too, where the launcher rewrites user-dirs.dirs to host paths.
        with mock.patch.object(capture.GLib, "get_user_special_dir", return_value="/srv/people/ada/Bilder"):
            self.assertEqual(self._original(), Path("/srv/people/ada/Bilder"))

    def test_snap_fallback_uses_the_real_home(self):
        with mock.patch.object(capture.GLib, "get_user_special_dir", return_value=None), \
                mock.patch.object(capture, "real_home", return_value=Path("/srv/people/ada")):
            self.assertEqual(self._original(), Path("/srv/people/ada/Pictures"))

    def test_snap_skips_gnome_screenshot(self):
        with mock.patch.object(capture, "IS_SNAP", True):
            self.assertFalse(capture.silent_capture_available())

    def test_only_the_new_portal_file_is_removed(self):
        (self.pictures / "Screenshot.png").write_text("existed before")
        before = {"Screenshot.png"}
        started = time.time()
        (self.pictures / "Screenshot-1.png").write_text("portal leftover")
        (self.pictures / "holiday.png").write_text("the user's photo")
        (self.pictures / "Screenshot from 2026-11-05 10-00-00.png").write_text("a GNOME screenshot")
        capture._sweep(before, started, time.time())
        self.assertEqual(sorted(p.name for p in self.pictures.iterdir()),
                         ["Screenshot from 2026-11-05 10-00-00.png", "Screenshot.png", "holiday.png"])

    def test_files_from_outside_the_request_window_are_kept(self):
        started = time.time()
        (self.pictures / "Screenshot-2.png").write_text("written much later")
        capture._sweep(set(), started - 100, started - 50)
        self.assertTrue((self.pictures / "Screenshot-2.png").exists())



class LoadAndRemoveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "Screenshot.png"
        Image.new("RGB", (8, 6), "red").save(self.path)

    def test_file_is_deleted(self):
        image, removed = capture._load_and_remove(self.path.as_uri())
        self.assertEqual(image.size, (8, 6))
        self.assertTrue(removed)
        self.assertFalse(self.path.exists())

    def test_read_only_copy_is_reported(self):
        # Portals before 1.18 hand the snap a document it may not delete.
        with mock.patch.object(capture.os, "remove", side_effect=PermissionError):
            image, removed = capture._load_and_remove(self.path.as_uri())
        self.assertEqual(image.mode, "RGB")
        self.assertFalse(removed)

    def test_unexpected_location(self):
        with self.assertRaises(capture.CaptureError):
            capture._load_and_remove("https://example.org/a.png")

class RouteTests(unittest.TestCase):
    def test_choice(self):
        choose = capture.choose_route
        self.assertEqual(choose(None, True, False, True), "x11")
        self.assertEqual(choose(None, False, True, True), "screenshot")    # 24.04 and later
        self.assertEqual(choose(None, False, False, True), "screencast")   # 22.04
        self.assertEqual(choose(None, False, False, False), "screenshot")  # no PipeWire reader
        self.assertEqual(choose("screencast", True, True, False), "screencast")
        self.assertEqual(choose("bogus", True, False, False), "x11")

    def test_x11_session(self):
        with mock.patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
            self.assertTrue(capture.x11_session())
        with mock.patch.dict("os.environ", {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            self.assertFalse(capture.x11_session())

    def test_no_note_outside_the_snap(self):
        with mock.patch.object(capture, "IS_SNAP", False):
            self.assertFalse(capture.needs_access_note())


class SandboxedCaptureTests(unittest.TestCase):
    """Which route runs and what reaches the app, with the routes faked."""

    def capture(self, route, screencast_result=None, x11_image=None):
        results, portal = [], []
        context = GLib.MainContext.default()
        with mock.patch.object(capture, "IS_SNAP", True), \
                mock.patch.object(capture, "silent_capture_available", return_value=False), \
                mock.patch.object(capture, "sandbox_route", return_value=route), \
                mock.patch.object(capture.screencast, "available", return_value=True), \
                mock.patch.object(capture, "_grab_x11", return_value=x11_image), \
                mock.patch.object(capture, "_capture_portal", side_effect=portal.append), \
                mock.patch.object(capture.screencast, "capture",
                                  side_effect=lambda cb: cb(*screencast_result)):
            capture.capture_screen(lambda image, error: results.append((image, error)))
            while context.pending():
                context.iteration(False)
        return results, portal

    def test_x11_grab_is_silent(self):
        image = Image.new("RGB", (4, 4))
        results, portal = self.capture("x11", x11_image=image)
        self.assertEqual(results, [(image, None)])
        self.assertFalse(portal)
        self.assertTrue(capture.last_capture_was_silent())

    def test_failed_x11_grab_falls_back(self):
        image = Image.new("RGB", (4, 4))
        results, _portal = self.capture("x11", screencast_result=(image, None, False))
        self.assertEqual(results, [(image, None)])

    def test_screencast_failure_falls_back_to_the_screenshot_portal(self):
        results, portal = self.capture("screencast", screencast_result=(None, "refused", False))
        self.assertEqual(results, [])
        self.assertEqual(len(portal), 1)

    def test_screencast_cancel_is_final(self):
        results, portal = self.capture("screencast",
                                       screencast_result=(None, "Screen capture was cancelled.", True))
        self.assertFalse(portal)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0][1].retryable)


class RetakeTests(unittest.TestCase):
    """The first picture after "Allow" may show the fading dialog."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(capture.screencast, "state_dir", return_value=Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_portal(self, elapsed, remembers, error=None):
        calls, results = [], []
        clock = iter([0.0, elapsed, elapsed + 0.1, elapsed + 0.2])
        first, second = Image.new("RGB", (2, 2), "red"), Image.new("RGB", (2, 2), "blue")

        def fake_portal(cb):
            calls.append(cb)
            if error:
                cb(None, error)
            else:
                cb(first if len(calls) == 1 else second, None)

        with mock.patch.object(capture, "_capture_portal", side_effect=fake_portal), \
                mock.patch.object(capture.time, "monotonic", side_effect=lambda: next(clock)), \
                mock.patch.object(capture, "portal_remembers_screenshots", return_value=remembers), \
                mock.patch.object(capture.GLib, "timeout_add", side_effect=lambda _ms, fn: fn()):
            capture._capture_portal_once_asked(lambda image, error: results.append(image))
        return len(calls), results, second

    def test_retaken_once_after_the_permission_dialog(self):
        count, results, second = self.run_portal(elapsed=5.0, remembers=True)
        self.assertEqual(count, 2)
        self.assertEqual(results, [second])

    def test_silent_capture_is_kept(self):
        count, results, _second = self.run_portal(elapsed=0.2, remembers=True)
        self.assertEqual((count, len(results)), (1, 1))

    def test_portal_that_always_asks_is_not_asked_twice(self):
        count, _results, _second = self.run_portal(elapsed=5.0, remembers=False)
        self.assertEqual(count, 1)

    def test_slow_captures_are_not_retaken_once_allowed(self):
        self.run_portal(elapsed=5.0, remembers=True)
        count, results, _second = self.run_portal(elapsed=5.0, remembers=True)
        self.assertEqual((count, len(results)), (1, 1))

    def test_a_failed_capture_allows_a_retake_again(self):
        self.run_portal(elapsed=5.0, remembers=True)
        self.run_portal(elapsed=5.0, remembers=True, error="denied")
        count, _results, _second = self.run_portal(elapsed=5.0, remembers=True)
        self.assertEqual(count, 2)

if __name__ == "__main__":
    unittest.main()
