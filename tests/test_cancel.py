# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
import threading
import time
import unittest

import gi

gi.require_version("Gtk", "4.0")

from kingfisher.ocr import CancelToken  # noqa: E402


class CancelTests(unittest.TestCase):
    def test_cancel_stops_a_wrapper_and_its_children(self):
        # A wrapper script whose child keeps the pipes open, as some Tesseract installs do.
        proc = subprocess.Popen(["sh", "-c", "sleep 5; echo done"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        token = CancelToken()
        token.attach(proc)
        threading.Timer(0.2, token.cancel).start()
        started = time.monotonic()
        proc.communicate(timeout=4)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(token.cancelled)


if __name__ == "__main__":
    unittest.main()
