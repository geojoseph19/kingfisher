# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from kingfisher.ocr import Word, _build_lines, _build_paragraphs


def word(text, left, top, block=1, par=1, line=1, width=None, height=14):
    return Word(text, 95.0, left, top, width or 8 * len(text), height, block, par, line)


class LineLayoutTests(unittest.TestCase):
    def test_columns_from_separate_blocks_share_a_row(self):
        words = [word("Name", 10, 10, block=1, line=1), word("Qty", 10, 30, block=1, line=2),
                 word("Apples", 200, 10, block=2, line=1), word("4", 200, 30, block=2, line=2)]
        rows = _build_lines(words).splitlines()
        self.assertEqual([r.split() for r in rows], [["Name", "Apples"], ["Qty", "4"]])
        self.assertEqual(rows[0].index("Apples"), rows[1].index("4"))  # columns line up

    def test_indentation_is_kept(self):
        words = [word("def", 10, 10, line=1), word("f():", 42, 10, line=1),
                 word("return", 42, 30, line=2), word("1", 98, 30, line=2)]
        self.assertEqual(_build_lines(words), "def f():\n    return 1")

    def test_blank_line_for_vertical_gap(self):
        words = [word("one", 10, 10, line=1), word("two", 10, 60, block=2, line=1)]
        self.assertEqual(_build_lines(words), "one\n\ntwo")


class ParagraphTests(unittest.TestCase):
    def test_wrapped_lines_join_and_dehyphenate(self):
        words = [word("data", 10, 10, line=1), word("is", 50, 10, line=1), word("suit-", 70, 10, line=1),
                 word("able", 10, 28, block=2, line=1), word("here.", 50, 28, block=2, line=1)]
        self.assertEqual(_build_paragraphs(words), "data is suitable here.")

    def test_separate_paragraphs_stay_apart(self):
        words = [word("Title.", 10, 10, par=1), word("Body", 10, 60, block=2, par=1)]
        self.assertEqual(_build_paragraphs(words), "Title.\n\nBody")


if __name__ == "__main__":
    unittest.main()


class TabularTests(unittest.TestCase):
    def test_real_columns_use_monospace(self):
        from kingfisher.ocr import looks_tabular
        self.assertTrue(looks_tabular("Name      Size    Date\nreport    12 KB   Mon\nphoto     2 MB    Tue"))

    def test_prose_with_one_indented_line_does_not(self):
        from kingfisher.ocr import looks_tabular
        self.assertFalse(looks_tabular("A heading\nSome text here.\n                 Scan to RSVP"))
        self.assertFalse(looks_tabular("Plain prose.\nAnother line."))
        self.assertFalse(looks_tabular(""))

