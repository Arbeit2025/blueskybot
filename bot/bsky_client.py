"""Minimal Bluesky (AT Protocol) client, stdlib only.

Authentication uses an *app password* (Settings -> Privacy and
Security -> App Passwords on Bluesky), never the account's main
password. An app password can be revoked on its own if it leaks.

Flow (XRPC over HTTPS):
  1. POST /xrpc/com.atproto.server.createSession  {identifier, password}
     -> {accessJwt, did, handle}
  2. GET  /xrpc/com.atproto.repo.listRecords      (duplicate pre-check)
     -> recent app.bsky.feed.post records from the account's own repo
  3. POST /xrpc/com.atproto.repo.createRecord     (Bearer accessJwt)
     {repo: did, collection: "app.bsky.feed.post", record: {...}}
     -> {uri, cid}

Posting is hardened against the failure modes of scheduled CI runs:

- Duplicate guard (server side): before posting, the account's recent
  records are checked for the same text created inside the slot's
  time window today. A re-run in a fresh environment (where the local
  state file is gone) therefore does not post the same slot twice.
  The window (30 min before to 120 min after the slot time) keeps
  apart slots that share an identical text hours apart.
  Failed lookups stop posting until a later scheduled run can verify
  the slot, and records are requested newest first.
- Response validation: a createRecord response only counts as a
  success when it carries a well-formed uri (at://...) and cid. A
  malformed "success" is verified against the repo before being
  believed or rejected.
- Retry: transient failures (network errors, HTTP 429, HTTP 5xx) are
  retried up to 3 attempts with backoff. Before every retry the repo
  is re-checked, because the previous attempt may have landed even
  though its response was lost. Client errors (other HTTP 4xx) fail
  immediately.

The PDS base URL defaults to https://bsky.social and can be overridden
with BLUESKY_PDS_URL for accounts hosted on another PDS.

Network is touched only inside the request helpers. The record
building and the duplicate matching are pure functions so they can be
unit-tested.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_PDS_URL = "https://bsky.social"
POST_COLLECTION = "app.bsky.feed.post"
MAX_ATTEMPTS = 3
WINDOW_BEFORE = timedelta(minutes=30)
WINDOW_AFTER = timedelta(minutes=120)
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_DID_RE = re.compile(r"did:[a-z0-9]+:[^\s/?#]+")
_RKEY_RE = re.compile(r"[A-Za-z0-9._:~-]{1,512}")
_TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"


class BskyError(Exception):
    """Bluesky request failure. `transient` marks errors worth retrying."""

    def __init__(self, message: str, *, status: int | None = None,
                 transient: bool = False, error_code: str | None = None):
        super().__init__(message)
        self.status = status
        self.transient = transient
        self.error_code = error_code


def is_post_uri(uri: object, expected_did: str | None = None,
                expected_rkey: str | None = None) -> bool:
    if not isinstance(uri, str) or not uri.startswith("at://"):
        return False
    parts = uri[5:].split("/")
    if len(parts) != 3:
        return False
    did, collection, rkey = parts
    return bool(_DID_RE.fullmatch(did) and collection == POST_COLLECTION
                and _RKEY_RE.fullmatch(rkey) and rkey not in (".", "..")
                and (expected_did is None or did == expected_did)
                and (expected_rkey is None or rkey == expected_rkey))


def slot_record_key(slot_date: date, slot_time: time, tz: ZoneInfo,
                    slot_id: str) -> str:
    """Stable, syntactically valid TID for a configured date/time/slot.

    Config validation forbids duplicate times. The timestamp separates
    configured slots; the deterministic clock bits also include the ID.
    Record createdAt remains the actual sending time, not this key's time.
    """
    scheduled = datetime.combine(slot_date, slot_time, tzinfo=tz)
    microseconds = int(scheduled.timestamp()) * 1_000_000
    if not 0 <= microseconds < (1 << 53):
        raise BskyError("scheduled date cannot be represented by a TID")
    clock = int.from_bytes(hashlib.sha256(slot_id.encode("utf-8")).digest()[:2], "big") & 1023
    value = (microseconds << 10) | clock
    return "".join(_TID_ALPHABET[(value >> shift) & 31] for shift in range(60, -1, -5))


def _get_slot_record(root: str, session: dict, rkey: str, text: str, *, sleep) -> str | None:
    query = urllib.parse.urlencode({"repo": session["did"], "collection": POST_COLLECTION, "rkey": rkey})
    try:
        result = _with_retry(lambda: _get_json(f"{root}/xrpc/com.atproto.repo.getRecord?{query}",
                                               token=session["accessJwt"]), sleep=sleep)
    except BskyError as exc:
        if exc.status in (400, 404) and exc.error_code == "RecordNotFound":
            return None
        raise
    value = result.get("value")
    if not is_post_uri(result.get("uri"), session["did"], rkey) or not isinstance(value, dict) \
            or value.get("$type") != POST_COLLECTION or not isinstance(value.get("createdAt"), str):
        raise BskyError("getRecord returned a malformed slot record")
    if value.get("text") != text:
        raise BskyError("slot key contains a different post; refusing to overwrite", error_code="SlotConflict")
    try:
        if datetime.fromisoformat(value["createdAt"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("timezone missing")
    except ValueError as exc:
        raise BskyError("slot record has an invalid timestamp") from exc
    return result["uri"]


def pds_url() -> str:
    return os.environ.get("BLUESKY_PDS_URL", DEFAULT_PDS_URL).rstrip("/")


def format_created_at(now: datetime | None = None) -> str:
    """AT Protocol timestamps: UTC ISO-8601 with a trailing 'Z'."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_post_record(text: str, facets: list[dict] | None = None,
                      now: datetime | None = None) -> dict:
    """Build the app.bsky.feed.post record for `text` (pure)."""
    record: dict = {
        "$type": POST_COLLECTION,
        "text": text,
        "langs": ["ja"],
        "createdAt": format_created_at(now),
    }
    if facets:
        record["facets"] = facets
    return record


