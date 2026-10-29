"""Cycle M3 (Muse): facets must not swallow trailing prose punctuation.

The URL pattern is a greedy \\S+ match, so in "…https://example.com/。"
the Japanese full stop became part of the facet URI: a broken link,
and a facet value the PDS may reject as an invalid URI. Current
production templates end the line right after the URL, so this is a
predicted bug for the first template edit that adds punctuation.
"""
import unittest

from bot.template import build_link_facets, clean_url


def facet_uri_and_slice(text):
    facets = build_link_facets(text)
    assert len(facets) == 1, facets
    facet = facets[0]
    idx = facet["index"]
    sliced = text.encode("utf-8")[idx["byteStart"]:idx["byteEnd"]].decode("utf-8")
    return facet["features"][0]["uri"], sliced


class CleanUrlTests(unittest.TestCase):
    def test_japanese_full_stop_trimmed(self):
        self.assertEqual(clean_url("https://example.com/path。"),
                         "https://example.com/path")

    def test_ascii_punctuation_trimmed(self):
        self.assertEqual(clean_url("https://example.com/a)."),
                         "https://example.com/a")
        self.assertEqual(clean_url("https://example.com/a!"),
                         "https://example.com/a")

    def test_balanced_brackets_kept(self):
        self.assertEqual(clean_url("https://example.com/a_(b)"),
                         "https://example.com/a_(b)")

    def test_fullwidth_closing_bracket_trimmed(self):
        self.assertEqual(clean_url("https://example.com/a）"),
                         "https://example.com/a")


class FacetTrimTests(unittest.TestCase):
    def test_facet_excludes_japanese_punctuation(self):
        text = "詳しくはhttps://example.com/path。見てね"
        uri, sliced = facet_uri_and_slice(text)
        self.assertEqual(uri, "https://example.com/path")
        self.assertEqual(sliced, uri)

    def test_facet_inside_japanese_brackets(self):
        text = "（https://example.com/a）を見て"
        uri, sliced = facet_uri_and_slice(text)
        self.assertEqual(uri, "https://example.com/a")
        self.assertEqual(sliced, uri)

    def test_production_url_unchanged(self):
        text = "こんにちは！☀️\nサイトにてミニゲーム開発中！\nhttps://minigame-b1qp.onrender.com/"
        uri, sliced = facet_uri_and_slice(text)
        self.assertEqual(uri, "https://minigame-b1qp.onrender.com/")
        self.assertEqual(sliced, uri)

    def test_two_urls_both_trimmed(self):
        text = "https://a.example/1、 https://b.example/2。"
        facets = build_link_facets(text)
        self.assertEqual([f["features"][0]["uri"] for f in facets],
                         ["https://a.example/1", "https://b.example/2"])
        encoded = text.encode("utf-8")
        for facet in facets:
            idx = facet["index"]
            self.assertEqual(
                encoded[idx["byteStart"]:idx["byteEnd"]].decode("utf-8"),
                facet["features"][0]["uri"])

    def test_bare_scheme_is_not_a_facet(self):
        self.assertEqual(build_link_facets("https://。"), [])


if __name__ == "__main__":
    unittest.main()
