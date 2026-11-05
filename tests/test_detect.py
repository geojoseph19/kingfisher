# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from kingfisher.detect import find_entities


def kinds(text):
    return [(e.kind, e.text) for e in find_entities(text)]


class LinkTests(unittest.TestCase):
    def test_links_with_and_without_scheme(self):
        found = kinds("Docs: https://example.org/a?b=1, mirror www.kernel.org and help.ubuntu.com/lts.")
        self.assertEqual(found, [("url", "https://example.org/a?b=1"),
                                 ("url", "www.kernel.org"),
                                 ("url", "help.ubuntu.com/lts")])

    def test_bare_link_gets_https(self):
        self.assertEqual(find_entities("see example.com")[0].uri, "https://example.com")

    def test_brackets_and_trailing_punctuation(self):
        found = kinds("(see https://en.wikipedia.org/wiki/Kingfisher_(bird)).")
        self.assertEqual(found, [("url", "https://en.wikipedia.org/wiki/Kingfisher_(bird)")])

    def test_versions_and_numbers_are_not_links(self):
        self.assertEqual(kinds("Python 3.10.12 on 192.168.0.1, total 42.50"), [])

    def test_file_names_are_not_links(self):
        self.assertEqual(kinds("open report.pdf and main.py"), [])


class EmailTests(unittest.TestCase):
    def test_email(self):
        found = find_entities("Contact support@example.co.uk today")
        self.assertEqual([(e.kind, e.uri) for e in found], [("email", "mailto:support@example.co.uk")])

    def test_email_domain_is_not_also_a_link(self):
        self.assertEqual([e.kind for e in find_entities("mail team@kernel.org")], ["email"])


class PhoneTests(unittest.TestCase):
    def test_international_and_local_formats(self):
        text = "Call +1 (555) 123-4567, +44 20 7946 0958, 98765 43210 or 5551234567."
        self.assertEqual([e.text for e in find_entities(text) if e.kind == "phone"],
                         ["+1 (555) 123-4567", "+44 20 7946 0958", "98765 43210", "5551234567"])

    def test_tel_uri_keeps_plus(self):
        self.assertEqual(find_entities("+91 98765 43210")[0].uri, "tel:+919876543210")

    def test_not_phone_numbers(self):
        for text in ("Total 12.50 13.75 EUR", "Released 2019-2024", "on 2024-03-15",
                     "dated 15.03.2024", "ID 1234567", "Version 1.2.3.4"):
            with self.subTest(text=text):
                self.assertEqual([e for e in find_entities(text) if e.kind == "phone"], [])


class GeneralTests(unittest.TestCase):
    def test_reading_order_and_duplicates(self):
        text = "a@b.io then https://x.dev and again a@b.io"
        self.assertEqual(kinds(text), [("email", "a@b.io"), ("url", "https://x.dev")])

    def test_empty(self):
        self.assertEqual(find_entities(""), [])


if __name__ == "__main__":
    unittest.main()
