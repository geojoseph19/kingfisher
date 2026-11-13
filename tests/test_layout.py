# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from kingfisher.ocr import Word, _build_lines, _build_paragraphs


def word(text, left, top, block=1, par=1, line=1, width=None, height=14):
    return Word(text, 95.0, left, top, width or 8 * len(text), height, block, par, line)


def rtl_line(texts, right=800, top=10, **extra):
    """Words of a right-to-left line in reading order: the first is rightmost."""
    words = []
    for text in texts:
        width = 8 * len(text)
        words.append(word(text, right - width, top, **extra))
        right -= width + 8
    return words


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


class RightToLeftTests(unittest.TestCase):
    """Tesseract lists the words of an Arabic, Hebrew or Urdu line in reading
    order, starting at the right. Sorting them by position reversed every
    sentence in the "As on Screen" format."""

    def test_sentence_keeps_its_reading_order(self):
        words = rtl_line(["مرحبا", "بكم", "في", "المكتبة", "العامة"])
        self.assertEqual(_build_lines(words), "مرحبا بكم في المكتبة العامة")
        self.assertEqual(_build_paragraphs(words), "مرحبا بكم في المكتبة العامة")

    def test_a_number_at_the_end_stays_at_the_end(self):
        words = rtl_line(["السعر", "250", "ريال"])
        self.assertEqual(_build_lines(words), "السعر 250 ريال")

    def test_embedded_left_to_right_phrase_gets_single_spaces(self):
        # "Open File" reads forwards inside the line, so "على" is followed in
        # reading order by a word at the phrase's far end: not a column gap.
        words = [word("اضغط", 900, 10, width=60), word("على", 842, 10, width=50),
                 word("Open", 700, 10, width=70), word("File", 778, 10, width=56),
                 word("ثم", 662, 10, width=30)]
        self.assertEqual(_build_lines(words), "اضغط على Open File ثم")

    def test_lines_are_indented_from_the_right(self):
        words = rtl_line(["عنوان"], right=800, top=10, line=1) + rtl_line(["فرعي", "هنا"], right=768, top=30, line=2)
        first, second = _build_lines(words).splitlines()
        self.assertEqual(first, "عنوان")
        self.assertTrue(second.startswith("  ") and second.strip() == "فرعي هنا")

    def test_columns_are_read_from_the_right(self):
        words = [word("الاسم", 700, 10, block=1, width=60), word("الكمية", 300, 10, block=2, width=60)]
        row = _build_lines(words)
        self.assertEqual(row.split(), ["الاسم", "الكمية"])
        self.assertIn("   ", row)   # the column gap is kept

    def test_hebrew(self):
        words = rtl_line(["שלום", "עולם"])
        self.assertEqual(_build_lines(words), "שלום עולם")

    def test_left_to_right_text_is_untouched(self):
        words = [word("Total", 10, 10), word("2024", 60, 10), word("items", 110, 10)]
        self.assertEqual(_build_lines(words), "Total 2024 items")
        # An Arabic name inside an English line does not flip the line.
        words = [word("Hello", 10, 10), word("مرحبا", 58, 10), word("world", 106, 10)]
        self.assertEqual(_build_lines(words), "Hello مرحبا world")


class UnspacedScriptTests(unittest.TestCase):
    """Chinese and Japanese have no spaces between words; Tesseract reports
    them in tokens of a character or two."""

    def test_chinese_tokens_are_joined(self):
        words = [word("欢迎", 10, 10, width=56), word("来", 70, 10, width=28), word("到", 100, 10, width=28),
                 word("公共", 130, 10, width=56)]
        self.assertEqual(_build_lines(words), "欢迎来到公共")
        self.assertEqual(_build_paragraphs(words), "欢迎来到公共")

    def test_japanese_tokens_are_joined(self):
        words = [word("これ", 10, 10, width=56), word("は", 70, 10, width=28), word("テス", 100, 10, width=56),
                 word("ト", 160, 10, width=28), word("です。", 190, 10, width=84)]
        self.assertEqual(_build_lines(words), "これはテストです。")

    def test_latin_and_numbers_keep_their_space(self):
        words = [word("测试", 10, 10, width=56), word("2024", 70, 10, width=40), word("Linux", 120, 10, width=50),
                 word("系统", 180, 10, width=56)]
        self.assertEqual(_build_lines(words), "测试 2024 Linux 系统")

    def test_columns_stay_apart(self):
        words = [word("姓名", 10, 10, block=1, width=56), word("数量", 400, 10, block=2, width=56)]
        self.assertIn("   ", _build_lines(words))

    def test_wrapped_sentence_joins_without_a_space(self):
        words = [word("这是", 10, 10, line=1, width=56), word("一个", 70, 10, line=1, width=56),
                 word("测试", 10, 30, line=2, width=56)]
        self.assertEqual(_build_paragraphs(words), "这是一个测试")

    def test_korean_keeps_its_spaces(self):
        # Korean separates words with spaces; Tesseract's boxes are too
        # imprecise to tell a split word from two words, so nothing is joined.
        words = [word("공공", 10, 10, width=56), word("도서관", 80, 10, width=84)]
        self.assertEqual(_build_lines(words), "공공 도서관")


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

