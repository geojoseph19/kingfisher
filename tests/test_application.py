# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from kingfisher import application  # noqa: E402
from kingfisher.application import option_path, unreadable_message  # noqa: E402
from kingfisher.capture import CaptureFailure  # noqa: E402


class OptionPathTests(unittest.TestCase):
    def test_string_is_kept(self):
        self.assertEqual(option_path("/tmp/a.png"), "/tmp/a.png")

    def test_bytes_are_decoded(self):
        self.assertEqual(option_path(b"/tmp/a.png"), "/tmp/a.png")

    def test_filename_array_is_a_byte_list_with_a_nul(self):
        # What PyGObject actually hands over for GLib.OPTION_REMAINING.
        self.assertEqual(option_path(list(b"/tmp/na\xc3\xafve.png\0")), "/tmp/na\u00efve.png")


class UnreadableMessageTests(unittest.TestCase):
    def test_a_damaged_file_is_named(self):
        self.assertEqual(unreadable_message("/home/u/a.png", OSError("bad"), sandboxed=True),
                         "Not a readable image: a.png")

    def test_outside_the_sandbox_the_folder_is_named(self):
        for error in (PermissionError(), FileNotFoundError()):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(unreadable_message("/mnt/disk/a.png", error, sandboxed=True),
                                 "Kingfisher can't read files in /mnt/disk")

    def test_without_a_sandbox_a_missing_file_is_just_unreadable(self):
        self.assertEqual(unreadable_message("/mnt/disk/a.png", FileNotFoundError(), sandboxed=False),
                         "Not a readable image: a.png")


class ScreenAccessNoteTests(unittest.TestCase):
    """The one-time note before GNOME's screen-sharing dialog."""

    def answer(self, response, window_visible):
        handlers = []
        dialog = mock.Mock()
        dialog.connect.side_effect = lambda _signal, handler: handlers.append(handler)
        window = mock.Mock()
        window.get_visible.return_value = window_visible
        app = mock.Mock(_access_note=None, settings={"screen_access_explained": False})
        app.get_window.return_value = window
        proceed = mock.Mock(return_value=None)
        with mock.patch.object(application.Adw, "AlertDialog", return_value=dialog, create=True), \
                mock.patch.object(application.Adw, "ResponseAppearance", create=True), \
                mock.patch.object(application.GLib, "timeout_add", side_effect=lambda _ms, fn: fn()):
            application.KingfisherApplication._explain_screen_access(app, proceed)
            handlers[0](dialog, response)
        return app, window, proceed

    def test_declining_closes_a_window_that_was_opened_for_the_note(self):
        # A window that is only hidden keeps the application running unseen.
        app, window, proceed = self.answer("cancel", window_visible=False)
        window.close.assert_called_once()
        proceed.assert_not_called()
        self.assertFalse(app.settings["screen_access_explained"])

    def test_declining_leaves_an_open_window_alone(self):
        _app, window, proceed = self.answer("cancel", window_visible=True)
        window.close.assert_not_called()
        window.set_visible.assert_not_called()
        proceed.assert_not_called()

    def test_continuing_closes_a_window_that_was_opened_for_the_note(self):
        # Hiding it would leave it running unseen if the capture is canceled.
        app, window, proceed = self.answer("continue", window_visible=False)
        window.close.assert_called_once()
        window.set_visible.assert_not_called()
        proceed.assert_called_once_with(True)
        self.assertTrue(app.settings["screen_access_explained"])

    def test_continuing_steps_an_open_window_aside_for_the_capture(self):
        # The note brought the window to the front; the picture must not show it.
        app, window, proceed = self.answer("continue", window_visible=True)
        window.close.assert_not_called()
        proceed.assert_called_once_with(True)
        self.assertTrue(app.settings["screen_access_explained"])


class AccessNeededTests(unittest.TestCase):
    """A capture the desktop refused without asking starts again, this time
    with the note and the screen-sharing dialog, instead of ending in an error."""

    def captured(self, error, was_visible, **extra):
        window = mock.Mock()
        app = mock.Mock()
        app.get_window.return_value = window
        application.KingfisherApplication._on_captured(app, None, error, "area", was_visible, **extra)
        return app, window

    def test_capture_from_the_window_starts_again(self):
        error = CaptureFailure("refused", retryable=False, needs_access=True)
        app, window = self.captured(error, was_visible=True)
        app._finish_capture.assert_called_once()
        window.present.assert_called_once()        # the note belongs on an open window
        window.toast.assert_not_called()
        app.start_capture.assert_called_once_with("area", delay=0, step_aside=True)

    def test_capture_from_the_shortcut_starts_again(self):
        error = CaptureFailure("refused", retryable=False, needs_access=True)
        app, window = self.captured(error, was_visible=False)
        window.present.assert_not_called()
        app.start_capture.assert_called_once_with("area", delay=0, step_aside=False)

    def test_a_countdown_runs_again_after_access_is_given(self):
        error = CaptureFailure("refused", retryable=False, needs_access=True)
        app, _window = self.captured(error, was_visible=True, counted_down=3)
        app.start_capture.assert_called_once_with("area", delay=3, step_aside=True)

    def test_other_errors_are_shown(self):
        error = CaptureFailure("refused", retryable=False, refused=True)
        app, window = self.captured(error, was_visible=True)
        app.start_capture.assert_not_called()
        window.toast.assert_called_once()


class CaptureDelayTests(unittest.TestCase):
    """How long a capture waits before the picture is taken."""

    def wait_before_capture(self, visible_windows=(), **options):
        app = mock.Mock(_capturing=False, selector=None, settings={"screen_access_explained": True})
        app.get_windows.return_value = list(visible_windows)
        with mock.patch.object(application.GLib, "timeout_add") as timeout_add:
            application.KingfisherApplication.start_capture(app, "area", **options)
        return timeout_add.call_args[0][0]

    def test_menu_capture_waits_for_the_menu_to_close(self):
        # GNOME Shell's menu fades out for 150 ms after the click is reported.
        wait = self.wait_before_capture(step_aside=False, from_menu=True)
        self.assertEqual(wait, application.MENU_CLOSE_DELAY_MS)
        self.assertGreaterEqual(wait, 250)

    def test_shortcut_capture_stays_immediate(self):
        self.assertLessEqual(self.wait_before_capture(step_aside=False), 100)

    def test_capture_from_the_window_waits_for_it_to_hide(self):
        window = mock.Mock()
        window.get_visible.return_value = True
        self.assertEqual(self.wait_before_capture([window]), application.HIDE_DELAY_MS)


if __name__ == "__main__":
    unittest.main()