def find_posted_uri(records: list[dict], text: str, *, slot_date: date,
                    slot_time: time, tz: ZoneInfo) -> str | None:
    """URI of an existing record for this slot, or None (pure).

    A record counts as this slot's post when its text matches exactly,
    its local time is inside the slot window (WINDOW_BEFORE before to WINDOW_AFTER
    after the slot time). The window is what tells apart the two
    daily slots that share an identical text (10:00 and 16:00).
    """
    slot_dt = datetime.combine(slot_date, slot_time, tzinfo=tz)
    window_start = slot_dt - WINDOW_BEFORE
    window_end = slot_dt + WINDOW_AFTER
    for item in records:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        if value.get("text") != text:
            continue
        created = value.get("createdAt")
        if not isinstance(created, str):
            continue
        try:
            moment = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            continue
        local = moment.astimezone(tz)
        if window_start <= local <= window_end:
            uri = item.get("uri")
            if is_post_uri(uri):
                return uri
    return None


def _is_transient_status(status: int) -> bool:
    return status == 429 or status >= 500


def _request_json(method: str, url: str, payload: dict | None = None,
                  token: str | None = None) -> dict:
    """One HTTP request. Raises BskyError on any failure."""
    data = None
    request = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise BskyError("API response exceeds size limit")
        try:
            raw = body.decode("utf-8")
        except UnicodeError as exc:
            raise BskyError("API response is not valid UTF-8") from exc
    except urllib.error.HTTPError as exc:
        error_code = None
        try:
            error_body = json.loads(exc.read(4096).decode("utf-8"))
            candidate = error_body.get("error") if isinstance(error_body, dict) else None
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", candidate):
                error_code = candidate
        except (ValueError, OSError, AttributeError):
            pass
        finally:
            exc.close()
        raise BskyError(
            f"HTTP {exc.code} from {url}",
            status=exc.code,
            transient=_is_transient_status(exc.code),
            error_code=error_code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BskyError(f"network error for {url}: {exc}",
                        transient=True) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BskyError(f"invalid JSON response from {url}") from exc
    if not isinstance(parsed, dict):
        raise BskyError(f"unexpected response shape from {url}")
    if parsed.get("error") is not None:
        raise BskyError("API returned an error object in a success response")
    return parsed


def _post_json(url: str, payload: dict, token: str | None = None) -> dict:
    return _request_json("POST", url, payload, token)


def _get_json(url: str, token: str | None = None) -> dict:
    return _request_json("GET", url, None, token)


def _with_retry(fn, *, attempts: int = MAX_ATTEMPTS, base_delay: float = 1.0,
                sleep=time_module.sleep):
    """Run `fn`, retrying transient BskyErrors with exponential backoff."""
    last_error: BskyError | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except BskyError as exc:
            if not exc.transient:
                raise
            last_error = exc
            if attempt < attempts - 1:
                sleep(base_delay * (2 ** attempt))
    assert last_error is not None
    raise last_error


def create_session(handle: str, app_password: str, base: str | None = None,
                   *, sleep=time_module.sleep) -> dict:
    """Log in with handle + app password. Returns the session dict."""
    root = (base or pds_url()).rstrip("/")
    session = _with_retry(
        lambda: _post_json(
            f"{root}/xrpc/com.atproto.server.createSession",
            {"identifier": handle, "password": app_password},
        ),
        sleep=sleep,
    )
    did, token = session.get("did"), session.get("accessJwt")
    if not isinstance(did, str) or not _DID_RE.fullmatch(did) \
            or not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise BskyError("createSession returned a malformed session")
    return session


def _list_recent_records(root: str, session: dict, *, limit: int = 100,
                         sleep=time_module.sleep) -> list[dict]:
    query = urllib.parse.urlencode({
        "repo": session["did"],
        "collection": POST_COLLECTION,
        "limit": limit,
        "reverse": "true",
    })
    result = _with_retry(
        lambda: _get_json(
            f"{root}/xrpc/com.atproto.repo.listRecords?{query}",
            token=session["accessJwt"],
        ),
        sleep=sleep,
    )
    records = result.get("records")
    if not isinstance(records, list):
        raise BskyError("listRecords returned a malformed response")
    for item in records:
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(value, dict) or not is_post_uri(item.get("uri"), session["did"]) \
                or value.get("$type") != POST_COLLECTION or not isinstance(value.get("text"), str) \
                or not isinstance(value.get("createdAt"), str):
            raise BskyError("listRecords contains a malformed post record")
        try:
            moment = datetime.fromisoformat(value["createdAt"].replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError("timezone missing")
        except ValueError as exc:
            raise BskyError("listRecords contains an invalid post timestamp") from exc
    return records


def _validate_created(result: dict, expected_did: str | None = None,
                      expected_rkey: str | None = None) -> str | None:
    """The uri of a createRecord response, or None when malformed."""
    uri = result.get("uri")
    cid = result.get("cid")
    if is_post_uri(uri, expected_did, expected_rkey) \
            and isinstance(cid, str) and 0 < len(cid) <= 512 and cid.isascii() \
            and not any(c.isspace() for c in cid):
        return uri
    return None


def create_post(text: str, handle: str, app_password: str,
                facets: list[dict] | None = None,
                base: str | None = None, *,
                slot_date: date | None = None,
                slot_time: time | None = None,
                slot_id: str | None = None,
                tz_name: str = "Asia/Tokyo",
                sleep=time_module.sleep,
                attempts: int = MAX_ATTEMPTS) -> dict:
    """Post `text` to Bluesky, hardened against duplicates and retries.

    Returns {"uri": ..., "cid": ...} for a fresh post, or
    {"uri": ..., "already_posted": True} when the slot's post already
    exists on the server. Raises BskyError when posting fails.

    The duplicate checks only run when `slot_date` and `slot_time`
    are given (the scheduled-slot context).
    """
    if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= MAX_ATTEMPTS:
        raise BskyError("attempts must be an integer between 1 and 3")
    if slot_id is not None and (not isinstance(slot_id, str) or not slot_id or slot_date is None or slot_time is None):
        raise BskyError("slot_id requires a non-empty ID, scheduled date and time")
    root = (base or pds_url()).rstrip("/")
    tz = ZoneInfo(tz_name)
    rkey = slot_record_key(slot_date, slot_time, tz, slot_id) if slot_id is not None else None
    session = create_session(handle, app_password, root, sleep=sleep)

    def check_existing(*, legacy: bool = True) -> str | None:
        if rkey is not None:
            known = _get_slot_record(root, session, rkey, text, sleep=sleep)
            if known:
                return known
        if not legacy:
            return None
        if slot_date is None or slot_time is None:
            return None
        records = _list_recent_records(root, session, sleep=sleep)
        return find_posted_uri(records, text, slot_date=slot_date,
                               slot_time=slot_time, tz=tz)

    # Periodic catch-up revisits the same slots. If the duplicate check
    # fails, leave the slot for a later run rather than posting blindly.
    existing = check_existing()
    if existing:
        return {"uri": existing, "already_posted": True}

    record = build_post_record(text, facets)
    payload = {"repo": session["did"], "collection": POST_COLLECTION, "record": record}
    if rkey is not None:
        payload["rkey"] = rkey
    last_error: BskyError | None = None
    for attempt in range(attempts):
        if attempt > 0:
            # The previous attempt may have landed even though its
            # response was lost; never post twice for one slot.
            found = check_existing()
            if found:
                return {"uri": found, "already_posted": True}
            sleep(1.0 * (2 ** (attempt - 1)))
        try:
            result = _post_json(
                f"{root}/xrpc/com.atproto.repo.createRecord",
                payload,
                token=session["accessJwt"],
            )
        except BskyError as exc:
            if rkey is not None and exc.status in (400, 409):
                # Another run may have created this same key after our lookup.
                found = check_existing(legacy=False)
                if found:
                    return {"uri": found, "already_posted": True}
            if not exc.transient:
                raise
            last_error = exc
            continue
        uri = _validate_created(result, session["did"], rkey)
        if uri:
            return {"uri": uri, "cid": result["cid"]}
        # A "success" response without a usable uri/cid is not proof
        # of posting. Believe it only if the record is really there.
        try:
            found = check_existing()
        except BskyError:
            found = None
        if found:
            return {"uri": found, "already_posted": True}
        raise BskyError(
            "createRecord returned a malformed response (no valid "
            "uri/cid) and the post was not found on the server"
        )
    # Even the last attempt may have been stored before its response was lost.
    found = check_existing()
    if found:
        return {"uri": found, "already_posted": True}
    assert last_error is not None
    raise last_error
