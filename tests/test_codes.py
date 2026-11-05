# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from PIL import Image, ImageOps

from kingfisher import codes

try:
    import qrcode
except ImportError:
    qrcode = None


@unittest.skipUnless(codes.available(), "python3-pyzbar is not installed")
class CodeTests(unittest.TestCase):
    def qr(self, data, box=10):
        return qrcode.make(data, box_size=box).get_image().convert("RGB")

    @unittest.skipUnless(qrcode, "python3-qrcode is not installed")
    def test_qr_in_screenshot(self):
        page = Image.new("RGB", (900, 500), "white")
        page.paste(self.qr("https://example.org/kingfisher"), (500, 80))
        found = codes.read_codes(page)
        self.assertEqual([(c.kind, c.data) for c in found], [("QR code", "https://example.org/kingfisher")])
        self.assertTrue(found[0].left >= 500 and found[0].top >= 80)

    @unittest.skipUnless(qrcode, "python3-qrcode is not installed")
    def test_small_qr_needs_upscale(self):
        page = Image.new("RGB", (400, 300), "white")
        page.paste(self.qr("TINY-42", box=1), (20, 20))
        self.assertEqual([c.data for c in codes.read_codes(page)], ["TINY-42"])

    @unittest.skipUnless(qrcode, "python3-qrcode is not installed")
    def test_dark_mode_qr(self):
        inverted = ImageOps.invert(self.qr("dark-mode"))
        self.assertEqual([c.data for c in codes.read_codes(inverted)], ["dark-mode"])

    def test_ean13_barcode(self):
        found = codes.read_codes(draw_ean13("4006381333931"))
        self.assertEqual([(c.kind, c.data) for c in found], [("EAN-13", "4006381333931")])

    @unittest.skipUnless(qrcode, "python3-qrcode is not installed")
    def test_mask_paints_codes_white(self):
        page = Image.new("RGB", (600, 400), "white")
        page.paste(self.qr("mask-me"), (100, 50))
        masked = codes.mask_codes(page, codes.read_codes(page))
        self.assertEqual(masked.convert("L").getextrema(), (255, 255))

    def test_nothing_found(self):
        self.assertEqual(codes.read_codes(Image.new("RGB", (200, 100), "white")), [])


# Minimal EAN-13 renderer for tests (no barcode library needed).
_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def draw_ean13(number, module=3, height=120):
    d = [int(c) for c in number]
    bits = "101"
    for i, p in enumerate(_PARITY[d[0]]):
        bits += (_L if p == "L" else _G)[d[i + 1]]
    bits += "01010"
    for n in d[7:]:
        bits += _R[n]
    bits += "101"
    quiet = 12 * module
    img = Image.new("L", (len(bits) * module + 2 * quiet, height + 2 * quiet), 255)
    for i, b in enumerate(bits):
        if b == "1":
            img.paste(0, (quiet + i * module, quiet, quiet + (i + 1) * module, quiet + height))
    return img.convert("RGB")


if __name__ == "__main__":
    unittest.main()
