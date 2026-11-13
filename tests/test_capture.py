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

    def test_refused_screenshots_move_to_screen_sharing(self):
        choose = capture.choose_route
        self.assertEqual(choose(None, False, True, True, True), "screencast")
        self.assertEqual(choose(None, False, True, False, True), "screenshot")   # nothing else to use
        self.assertEqual(choose("screenshot", False, True, True, True), "screenshot")

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

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patcher in (mock.patch.object(capture.screencast, "state_dir", return_value=Path(tmp.name)),
                        mock.patch.object(capture, "_refused", False)):
            patcher.start()
            self.addCleanup(patcher.stop)

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

    def test_screencast_failure_is_reported_once_screenshots_are_refused(self):
        capture._remember_refusal()
        results, portal = self.capture("screencast", screencast_result=(None, "No screen was shared.", False))
        self.assertFalse(portal)
        self.assertEqual(results, [(None, "No screen was shared.")])
        self.assertFalse(results[0][1].retryable)


class RefusedScreenshotTests(unittest.TestCase):
    """Ubuntu 24.04: GNOME Shell refuses the Screenshot portal's question for
    a snap, so the portal says no without the user having seen anything."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        for patcher in (mock.patch.object(capture.screencast, "state_dir", return_value=self.state),
                        mock.patch.object(capture, "_refused", False),
                        mock.patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0"}),
                        mock.patch.object(capture, "x11_session", return_value=False),
                        mock.patch.object(capture, "IS_SNAP", True),
                        mock.patch.object(capture, "silent_capture_available", return_value=False),
                        mock.patch.object(capture, "portal_remembers_screenshots", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        capture.os.environ.pop("KINGFISHER_CAPTURE", None)   # restored with the environment patch
        patcher = mock.patch.object(capture.screencast, "available", return_value=True)
        self.screencast_available = patcher.start()
        self.addCleanup(patcher.stop)

    def capture(self, elapsed=0.01, screencast_works=True, screencast_result=None, portal_image=None):
        """One capture; the Screenshot portal answers after ``elapsed`` seconds."""
        results, portal_calls, cast_calls = [], [], []
        clock = [100.0]

        def fake_portal(cb):
            portal_calls.append(cb)
            clock[0] += elapsed
            if portal_image is not None:
                cb(portal_image, None)
            else:
                cb(None, capture.CaptureFailure(capture.REFUSED_MESSAGE, retryable=False, refused=True))

        def fake_cast(cb):
            cast_calls.append(cb)
            cb(*screencast_result)

        self.screencast_available.return_value = screencast_works
        with mock.patch.object(capture, "_capture_portal", side_effect=fake_portal), \
                mock.patch.object(capture.screencast, "capture", side_effect=fake_cast), \
                mock.patch.object(capture.time, "monotonic", side_effect=lambda: clock[0]):
            capture.capture_screen(lambda image, error: results.append((image, error)))
            route = capture.sandbox_route()
        return results, len(portal_calls), len(cast_calls), route

    def test_silent_refusal_asks_for_access_instead_of_failing(self):
        results, portal, cast, route = self.capture()
        self.assertEqual((portal, cast), (1, 0))
        (image, error), = results
        self.assertIsNone(image)
        self.assertTrue(error.needs_access)
        self.assertFalse(error.retryable)
        # From now on, and after a restart, the capture uses screen sharing.
        self.assertEqual(route, "screencast")
        self.assertTrue(capture.screenshot_refused_marker().exists())
        with mock.patch.object(capture.screencast, "has_token", return_value=False):
            self.assertTrue(capture.needs_access_note())

    def test_next_capture_goes_straight_to_screen_sharing(self):
        self.capture()
        image = Image.new("RGB", (4, 4))
        results, portal, cast, _route = self.capture(screencast_result=(image, None, False))
        self.assertEqual((portal, cast), (0, 1))
        self.assertEqual(results, [(image, None)])

    def test_refusal_holds_when_the_marker_cannot_be_written(self):
        # Otherwise the application would start the same capture forever.
        with mock.patch.object(capture.Path, "touch", side_effect=PermissionError):
            _results, _portal, _cast, route = self.capture()
        self.assertFalse(capture.screenshot_refused_marker().exists())
        self.assertEqual(route, "screencast")

    def test_remembered_selection_is_used_at_once(self):
        image = Image.new("RGB", (4, 4))
        with mock.patch.object(capture.screencast, "has_token", return_value=True):
            results, portal, cast, _route = self.capture(screencast_result=(image, None, False))
        self.assertEqual((portal, cast), (1, 1))
        self.assertEqual(results, [(image, None)])

    def test_slow_refusal_is_treated_the_same(self):
        # A slow machine, or "Deny" chosen in a dialog: never a dead end.
        results, portal, cast, route = self.capture(elapsed=6.0)
        self.assertEqual((portal, cast), (1, 0))
        self.assertTrue(results[0][1].needs_access)
        self.assertEqual(route, "screencast")

    def test_without_screen_sharing_the_refusal_is_final(self):
        results, portal, cast, route = self.capture(screencast_works=False)
        self.assertEqual((portal, cast), (1, 0))
        (_image, error), = results
        self.assertFalse(error.needs_access)
        self.assertFalse(error.retryable)
        self.assertEqual(route, "screenshot")

    def test_forced_route_cannot_loop(self):
        with mock.patch.dict("os.environ", {"KINGFISHER_CAPTURE": "screenshot"}):
            results, _portal, cast, _route = self.capture()
        self.assertEqual(cast, 0)
        self.assertFalse(results[0][1].needs_access)

    def test_allowed_screenshots_are_untouched(self):
        image = Image.new("RGB", (4, 4))
        results, portal, cast, route = self.capture(portal_image=image)
        self.assertEqual((portal, cast, route), (1, 0, "screenshot"))
        self.assertEqual(results, [(image, None)])
        self.assertFalse(capture.screenshots_refused())
        self.assertEqual(capture.access_state(), "granted")

    def test_message_names_no_document(self):
        self.assertNotIn("README", capture.REFUSED_MESSAGE)

    def test_reset_starts_over(self):
        self.capture()
        capture.screencast.save_token("token")
        self.assertEqual(capture.access_state(), "granted")
        capture.reset_access()
        self.assertFalse(capture.screenshots_refused())
        self.assertFalse(capture.screencast.has_token())
        self.assertEqual(capture.sandbox_route(), "screenshot")
        self.assertEqual(capture.access_state(), "ask")


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
