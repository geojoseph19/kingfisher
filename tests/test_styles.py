# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""The color variables for newer libadwaita must match the named colors."""

import re
import unittest
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "kingfisher" / "data"
DEFINE = re.compile(r"@define-color\s+([\w-]+)\s+([^;]+);")
VARIABLE = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")


def named_colors(name):
    colors = dict(DEFINE.findall((DATA / name).read_text(encoding="utf-8")))

    def resolve(value):
        value = value.strip()
        return resolve(colors[value[1:]]) if value.startswith("@") else value.lower()
    return {key: resolve(value) for key, value in colors.items()}


def variables(name):
    return {key.replace("-", "_"): value.strip().lower()
            for key, value in VARIABLE.findall((DATA / name).read_text(encoding="utf-8"))}


class ColorVariablesTest(unittest.TestCase):
    def check(self, styles, mirror):
        named = named_colors(styles)
        mirrored = variables(mirror)
        self.assertTrue(mirrored)
        for key, value in mirrored.items():
            with self.subTest(color=key):
                self.assertEqual(value, named[key])
        # Every libadwaita color the stylesheet overrides has a variable too.
        overridden = {key for key in named if not key.startswith("kf_")}
        self.assertEqual(overridden - set(mirrored), set())

    def test_light(self):
        self.check("style.css", "colors.css")

    def test_dark(self):
        self.check("style-dark.css", "colors-dark.css")


if __name__ == "__main__":
    unittest.main()
