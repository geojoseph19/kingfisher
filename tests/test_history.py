# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from datetime import datetime, timedelta, timezone

from kingfisher.history import (
    History,
    HistoryEntry,
    day_label,
    group_by_day,
    history_meta,
    reflow_text,
    relative_time,
)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "sub" / "history.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_add_is_newest_first_and_persists(self):
        h = History(self.path)
        h.add(HistoryEntry(text="first"))
        h.add(HistoryEntry(text="second"))
        self.assertEqual([e.text for e in History(self.path).entries()], ["second", "first"])

    def test_cap_and_duplicates(self):
        h = History(self.path, max_entries=3)
        for t in ("a", "b", "c", "d", "b"):
            h.add(HistoryEntry(text=t))
        self.assertEqual([e.text for e in h.entries()], ["b", "d", "c"])

    def test_empty_results_are_not_stored(self):
        h = History(self.path)
        self.assertIsNone(h.add(HistoryEntry(text="   ")))
        self.assertEqual(len(h), 0)

    def test_code_only_result_is_stored(self):
        h = History(self.path)
        h.add(HistoryEntry(text="", codes=[["QR code", "https://example.org"]]))
        self.assertEqual(h.entries()[0].title, "QR code: https://example.org")

    def test_file_is_private(self):
        History(self.path).add(HistoryEntry(text="secret"))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.path.parent).st_mode), 0o700)

    def test_remove_and_clear(self):
        h = History(self.path)
        keep = h.add(HistoryEntry(text="keep"))
        drop = h.add(HistoryEntry(text="drop"))
        h.remove(drop.id)
        self.assertEqual([e.id for e in h.entries()], [keep.id])
        h.clear()
        self.assertFalse(self.path.exists())
        self.assertEqual(History(self.path).entries(), [])

    def test_corrupt_or_foreign_file_is_ignored(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        self.assertEqual(History(self.path).entries(), [])
        self.path.write_text(json.dumps({"entries": [{"text": "ok", "unknown": 1}, {"oops": 2}]}))
        self.assertEqual([e.text for e in History(self.path).entries()], ["ok"])

    def test_listener_called(self):
        h = History(self.path)
        calls = []
        h.connect(lambda: calls.append(1))
        h.add(HistoryEntry(text="x"))
        h.clear()
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()


class FormattingTests(unittest.TestCase):
    NOW = datetime(2026, 3, 10, 15, 30, tzinfo=timezone.utc)

    def ago(self, **delta):
        return relative_time(self.NOW - timedelta(**delta), now=self.NOW)

    def test_relative_time(self):
        self.assertEqual(self.ago(seconds=20), "Just now")
        self.assertEqual(self.ago(minutes=12), "12 min ago")
        self.assertEqual(self.ago(hours=3), "Today, 12:30")
        self.assertEqual(self.ago(days=1), "Yesterday, 15:30")
        self.assertEqual(self.ago(days=9), "1 Mar, 15:30")
        self.assertEqual(relative_time(None), "")

    def test_history_meta(self):
        self.assertIn("1 word", history_meta(HistoryEntry(text="hello", word_count=1)))
        self.assertNotIn("1 words", history_meta(HistoryEntry(text="hello", word_count=1)))
        code = HistoryEntry(text="", word_count=0, codes=[["QR code", "x"]])
        self.assertTrue(history_meta(code).endswith("QR code"))

    def test_day_labels_and_grouping(self):
        self.assertEqual(day_label(self.NOW - timedelta(hours=2), self.NOW), "Today")
        self.assertEqual(day_label(self.NOW - timedelta(days=1), self.NOW), "Yesterday")
        self.assertEqual(day_label(self.NOW - timedelta(days=3), self.NOW), "Saturday")
        self.assertEqual(day_label(self.NOW - timedelta(days=30), self.NOW), "8 February 2026")
        local_now = datetime(2026, 3, 10, 15, 30).astimezone()   # mid-afternoon in any time zone
        entries = [HistoryEntry(text=str(i), created=(local_now - timedelta(hours=h)).isoformat())
                   for i, h in enumerate((1, 2, 30, 31, 200))]
        groups = group_by_day(entries, now=local_now)
        self.assertEqual([len(g[1]) for g in groups], [2, 2, 1])
        self.assertEqual([e.text for g in groups for e in g[1]], ["0", "1", "2", "3", "4"])


class RestoreTests(unittest.TestCase):
    def test_restore_brings_entries_back(self):
        with tempfile.TemporaryDirectory() as d:
            h = History(Path(d) / "h.json")
            h.add(HistoryEntry(text="a"))
            h.add(HistoryEntry(text="b"))
            saved = h.entries()
            h.clear()
            h.restore(saved)
            self.assertEqual([e.text for e in History(Path(d) / "h.json").entries()], ["b", "a"])


class FormatTests(unittest.TestCase):
    def test_both_layouts_are_kept_and_offered(self):
        e = HistoryEntry(text="one\ntwo", paragraphs="one two")
        self.assertTrue(e.has_formats)
        self.assertEqual(e.text_in("lines"), "one\ntwo")
        self.assertEqual(e.text_in("paragraphs"), "one two")

    def test_older_entries_get_paragraphs_rebuilt(self):
        old = HistoryEntry(text="The report is due on Fri-\nday, and the draft\nshould go out.\n\nThanks,   team")
        self.assertTrue(old.has_formats)
        self.assertEqual(old.text_in("paragraphs"),
                         "The report is due on Friday, and the draft should go out.\n\nThanks, team")
        self.assertEqual(old.text_in("lines"), old.text)

    def test_code_only_entries_have_no_layouts(self):
        self.assertFalse(HistoryEntry(text="", codes=[["QR code", "x"]]).has_formats)

    def test_reflow_keeps_real_hyphens(self):
        self.assertEqual(reflow_text("well-\nKnown"), "well- Known")
        self.assertEqual(reflow_text("e-\nmail"), "email")

    def test_old_files_still_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h.json"
            path.write_text(json.dumps({"version": 1, "entries": [{"text": "legacy", "word_count": 1}]}))
            entry = History(path).entries()[0]
            self.assertEqual((entry.text, entry.paragraphs, entry.has_formats), ("legacy", "", True))


class DamagedFileTests(unittest.TestCase):
    def test_bad_fields_do_not_break_loading(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "h.json"
            path.write_text(json.dumps({"entries": [
                {"text": "good", "created": None, "word_count": "many", "codes": [["QR code"], 5]},
                {"text": 42},
                "not an entry",
                {"text": "", "codes": []},
            ]}))
            entries = History(path).entries()
            self.assertEqual([e.text for e in entries], ["good"])
            self.assertIsNotNone(entries[0].created_local)   # unreadable date: a fresh one
            self.assertIsNone(HistoryEntry(text="x", created=None).created_local)
            self.assertEqual(entries[0].word_count, 0)
            self.assertEqual(entries[0].codes, [])

    def test_existing_folder_is_made_private(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "kingfisher"
            folder.mkdir(mode=0o755)
            os.chmod(folder, 0o755)
            History(folder / "history.json").add(HistoryEntry(text="secret"))
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)


class UndoTests(unittest.TestCase):
    def test_undo_puts_back_exactly_what_was_removed(self):
        with tempfile.TemporaryDirectory() as d:
            h = History(Path(d) / "h.json")
            for text in ("c", "b", "a"):
                h.add(HistoryEntry(text=text))          # newest first: a, b, c
            a, b, _c = h.entries()
            removed_a = [(0, a)]
            h.remove(a.id)
            removed_b = [(0, b)]
            h.remove(b.id)
            h.reinsert(removed_a)                        # Undo on the first toast
            self.assertEqual([e.text for e in h.entries()], ["a", "c"])
            h.reinsert(removed_b)                        # then on the second
            self.assertEqual([e.text for e in h.entries()], ["b", "a", "c"])
            h.reinsert(removed_a)                        # repeating is harmless
            self.assertEqual(len(h.entries()), 3)

