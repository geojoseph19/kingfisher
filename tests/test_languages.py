# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from kingfisher.languages import (
    POPULAR,
    code_from_package,
    friendly_error,
    next_stage,
    stage_text,
    human_size,
    install_command,
    package_name,
    suggested_codes,
)
from kingfisher.ocr import language_name


class PackageNameTests(unittest.TestCase):
    def test_round_trip(self):
        for code in ("hin", "chi_sim", "chi_tra_vert", "aze_cyrl"):
            self.assertEqual(code_from_package(package_name(code)), code)
        self.assertEqual(package_name("chi_sim"), "tesseract-ocr-chi-sim")

    def test_non_language_packages_are_skipped(self):
        for name in ("tesseract-ocr-all", "tesseract-ocr-osd", "tesseract-ocr-equ",
                     "tesseract-ocr-script-deva", "tesseract-ocr", "libtesseract-dev"):
            self.assertIsNone(code_from_package(name))

    def test_install_command(self):
        self.assertEqual(install_command(["hin", "chi_sim"]),
                         "sudo apt install tesseract-ocr-hin tesseract-ocr-chi-sim")


class SuggestionTests(unittest.TestCase):
    def test_locales_map_to_languages_in_order(self):
        self.assertEqual(suggested_codes(["hi_IN.UTF-8", "en_IN", "en", "C"]), ["hin", "eng"])
        self.assertEqual(suggested_codes(["ml_IN.UTF-8@latin"]), ["mal"])
        self.assertEqual(suggested_codes(["zh_TW.UTF-8"]), ["chi_tra"])
        self.assertEqual(suggested_codes(["zh_CN.UTF-8"]), ["chi_sim"])
        self.assertEqual(suggested_codes(["C", "POSIX"]), [])

    def test_every_popular_language_has_a_name(self):
        for code in POPULAR:
            self.assertNotEqual(language_name(code), code)


class SearchTests(unittest.TestCase):
    def test_word_start_matching(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Adw", "1")
        from kingfisher.language_window import _matches
        self.assertTrue(_matches("hin", "hin"))
        self.assertFalse(_matches("hin", "chi_sim"))
        self.assertTrue(_matches("chin", "chi_sim"))
        self.assertTrue(_matches("simpl", "chi_sim"))
        self.assertTrue(_matches("mal", "mal"))


class StageTests(unittest.TestCase):
    class Status:   # stand-in for PackageKitGlib.StatusEnum
        WAITING_FOR_AUTH, DOWNLOAD, INSTALL, SETUP, FINISHED, QUERY = range(6)

    def test_status_names(self):
        s = self.Status
        self.assertEqual(stage_text(s, s.WAITING_FOR_AUTH), "Waiting for password…")
        self.assertEqual(stage_text(s, s.DOWNLOAD), "Downloading…")
        self.assertEqual(stage_text(s, s.INSTALL), "Installing…")
        self.assertEqual(stage_text(s, s.QUERY), "Preparing…")

    def test_stages_never_go_backwards(self):
        shown = None
        seen = []
        for stage in ("Preparing…", "Waiting for password…", "Preparing…", "Downloading…",
                      "Preparing…", "Installing…", "Downloading…", "Finishing…"):
            shown = next_stage(shown, stage)
            if not seen or seen[-1] != shown:
                seen.append(shown)
        self.assertEqual(seen, ["Preparing…", "Waiting for password…", "Downloading…",
                                "Installing…", "Finishing…"])


class FormattingTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(human_size(0), "")
        self.assertEqual(human_size(900 * 1024), "900 KB")
        self.assertEqual(human_size(1_700_000), "1.6 MB")

    def test_errors_are_plain(self):
        self.assertIn("canceled", friendly_error("Not authorized"))
        self.assertIn("connection", friendly_error("Failed to download packages"))


if __name__ == "__main__":
    unittest.main()
