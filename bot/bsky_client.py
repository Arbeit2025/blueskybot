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
import os
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


class BskyError(Exception):
    """Bluesky request failure. `transient` marks errors worth retrying."""

    def __init__(self, message: str, *, status: int | None = None,
                 transient: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient


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
    its createdAt falls on `slot_date` in `tz`, and its local time is
    inside the slot window (WINDOW_BEFORE before to WINDOW_AFTER
    after the slot time). The window is what tells apart the two
    daily slots that share an identical text (10:00 and 16:00).
    """
    slot_dt = datetime.combine(slot_date, slot_time, tzinfo=tz)
    window_start = slot_dt - WINDOW_BEFORE
    window_end = slot_dt + WINDOW_AFTER
    for item in records:
        value = item.get("value") or {}
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
        if local.date() != slot_date:
            continue
        if window_start <= local <= window_end:
            uri = item.get("uri")
            if isinstance(uri, str) and uri:
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
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise BskyError(
            f"HTTP {exc.code} from {url}",
            status=exc.code,
            transient=_is_transient_status(exc.code),
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
    if not session.get("did") or not session.get("accessJwt"):
        raise BskyError("createSession returned a malformed session")
    return session


def _list_recent_records(root: str, session: dict, *, limit: int = 30,
                         sleep=time_module.sleep) -> list[dict]:
    query = urllib.parse.urlencode({
        "repo": session["did"],
        "collection": POST_COLLECTION,
        "limit": limit,
    })
    result = _with_retry(
        lambda: _get_json(
            f"{root}/xrpc/com.atproto.repo.listRecords?{query}",
            token=session["accessJwt"],
        ),
        sleep=sleep,
    )
    records = result.get("records")
    return records if isinstance(records, list) else []


def _validate_created(result: dict) -> str | None:
    """The uri of a createRecord response, or None when malformed."""
    uri = result.get("uri")
    cid = result.get("cid")
    if isinstance(uri, str) and uri.startswith("at://") \
            and isinstance(cid, str) and cid:
        return uri
    return None


def create_post(text: str, handle: str, app_password: str,
                facets: list[dict] | None = None,
                base: str | None = None, *,
                slot_date: date | None = None,
                slot_time: time | None = None,
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
    root = (base or pds_url()).rstrip("/")
    session = create_session(handle, app_password, root, sleep=sleep)
    tz = ZoneInfo(tz_name)

    def check_existing() -> str | None:
        if slot_date is None or slot_time is None:
            return None
        records = _list_recent_records(root, session, sleep=sleep)
        return find_posted_uri(records, text, slot_date=slot_date,
                               slot_time=slot_time, tz=tz)

    precheck_failed = False
    try:
        existing = check_existing()
    except BskyError:
        # The check itself is down; proceed, but the retry loop below
        # re-checks before every further attempt.
        existing = None
        precheck_failed = True
    if existing:
        return {"uri": existing, "already_posted": True,
                "precheck_failed": precheck_failed}

    record = build_post_record(text, facets)
    last_error: BskyError | None = None
    for attempt in range(attempts):
        if attempt > 0:
            # The previous attempt may have landed even though its
            # response was lost; never post twice for one slot.
            try:
                found = check_existing()
            except BskyError:
                found = None
            if found:
                return {"uri": found, "already_posted": True,
                        "precheck_failed": precheck_failed}
            sleep(1.0 * (2 ** (attempt - 1)))
        try:
            result = _post_json(
                f"{root}/xrpc/com.atproto.repo.createRecord",
                {
                    "repo": session["did"],
                    "collection": POST_COLLECTION,
                    "record": record,
                },
                token=session["accessJwt"],
            )
        except BskyError as exc:
            if not exc.transient:
                raise
            last_error = exc
            continue
        uri = _validate_created(result)
        if uri:
            return {"uri": uri, "cid": result["cid"],
                    "precheck_failed": precheck_failed}
        # A "success" response without a usable uri/cid is not proof
        # of posting. Believe it only if the record is really there.
        try:
            found = check_existing()
        except BskyError:
            found = None
        if found:
            return {"uri": found, "already_posted": True,
                    "precheck_failed": precheck_failed}
        raise BskyError(
            "createRecord returned a malformed response (no valid "
            "uri/cid) and the post was not found on the server"
        )
    assert last_error is not None
    raise last_error
