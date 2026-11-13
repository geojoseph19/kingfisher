# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from PIL import Image, ImageDraw  # noqa: E402

from kingfisher import application  # noqa: E402
from kingfisher.ocr import to_rgb  # noqa: E402


def drawing(mode, background, ink):
    image = Image.new(mode, (60, 40), background)
    ImageDraw.Draw(image).rectangle((10, 10, 30, 30), fill=ink)
    return image


class TransparencyTests(unittest.TestCase):
    """Images from files and the clipboard may be transparent. A plain
    conversion paints transparent areas black, which hides dark text."""

    def test_dark_content_gets_a_white_background(self):
        flat = to_rgb(drawing("RGBA", (0, 0, 0, 0), (0, 0, 0, 255)))
        self.assertEqual(flat.mode, "RGB")
        self.assertEqual(flat.getpixel((50, 5)), (255, 255, 255))
        self.assertEqual(flat.getpixel((20, 20)), (0, 0, 0))

    def test_light_content_gets_a_dark_background(self):
        flat = to_rgb(drawing("RGBA", (255, 255, 255, 0), (250, 250, 250, 255)))
        self.assertLess(max(flat.getpixel((50, 5))), 60)
        self.assertEqual(flat.getpixel((20, 20)), (250, 250, 250))

    def test_grey_with_alpha(self):
        flat = to_rgb(drawing("LA", (0, 0), (10, 255)))
        self.assertEqual(flat.getpixel((50, 5)), (255, 255, 255))

    def test_palette_image_with_a_transparent_colour(self):
        image = Image.new("P", (60, 40), 0)
        image.putpalette([0, 0, 0, 30, 30, 30] + [0] * 762)
        ImageDraw.Draw(image).rectangle((10, 10, 30, 30), fill=1)
        image.info["transparency"] = 0
        flat = to_rgb(image)
        self.assertEqual(flat.getpixel((50, 5)), (255, 255, 255))
        self.assertEqual(flat.getpixel((20, 20)), (30, 30, 30))

    def test_fully_transparent_image_does_not_fail(self):
        self.assertEqual(to_rgb(Image.new("RGBA", (20, 20), (0, 0, 0, 0))).getpixel((5, 5)), (255, 255, 255))

    def test_opaque_images_are_only_converted(self):
        self.assertEqual(to_rgb(Image.new("RGBA", (8, 8), (9, 8, 7, 255))).getpixel((1, 1)), (9, 8, 7))
        for mode in ("RGB", "L", "1", "P", "CMYK", "I", "F"):
            with self.subTest(mode=mode):
                self.assertEqual(to_rgb(Image.new(mode, (8, 8))).mode, "RGB")


class LoadImageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_transparent_png_is_flattened(self):
        path = self.dir / "logo.png"
        drawing("RGBA", (0, 0, 0, 0), (0, 0, 0, 255)).save(path)
        image = application.load_image(path)
        self.assertEqual((image.mode, image.getpixel((50, 5))), ("RGB", (255, 255, 255)))

    def test_damaged_orientation_data_does_not_stop_the_file(self):
        path = self.dir / "photo.png"
        Image.new("RGB", (30, 20), "white").save(path)
        with mock.patch.object(application.ImageOps, "exif_transpose", side_effect=ZeroDivisionError):
            self.assertEqual(application.load_image(path).size, (30, 20))


class OpenFileTests(unittest.TestCase):
    """Whatever is wrong with a file, the user is told; nothing fails silently."""

    def open(self, path, **patches):
        app = mock.Mock()
        with mock.patch.multiple(application, **patches) if patches else mock.patch.object(application, "IS_SNAP", False):
            application.KingfisherApplication.ocr_file(app, path)
        return app, app.get_window.return_value

    def test_unexpected_failure_is_reported(self):
        for error in (SyntaxError("broken PNG file"), IndexError(), MemoryError(), OSError("truncated")):
            with self.subTest(error=type(error).__name__):
                app, window = self.open("/tmp/x.png", load_image=mock.Mock(side_effect=error))
                window.toast.assert_called_once()
                app.run_ocr.assert_not_called()

    def test_file_that_is_not_on_this_computer(self):
        app, window = self.open(None)
        window.toast.assert_called_once()
        app.run_ocr.assert_not_called()

    def test_readable_file_is_read(self):
        image = Image.new("RGB", (4, 4))
        app, window = self.open("/tmp/x.png", load_image=mock.Mock(return_value=image))
        app.run_ocr.assert_called_once_with(image, "file")
        window.toast.assert_not_called()


if __name__ == "__main__":
    unittest.main()
