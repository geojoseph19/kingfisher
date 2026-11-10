# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gi.repository import GLib

from kingfisher import autostart
from kingfisher.autostart import _desktop_quote, exec_value


def parse_exec(value):
    """Read an Exec value the way desktops do: through GLib's key-file reader
    (string unescaping), then field codes, then shell-style splitting."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.desktop"
        path.write_text(f"[Desktop Entry]\nType=Application\nName=T\nExec={value}\n", encoding="utf-8")
        keyfile = GLib.KeyFile()
        keyfile.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        line = keyfile.get_string("Desktop Entry", "Exec").replace("%%", "%")
    ok, argv = GLib.shell_parse_argv(line)
    assert ok
    return argv


class DesktopQuoteTests(unittest.TestCase):
    def round_trip(self, args):
        value = exec_value(args)
        self.assertEqual(parse_exec(value), args)
        return value

    def test_plain_path_is_unquoted(self):
        self.assertEqual(self.round_trip(["/usr/bin/kingfisher", "--background"]),
                         "/usr/bin/kingfisher --background")

    def test_path_with_spaces_uses_double_quotes(self):
        value = self.round_trip(["/opt/my apps/kingfisher", "--background"])
        self.assertTrue(value.startswith('"'))
        self.assertNotIn("'", value)

    def test_special_characters(self):
        self.round_trip(['/opt/odd "dir"/$HOME`x`\\bin', "--background"])
        self.round_trip(["/opt/100% sure/kingfisher", "--background"])

    def test_quote_alone_still_quotes_spaces(self):
        self.assertEqual(_desktop_quote("a b"), '"a b"')


class SnapTests(unittest.TestCase):
    def test_snap_entry_only_carries_arguments_for_snapd(self):
        # snapd starts the app's own command at login and keeps only the
        # arguments, so the entry must not point into a revision folder.
        with mock.patch.object(autostart, "IS_SNAP", True), \
                mock.patch.dict("os.environ", {"KINGFISHER_LAUNCHER": "/snap/kingfisher/x4/bin/kingfisher"}):
            self.assertEqual(parse_exec(autostart._command()), ["kingfisher", "--background"])


if __name__ == "__main__":
    unittest.main()
