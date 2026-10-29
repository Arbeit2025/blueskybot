"""Pure logic for the daily fixed post: rendering, length check, facets.

No network, no credentials. Everything here is unit-testable.

Why the date placeholder matters:
Posting the exact same text every day looks like spam to readers (and
to moderation systems), even though Bluesky itself does not reject
duplicates. The fixed message therefore keeps its core text, but the
template embeds {date} / {weekday_ja} so each day's post is unique.

Bluesky's limit is 300 grapheme clusters. Python's len() counts code
points, which is >= the grapheme count for ordinary Japanese/emoji
text, so validating with len() is a safe (slightly strict) check.
"""
from __future__ import annotations

import re
from string import Formatter
from datetime import datetime

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]  # Monday = 0

_URL_RE = re.compile(r"https?://\S+")
MAX_LENGTH = 300

# Characters that prose puts right after a URL but that are never part
# of the address itself: sentence punctuation (ASCII and Japanese) and
# closing brackets. The greedy \S+ match above swallows them into the
# facet URI, producing a broken link (or an invalid facet the PDS
# rejects). Closing brackets are only trimmed while unbalanced, so a
# URL that legitimately contains "(...)" keeps its closing bracket.
_TRAILING_PUNCT = ".,;:!?…,;:、。！？・\"'）】」』〉》"
_CLOSERS = {")": "(", "]": "[", "}": "{"}
# Japanese sentence punctuation and brackets can also appear in the
# MIDDLE of a greedy match, when prose continues right after them
# with no whitespace ("…https://example.com/。見てね"). These
# characters are never part of a valid (ASCII) facet URI, so the
# match is cut at the first one.
_CJK_CUT = "。、！？・「」『』（）【】〈〉《》…‥"


def clean_url(raw: str) -> str:
    """Strip trailing prose punctuation from a matched URL."""
    url = raw
    cut = [url.find(ch) for ch in _CJK_CUT if ch in url]
    if cut:
        url = url[:min(cut)]
    while url:
        last = url[-1]
        if last in _CLOSERS:
            if url.count(_CLOSERS[last]) >= url.count(last):
                break
            url = url[:-1]
        elif last in _TRAILING_PUNCT:
            url = url[:-1]
        else:
            break
    return url


def render_template(template: str, now: datetime,
                    extra: dict | None = None) -> str:
    """Expand {date}, {date_ja}, {weekday_ja} (and any `extra` keys).

    `extra` carries e.g. {"slot_time": "10:00"} so that two slots on
    the same day that share a template still produce different texts.
    """
    values = {
        "date": now.strftime("%Y-%m-%d"),
        "date_ja": f"{now.year}年{now.month}月{now.day}日",
        "weekday_ja": WEEKDAYS_JA[now.weekday()],
    }
    if extra:
        values.update(extra)
    pieces = []
    for literal, field, spec, conversion in Formatter().parse(template):
        pieces.append(literal)
        if field is not None:
            if field not in values or spec or conversion:
                raise ValueError("unsupported template field or format")
            pieces.append(str(values[field]))
    return "".join(pieces)


def contains_url(text: str) -> bool:
    return bool(_URL_RE.search(text))


def text_length(text: str) -> int:
    """Conservative length: code points (>= grapheme clusters)."""
    return len(text)


def validate_post(text: str) -> list[str]:
    """Return a list of problems. Empty list means the post is postable."""
    problems: list[str] = []
    if not text.strip():
        problems.append("empty post")
    length = text_length(text)
    if length > MAX_LENGTH:
        problems.append(f"too long: length {length} > {MAX_LENGTH}")
    return problems


def build_link_facets(text: str) -> list[dict]:
    """Build Bluesky rich-text facets so URLs become clickable links.

    Facet indexes are byte offsets into the UTF-8 encoding of the text,
    not character offsets. Without facets, a URL in a post made through
    the API is displayed as plain, non-clickable text.
    """
    facets: list[dict] = []
    encoded = text.encode("utf-8")
    char_pos = 0
    byte_pos = 0
    for match in _URL_RE.finditer(text):
        byte_pos += len(text[char_pos:match.start()].encode("utf-8"))
        byte_start = byte_pos
        url = clean_url(match.group(0))
        if url in ("http://", "https://"):
            char_pos = match.end()
            byte_pos = byte_start + len(match.group(0).encode("utf-8"))
            continue
        byte_end = byte_start + len(url.encode("utf-8"))
        facets.append(
            {
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#link",
                        "uri": url,
                    }
                ],
            }
        )
        char_pos = match.end()
        byte_pos = byte_start + len(match.group(0).encode("utf-8"))
    # Reference to `encoded` documents the byte basis; offsets above are
    # computed incrementally and match len(encoded) slicing.
    assert facets == [] or facets[-1]["index"]["byteEnd"] <= len(encoded)
    return facets
