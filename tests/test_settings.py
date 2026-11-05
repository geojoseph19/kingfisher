# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from kingfisher import settings as settings_module
from kingfisher.settings import DEFAULTS, Settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self._orig = settings_module.CONFIG_DIR, settings_module.CONFIG_FILE
        settings_module.CONFIG_DIR = Path(self.dir.name)
        settings_module.CONFIG_FILE = Path(self.dir.name) / "settings.json"

    def tearDown(self):
        settings_module.CONFIG_DIR, settings_module.CONFIG_FILE = self._orig
        self.dir.cleanup()

    def write(self, data):
        settings_module.CONFIG_FILE.write_text(json.dumps(data))

    def test_damaged_files_fall_back_to_defaults(self):
        for content in ("[]", "null", "{not json", '"text"'):
            settings_module.CONFIG_FILE.write_text(content)
            self.assertEqual(Settings()["languages"], DEFAULTS["languages"])

    def test_invalid_values_are_ignored(self):
        self.write({"languages": [], "last_selection": ["a", 1, 2, 3], "enhance": 1,
                    "window_width": True, "layout": "sparse"})
        s = Settings()
        self.assertEqual(s["languages"], DEFAULTS["languages"])
        self.assertEqual(s["last_selection"], [])
        self.assertIs(s["enhance"], DEFAULTS["enhance"])
        self.assertEqual(s["window_width"], DEFAULTS["window_width"])
        self.assertEqual(s["layout"], "sparse")

    def test_changes_from_another_process_are_kept(self):
        running = Settings()                      # the app, started earlier
        cli = Settings()                          # e.g. kingfisher --enable-shortcut
        cli["shortcut_key"] = "<Control>Print"
        running["last_selection"] = [1, 2, 300, 200]
        fresh = Settings()
        self.assertEqual(fresh["shortcut_key"], "<Control>Print")
        self.assertEqual(fresh["last_selection"], [1, 2, 300, 200])
        self.assertEqual(running["shortcut_key"], "<Control>Print")

    def test_file_is_private(self):
        Settings()["layout"] = "block"
        mode = stat.S_IMODE(os.stat(settings_module.CONFIG_FILE).st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
