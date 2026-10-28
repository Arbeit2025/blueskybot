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
from datetime import datetime

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]  # Monday = 0

_URL_RE = re.compile(r"https?://\S+")
MAX_LENGTH = 300


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
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


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
        byte_end = byte_start + len(match.group(0).encode("utf-8"))
        facets.append(
            {
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [
                    {
                        "$type": "app.bsky.richtext.facet#link",
                        "uri": match.group(0),
                    }
                ],
            }
        )
        char_pos = match.end()
        byte_pos = byte_end
    # Reference to `encoded` documents the byte basis; offsets above are
    # computed incrementally and match len(encoded) slicing.
    assert facets == [] or facets[-1]["index"]["byteEnd"] <= len(encoded)
    return facets
