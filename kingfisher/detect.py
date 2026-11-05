# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Detect links, email addresses and phone numbers in recognised text.

Detection is deliberately conservative: a false "phone number" on a price or
a date is worse than a missed one, because the user can always select and
copy text by hand.
"""

import re
from dataclasses import dataclass

MAX_ITEMS = 20

# Top-level domains accepted for bare links such as "example.org/path".
# Links with a scheme or a "www." prefix are accepted with any TLD.
_COMMON_TLDS = (
    "com|org|net|io|dev|app|ai|co|edu|gov|info|biz|me|tv|us|uk|in|de|fr|es|it|nl|"
    "eu|ca|au|jp|cn|ru|br|ch|se|no|fi|dk|pl|be|at|ie|nz|sg|hk|kr|tw|za|mx|ar|cl|"
    "pt|gr|cz|ro|hu|il|ae|sa|tr|ua|id|my|th|vn|ph|pk|bd|lk|np|cloud|tech|site|xyz"
)

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*\.[A-Za-z]{2,24}(?![\w-])")

_URL = re.compile(
    r"(?<![\w@/.-])"
    r"(?:"
    r"(?:https?|ftp)://[^\s<>\"'`]+"                                   # with scheme
    r"|www\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:[/?#][^\s<>\"'`]*)?"   # www.
    rf"|[A-Za-z0-9](?:[A-Za-z0-9-]{{0,62}}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]+)*\.(?:{_COMMON_TLDS})"
    r"(?:[/?#][^\s<>\"'`]*)?(?![\w@-])"                                # bare domain
    r")",
    re.IGNORECASE)

# Optional "+" and country code, then groups of digits split by spaces,
# dots, dashes or brackets.
_PHONE = re.compile(r"(?<![\w+.-])(\+?\(?\d[\d\s().-]{6,22}\d)(?![\w.-]?\d)")

_TRAILING = ".,;:!?)]}'\"»”’"


@dataclass(frozen=True)
class Entity:
    kind: str   # "url" | "email" | "phone"
    text: str   # as it appears in the text
    uri: str    # what to open: https://…, mailto:…, tel:…


def find_entities(text):
    """Return links, emails and phone numbers in reading order, without
    duplicates."""
    if not text:
        return []
    found = []  # (start, Entity)
    taken = []  # character spans already claimed

    def claim(start, end):
        if any(s < end and start < e for s, e in taken):
            return False
        taken.append((start, end))
        return True

    for m in _EMAIL.finditer(text):
        if claim(m.start(), m.end()):
            found.append((m.start(), Entity("email", m.group(0), "mailto:" + m.group(0))))

    for m in _URL.finditer(text):
        raw = _strip_trailing(m.group(0))
        if not raw or "@" in raw or _looks_like_number(raw):
            continue
        if claim(m.start(), m.start() + len(raw)):
            uri = raw if re.match(r"(?i)(?:https?|ftp)://", raw) else "https://" + raw
            found.append((m.start(), Entity("url", raw, uri)))

    for m in _PHONE.finditer(text):
        raw = m.group(1).strip()
        if _is_phone(raw) and claim(m.start(1), m.start(1) + len(raw)):
            digits = re.sub(r"\D", "", raw)
            found.append((m.start(1), Entity("phone", raw, "tel:" + ("+" if raw.startswith("+") else "") + digits)))

    found.sort(key=lambda item: item[0])
    unique, seen = [], set()
    for _start, entity in found:
        key = (entity.kind, entity.uri.lower())
        if key not in seen:
            seen.add(key)
            unique.append(entity)
    return unique[:MAX_ITEMS]


def _strip_trailing(value):
    # Drop sentence punctuation, but keep a closing bracket that has an
    # opening partner inside the link (e.g. Wikipedia URLs).
    while value and value[-1] in _TRAILING:
        if value[-1] == ")" and value.count("(") >= value.count(")"):
            break
        value = value[:-1]
    return value


def _looks_like_number(value):
    # "3.14", "v1.2.3" or "192.168.0.1" are not links.
    return bool(re.fullmatch(r"[\dv.]+", value, re.IGNORECASE))


def _is_phone(raw):
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        return False
    if raw.count("(") != raw.count(")"):
        return False
    # Dates (2024-03-15, 15.03.2024) and decimals are not phone numbers.
    if re.fullmatch(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{2,4}", raw):
        return False
    # Prices and decimals ("12.50 13.75") are not phone numbers.
    if re.search(r"\d\.\d{1,2}(?!\d)", raw):
        return False
    groups = [g for g in re.split(r"[\s().-]+", raw.lstrip("+")) if g]
    # Year ranges and lists of years ("2019-2024").
    if all(len(g) == 4 and g[:2] in ("19", "20") for g in groups):
        return False
    # Require phone-like structure: a leading "+", or 2-5 digit groups of
    # 2-5 digits each, or a plain run of 10+ digits.
    if raw.startswith("+"):
        return True
    if 2 <= len(groups) <= 5 and all(2 <= len(g) <= 5 for g in groups):
        return True
    return len(groups) == 1 and len(digits) >= 10
