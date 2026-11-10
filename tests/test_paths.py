# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import os
import unittest
from unittest import mock

import kingfisher
from kingfisher import display_path


class DisplayPathTests(unittest.TestCase):
    def test_home_is_shortened(self):
        self.assertEqual(display_path("/srv/people/ada/.local/share/kingfisher/tessdata", home="/srv/people/ada"),
                         "~/.local/share/kingfisher/tessdata")
        self.assertEqual(display_path("/srv/people/ada", home="/srv/people/ada"), "~")

    def test_other_paths_are_left_alone(self):
        self.assertEqual(display_path("/usr/share/tesseract-ocr", home="/srv/people/ada"), "/usr/share/tesseract-ocr")
        self.assertEqual(display_path("/srv/people/other/x", home="/srv/people/ada"), "/srv/people/other/x")


class SnapDetectionTests(unittest.TestCase):
    def detect(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return importlib.reload(kingfisher).IS_SNAP

    def tearDown(self):
        importlib.reload(kingfisher)

    def test_inside_the_kingfisher_snap(self):
        self.assertTrue(self.detect({"SNAP": "/snap/kingfisher/x1", "SNAP_NAME": "kingfisher"}))

    def test_terminal_opened_by_another_snap_is_not_the_sandbox(self):
        # Classic snaps (editors, IDEs) leak SNAP into the shells they open.
        self.assertFalse(self.detect({"SNAP": "/snap/code/200", "SNAP_NAME": "code"}))
        self.assertFalse(self.detect({}))

    def test_snap_paths_are_shortened_against_the_real_home(self):
        env = {"SNAP": "/snap/kingfisher/x1", "SNAP_NAME": "kingfisher",
               "SNAP_REAL_HOME": "/srv/people/ada", "HOME": "/srv/people/ada/snap/kingfisher/x1"}
        with mock.patch.dict(os.environ, env, clear=True):
            module = importlib.reload(kingfisher)
            self.assertEqual(module.display_path("/srv/people/ada/snap/kingfisher/common/tessdata"),
                             "~/snap/kingfisher/common/tessdata")


if __name__ == "__main__":
    unittest.main()
