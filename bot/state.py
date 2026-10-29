"""Same-day duplicate guard backed by a small JSON state file.

State format: {"posted": {"2026-10-29:greeting_morning": "<hash>"}}
Note: on GitHub Actions the filesystem is ephemeral, so this guard is a
local/second-layer defence. Scheduled catch-up runs rely on the
server-side duplicate check in bsky_client and workflow concurrency.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Posted-slot entries older than this many days (relative to the
# entry being recorded) are pruned on save. The duplicate guard only
# ever looks up today's key, so keeping history forever only grows
# the file on long-lived local installs. 7 days keeps a useful audit
# trail while bounding the size; unparsable keys are never deleted.
STATE_RETENTION_DAYS = 7


class StateError(OSError):
    """State is unreadable, malformed, or cannot be safely saved."""


def validate_state(state: object) -> dict:
    if not isinstance(state, dict):
        raise StateError("state must be a JSON object")
    posted = state.get("posted", {})
    if not isinstance(posted, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in posted.items()
    ):
        raise StateError("state.posted must map string keys to string hashes")
    return state


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_state(path: Path) -> dict:
    try:
        return validate_state(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeError, OSError) as exc:
        raise StateError(f"cannot load state: {exc}") from exc


def prepare_state_path(path: Path) -> None:
    """Probe the directory before sending a post; leave the state untouched."""
    try:
        if path.exists() and not path.is_file():
            raise StateError("state path is not a regular file")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path.parent) as probe:
            probe.write(b"{}\n")
            probe.flush()
            os.fsync(probe.fileno())
    except OSError as exc:
        raise StateError(f"state storage is not writable: {exc}") from exc


def atomic_write_state(path: Path, payload: dict) -> None:
    validate_state(payload)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise StateError(f"cannot save state: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def already_posted_on(state: dict, date_str: str) -> bool:
    return state.get("last_post_date") == date_str


def record_post(path: Path, date_str: str, text: str) -> None:
    payload = {"last_post_date": date_str, "last_text_hash": text_hash(text)}
    atomic_write_state(path, payload)


# --- Per-slot guard (the bot posts several times a day) ----------------
# State format: {"posted": {"2026-10-29:greeting_morning": "<hash>", ...}}

def slot_key(date_str: str, slot_id: str) -> str:
    return f"{date_str}:{slot_id}"


def already_posted_slot(state: dict, date_str: str, slot_id: str,
                        text: str | None = None) -> bool:
    """Whether the state file proves this slot was posted.

    With `text`, the stored hash must match the text about to be sent.
    A bare key hit only proves *something* was posted under this slot:
    if the template changed after that post, trusting the key would
    silently skip the corrected text, so callers that know the text
    pass it and let the server-side record-key check arbitrate.
    Without `text` the legacy key-only check is kept for other callers.
    """
    posted = state.get("posted", {})
    key = slot_key(date_str, slot_id)
    if key not in posted:
        return False
    if text is None:
        return True
    return posted[key] == text_hash(text)


def _prune_posted(posted: dict, current_date_str: str) -> dict:
    try:
        current = datetime.strptime(current_date_str, "%Y-%m-%d").date()
    except ValueError:
        return posted
    cutoff = current - timedelta(days=STATE_RETENTION_DAYS)
    kept = {}
    for key, value in posted.items():
        date_part = key.split(":", 1)[0]
        try:
            entry_date = datetime.strptime(date_part, "%Y-%m-%d").date()
        except ValueError:
            kept[key] = value  # unknown format: never delete blindly
            continue
        if entry_date >= cutoff:
            kept[key] = value
    return kept


def record_post_slot(path: Path, state: dict, date_str: str, slot_id: str,
                     text: str) -> dict:
    posted = _prune_posted(dict(state.get("posted", {})), date_str)
    posted[slot_key(date_str, slot_id)] = text_hash(text)
    payload = {"posted": posted}
    atomic_write_state(path, payload)
    return payload
