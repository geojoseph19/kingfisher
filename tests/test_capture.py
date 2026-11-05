# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import time
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
