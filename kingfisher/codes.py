# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""QR code and barcode reading, offline, through the ZBar library.

ZBar is optional: without ``python3-pyzbar`` the feature reports itself as
unavailable and the rest of the app works unchanged. Images are decoded in
memory and never written to disk.
"""

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageOps, ImageStat

PACKAGE = "python3-pyzbar"
# Extra passes cost time on every capture, so they only run where they help.
UPSCALE_BELOW = 800      # px, longest side: small crops may hold tiny codes
DARK_MEDIAN = 110        # mostly dark captures may hold light-on-dark codes

pyzbar = None            # loaded on first use; see _load()
_load_tried = False


def _load():
    """Import ZBar lazily (it is not needed to start the app)."""
    global pyzbar, _load_tried
    if pyzbar is None and not _load_tried:
        _load_tried = True
        try:
            import importlib
            importlib.invalidate_caches()
            pyzbar = importlib.import_module("pyzbar.pyzbar")
        except (ImportError, OSError):   # library missing or libzbar not installed
            pyzbar = None
    return pyzbar


def reload():
    """Try the import again, e.g. right after the package was installed."""
    global _load_tried
    _load_tried = False
    return _load() is not None

# Friendly names for the symbologies ZBar reports.
KIND_NAMES = {
    "QRCODE": "QR code",
    "EAN13": "EAN-13",
    "EAN8": "EAN-8",
    "UPCA": "UPC-A",
    "UPCE": "UPC-E",
    "ISBN10": "ISBN-10",
    "ISBN13": "ISBN-13",
    "CODE128": "Code 128",
    "CODE93": "Code 93",
    "CODE39": "Code 39",
    "CODABAR": "Codabar",
    "I25": "Interleaved 2 of 5",
    "PDF417": "PDF417",
    "DATABAR": "GS1 DataBar",
    "DATABAR_EXP": "GS1 DataBar Expanded",
}

MAX_CODES = 20


@dataclass(frozen=True)
class Code:
    kind: str       # friendly name, e.g. "QR code"
    data: str       # decoded payload
    left: int
    top: int
    width: int
    height: int



QR_CODE = KIND_NAMES["QRCODE"]


def icon_name(kind):
    """Symbolic icon for a code kind as shown to people ("QR code", "EAN-13" …)."""
    return "kingfisher-qr-symbolic" if kind == QR_CODE else "kingfisher-barcode-symbolic"


def available():
    return _load() is not None


def read_codes(image):
    """Return every QR code and barcode found in a PIL image.

    One pass usually suffices. A 2x upscaled pass runs only for small crops
    (where codes are tiny), and an inverted pass only for mostly dark
    captures (light-on-dark codes), so ordinary captures pay for one pass."""
    if _load() is None:
        return []
    gray = image.convert("L")
    codes = _decode(gray, 1)
    if not codes and max(gray.size) < UPSCALE_BELOW:
        big = gray.resize((gray.width * 2, gray.height * 2), Image.NEAREST)
        codes = _decode(big, 2)
    if not codes and _mostly_dark(gray):
        codes = _decode(ImageOps.invert(gray), 1)
    return codes[:MAX_CODES]


def _mostly_dark(gray):
    small = gray.copy()
    small.thumbnail((64, 64))
    return ImageStat.Stat(small).median[0] < DARK_MEDIAN


def mask_codes(image, codes, padding=6):
    """Copy of ``image`` with code areas painted white, so OCR does not
    read the code pattern as stray characters."""
    if not codes:
        return image
    masked = image.copy()
    draw = ImageDraw.Draw(masked)
    for code in codes:
        draw.rectangle(
            (code.left - padding, code.top - padding,
             code.left + code.width + padding, code.top + code.height + padding),
            fill=(255, 255, 255) if masked.mode == "RGB" else 255)
    return masked


def _decode(gray, scale):
    found, seen = [], set()
    for symbol in pyzbar.decode(gray):
        kind = KIND_NAMES.get(symbol.type, symbol.type.title())
        data = symbol.data.decode("utf-8", errors="replace").strip()
        if not data or (kind, data) in seen:
            continue
        seen.add((kind, data))
        rect = symbol.rect
        found.append(Code(kind, data, rect.left // scale, rect.top // scale,
                          rect.width // scale, rect.height // scale))
    return found
