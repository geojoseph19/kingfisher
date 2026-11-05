# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from kingfisher import display_path


class DisplayPathTests(unittest.TestCase):
    def test_home_is_shortened(self):
        self.assertEqual(display_path("/srv/people/ada/.local/share/kingfisher/tessdata", home="/srv/people/ada"),
                         "~/.local/share/kingfisher/tessdata")
        self.assertEqual(display_path("/srv/people/ada", home="/srv/people/ada"), "~")

    def test_other_paths_are_left_alone(self):
        self.assertEqual(display_path("/usr/share/tesseract-ocr", home="/srv/people/ada"), "/usr/share/tesseract-ocr")
        self.assertEqual(display_path("/srv/people/other/x", home="/srv/people/ada"), "/srv/people/other/x")


if __name__ == "__main__":
    unittest.main()
