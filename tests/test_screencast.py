# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from kingfisher import screencast


def solid(size, color):
    return Image.new("RGB", size, color)


class ComposeTests(unittest.TestCase):
    def test_single_screen_is_returned_as_is(self):
        frame = solid((1920, 1080), "red")
        self.assertIs(screencast.compose([(0, 0, 1920, 1080, frame)], [(0, 0, 1920, 1080)]), frame)

    def test_two_screens_side_by_side(self):
        left, right = solid((1920, 1080), "red"), solid((1280, 1024), "blue")
        image = screencast.compose([(0, 0, 1920, 1080, left), (1920, 0, 1280, 1024, right)],
                                   [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)])
        self.assertEqual(image.size, (3200, 1080))
        self.assertEqual(image.getpixel((10, 10)), (255, 0, 0))
        self.assertEqual(image.getpixel((1930, 10)), (0, 0, 255))
        self.assertEqual(image.getpixel((1930, 1070)), (0, 0, 0))   # below the shorter screen

    def test_hidpi_frames_keep_device_pixels(self):
        # Logical 1440x900 at scale 2 arrives as a 2880x1800 frame.
        frame = solid((2880, 1800), "green")
        image = screencast.compose([(0, 0, 1440, 900, frame)], [(0, 0, 1440, 900), (1440, 0, 1440, 900)])
        self.assertEqual(image.size, (5760, 1800))
        self.assertEqual(image.getpixel((100, 100)), (0, 128, 0))

    def test_mixed_scales_use_the_largest(self):
        hidpi, normal = solid((2880, 1800), "green"), solid((1920, 1080), "blue")
        image = screencast.compose([(0, 0, 1440, 900, hidpi), (1440, 0, 1920, 1080, normal)],
                                   [(0, 0, 1440, 900), (1440, 0, 1920, 1080)])
        self.assertEqual(image.size, (6720, 2160))
        self.assertEqual(image.getpixel((2880 + 100, 100)), (0, 0, 255))

    def test_screen_left_of_the_primary(self):
        # Monitors may have negative coordinates; the result starts at the leftmost.
        image = screencast.compose([(-1280, 0, 1280, 1024, solid((1280, 1024), "blue")),
                                    (0, 0, 1920, 1080, solid((1920, 1080), "red"))],
                                   [(-1280, 0, 1280, 1024), (0, 0, 1920, 1080)])
        self.assertEqual(image.size, (3200, 1080))
        self.assertEqual(image.getpixel((10, 10)), (0, 0, 255))
        self.assertEqual(image.getpixel((1290, 10)), (255, 0, 0))

    def test_unshared_screen_stays_black_and_keeps_the_layout(self):
        image = screencast.compose([(1920, 0, 1920, 1080, solid((1920, 1080), "red"))],
                                   [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)])
        self.assertEqual(image.size, (3840, 1080))
        self.assertEqual(image.getpixel((10, 10)), (0, 0, 0))
        self.assertEqual(image.getpixel((1930, 10)), (255, 0, 0))

    def test_without_monitor_list_the_streams_define_the_area(self):
        image = screencast.compose([(0, 0, 100, 50, solid((100, 50), "red")),
                                    (100, 0, 100, 50, solid((100, 50), "blue"))])
        self.assertEqual(image.size, (200, 50))

    def test_no_frames(self):
        with self.assertRaises(ValueError):
            screencast.compose([])


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"SNAP_USER_COMMON": self.dir.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_round_trip_and_private(self):
        screencast.save_token("f5c92f0e-42e0-4624-845b-4b11dd8b1aa9")
        self.assertEqual(screencast.load_token(), "f5c92f0e-42e0-4624-845b-4b11dd8b1aa9")
        mode = stat.S_IMODE(screencast.token_path().stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(os.listdir(self.dir.name), ["screencast-token"])   # no temporary files left

    def test_forget(self):
        screencast.save_token("abc")
        screencast.forget()
        self.assertFalse(screencast.has_token())
        screencast.forget()   # forgetting twice is fine

    def test_damaged_file_is_ignored(self):
        Path(self.dir.name, "screencast-token").write_bytes(b"\xff\xfe\x00junk")
        self.assertEqual(screencast.load_token(), "")
        Path(self.dir.name, "screencast-token").write_text("x" * 1000)
        self.assertEqual(screencast.load_token(), "")


class CaptureFlowTests(unittest.TestCase):
    """The session flow, with the portal replaced by a scripted fake."""

    def run_flow(self, responses, frames_ok=True):
        calls = []
        done = []

        class FakeBus:
            def get_unique_name(self):
                return ":1.42"

            def signal_subscribe(self, *args):
                self.handler = args[-1]
                self.path = args[3]
                return 7

            def signal_unsubscribe(self, _sub):
                pass

            def call(self, _bus, path, iface, method, args, *_rest):
                calls.append(method)
                if method == "Close":
                    return
                code, results = responses[method]
                self.handler(None, None, self.path, None, None, FakeVariant((code, results)))

            def call_with_unix_fd_list(self, *_args):
                calls.append("OpenPipeWireRemote")

        class FakeVariant:
            def __init__(self, value):
                self.value = value

            def unpack(self):
                return self.value

        bus = FakeBus()
        with mock.patch.object(screencast.Gio, "bus_get_sync", return_value=bus), \
                mock.patch.object(screencast, "_portal_property", return_value=7), \
                mock.patch.object(screencast, "save_token") as save, \
                mock.patch.object(screencast, "forget") as forget, \
                mock.patch.object(screencast, "load_token", return_value="old-token"), \
                mock.patch.object(screencast.GLib, "timeout_add_seconds", return_value=0):
            screencast._Capture(lambda *a: done.append(a)).start()
        return calls, done, save, forget

    def test_cancel_in_the_dialog_is_reported_as_cancelled(self):
        calls, done, _save, _forget = self.run_flow({
            "CreateSession": (0, {"session_handle": "/s/1"}),
            "SelectSources": (1, {}),
        })
        self.assertEqual(done, [(None, "Screen capture was cancelled.", True)])
        self.assertEqual(calls[-1], "Close")   # the session is always closed

    def timed_out(self, waiting_for_user):
        done = []
        with mock.patch.object(screencast.Gio, "bus_get_sync", return_value=mock.Mock(
                get_unique_name=lambda: ":1.42")):
            capture = screencast._Capture(lambda *a: done.append(a))
        capture.waiting_for_user = waiting_for_user
        capture._on_timeout()
        return done

    def test_an_unanswered_dialog_ends_like_a_cancellation(self):
        # Otherwise the Screenshot portal's dialog would follow the first one.
        self.assertEqual(self.timed_out(True), [(None, "Screen access was not given in time.", True)])

    def test_a_silent_service_is_a_failure_that_allows_a_fallback(self):
        self.assertEqual(self.timed_out(False),
                         [(None, "The screen-sharing service did not respond.", False)])

    def test_refusal_is_a_failure_that_allows_a_fallback(self):
        _calls, done, _save, _forget = self.run_flow({
            "CreateSession": (0, {"session_handle": "/s/1"}),
            "SelectSources": (0, {}),
            "Start": (2, {}),
        })
        self.assertEqual(len(done), 1)
        self.assertIsNone(done[0][0])
        self.assertFalse(done[0][2])

    def test_new_token_replaces_the_used_one(self):
        calls, _done, save, forget = self.run_flow({
            "CreateSession": (0, {"session_handle": "/s/1"}),
            "SelectSources": (0, {}),
            "Start": (0, {"restore_token": "new-token",
                          "streams": [(42, {"position": (0, 0), "size": (1920, 1080)})]}),
        })
        save.assert_called_once_with("new-token")
        forget.assert_not_called()
        self.assertIn("OpenPipeWireRemote", calls)

    def test_without_remember_the_stale_token_is_dropped(self):
        _calls, _done, save, forget = self.run_flow({
            "CreateSession": (0, {"session_handle": "/s/1"}),
            "SelectSources": (0, {}),
            "Start": (0, {"streams": [(42, {"position": (0, 0), "size": (1920, 1080)})]}),
        })
        save.assert_not_called()
        forget.assert_called_once()



class SettleTests(unittest.TestCase):
    """After GNOME's dialog the frame is read only once the dialog has faded."""

    def settle_for(self, asked_seconds_ago):
        started = []

        class FakeThread:
            def __init__(self, target, args, daemon):
                started.append(args[-1])

            def start(self):
                pass

        class FakeBus:
            def get_unique_name(self):
                return ":1.42"

            def call_with_unix_fd_list_finish(self, _result):
                class Reply:
                    def unpack(self):
                        return (0,)

                class Fds:
                    def get(self, _index):
                        return 99
                return Reply(), Fds()

        with mock.patch.object(screencast.Gio, "bus_get_sync", return_value=FakeBus()), \
                mock.patch.object(screencast.threading, "Thread", FakeThread), \
                mock.patch.object(screencast, "_gdk_monitors", return_value=[]):
            cap = screencast._Capture(lambda *a: None)
            cap.asked_at = screencast.time.monotonic() - asked_seconds_ago
            cap._on_remote(cap.bus, None, [(42, 0, 0, 1920, 1080)])
        return started[0]

    def test_silent_restore_reads_at_once(self):
        self.assertEqual(self.settle_for(0.1), 0)

    def test_after_the_dialog_it_waits(self):
        self.assertEqual(self.settle_for(5), screencast.SETTLE_SECONDS)

if __name__ == "__main__":
    unittest.main()
