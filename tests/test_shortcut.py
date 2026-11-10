# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest import mock

from kingfisher import shortcut


class SandboxTests(unittest.TestCase):
    def test_snap_cannot_set_shortcuts(self):
        with mock.patch.object(shortcut, "IS_SNAP", True):
            self.assertFalse(shortcut.supported())

    def test_snap_unavailable_reason_explains_the_manual_setup(self):
        with mock.patch.object(shortcut, "IS_SNAP", True):
            reason = shortcut.unavailable_reason()
            self.assertIn("Custom Shortcuts", reason)
            self.assertIn("kingfisher --capture", reason)

    def test_snap_enable_raises(self):
        with mock.patch.object(shortcut, "IS_SNAP", True), self.assertRaises(RuntimeError):
            shortcut.enable()


if __name__ == "__main__":
    unittest.main()
