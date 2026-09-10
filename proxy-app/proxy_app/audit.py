"""Records who reached what, and when the proxy woke or slept an app.

Auditing is best effort by design: :meth:`Recorder.record` puts an event on a
bounded queue and returns, and a background task drains it. It drops events
rather than slow a request down, and it never raises. A dropped audit row
would be a worse outcome than a missing one only if the audit were the
security control -- it is not. The access decision is, and it does not depend
on this module.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

#: Event kinds, matching the `event` attribute in shiny-proxy-audit.
EVENT_ALLOW = "allow"
EVENT_DENY = "deny"
EVENT_WAKE = "wake"
EVENT_SLEEP = "sleep"
EVENT_EXPIRED = "expired"
#: The sleeper's C1 hard cap: a service that stayed awake past
#: max_session_hours, scaled to zero regardless of open sockets or recent
#: requests. Distinct from EVENT_SLEEP so the audit trail can tell "nobody was
#: using it" apart from "somebody was, and the cap ended their session anyway".
EVENT_FORCE_SLEEP = "force_sleep"
#: A successful PATCH through the portal's admin API. The `path` carries the
#: NAMES of the fields that changed, never their values -- an audit row must
#: not be somewhere an allowlist can be read out of. Never deduplicated: two
#: identical edits a second apart are two decisions somebody made.
EVENT_CONFIG_CHANGE = "config_change"
#: Somebody used the portal's sign-out. Recorded against the PORTAL host
#: (that is where the act happened; no app was involved) and never
#: deduplicated -- signing out twice is two decisions, and the second one
#: usually means the first appeared not to work, which is exactly the thing
#: a trail should show.
EVENT_SIGNED_OUT = "signed_out"

#: --- P2a creation (docs/design/portal-p2a.md) -----------------------------
#: One event per state transition, so the trail answers "who put this
#: hostname on the internet, when, and did it work" without reading logs.
#: None of them are ever deduplicated -- each is a distinct thing that
#: happened once, and a creation attempted twice is two attempts.
#:
#: `app_created` carries the creator's email; the build events carry it too
#: where the row remembers it, because the build outcome arrives on a
#: background loop with no request and no caller of its own.
EVENT_APP_CREATED = "app_created"
EVENT_BUILD_STARTED = "build_started"
EVENT_BUILD_SUCCEEDED = "build_succeeded"
EVENT_BUILD_FAILED = "build_failed"
#: Distinct from `build_failed`: the image built fine (or was never reached)
#: and a provisioning API call -- ECR, IAM, the task definition, the service,
#: the Cognito callback -- is what went wrong. Different fix, different event.
EVENT_PROVISION_FAILED = "provision_failed"

#: Allow events are collapsed per host+email for this long. A single Shiny
#: page load is dozens of asset requests by the same person to the same host;
#: a row for each would turn "cents of DynamoDB" into real money and make the
#: trail unreadable. Denials, wakes, sleeps, expiries and force-sleeps are
#: NEVER collapsed.
ALLOW_WINDOW = 600.0

#: 90 days, per the design spec, written into every row as the table's TTL
#: attribute so expiry needs no housekeeping job.
RETENTION_SECONDS = 90 * 24 * 60 * 60

DEFAULT_QUEUE_DEPTH = 512


@dataclass(frozen=True)
class Event:
    """One row of the audit table."""

    host: str
    event: str
    email: str = ""
    path: str = ""
    outcome: str = ""  # why, for deny events
    at: float = 0.0  # epoch seconds; 0 means "stamp it on the way in"


class AuditSink(Protocol):
    """Persistence for events. The DynamoDB implementation is below."""

    async def put(self, event: Event) -> None: ...


def sort_key(at: float, *, suffix: str | None = None) -> str:
    """The design spec's ``ts#rand`` sort key.

    Fixed-width 13-digit epoch milliseconds so a range query sorts
    lexicographically, plus 8 hex characters of randomness so two events in
    the same millisecond on two proxy tasks do not overwrite each other.
    """
    millis = int(at * 1000)
    random_part = suffix if suffix is not None else secrets.token_hex(4)
    return f"{millis:013d}#{random_part}"


class Recorder:
    """The non-blocking front end to an :class:`AuditSink`.

    A ``None`` sink logs and discards, which is what running the proxy on a
    laptop wants.
    """

    def __init__(
        self,
        sink: AuditSink | None = None,
        log: logging.Logger | None = None,
        *,
        queue_depth: int = DEFAULT_QUEUE_DEPTH,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sink = sink
        self._log = log or logging.getLogger("proxy.audit")
        self._clock = clock
        self._queue: asyncio.Queue[Event] = asyncio.Queue(
            maxsize=queue_depth if queue_depth > 0 else DEFAULT_QUEUE_DEPTH
        )
        self._seen: dict[tuple[str, str], float] = {}
        self._worker: asyncio.Task[None] | None = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the drain task. Idempotent."""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._drain())

    async def stop(self, timeout: float = 2.0) -> None:
        """Finish what is queued, then stop. Never raises."""
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except (TimeoutError, asyncio.TimeoutError):
            self._log.warning("audit queue not drained before shutdown")
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception:  # shutdown is quiet; there is nobody left to tell
            pass

    # --- recording ---------------------------------------------------------

    def record(self, event: Event) -> None:
        """Queue an event.

        Never blocks, never raises, never returns an error: on a full queue
        the event is dropped and counted in the log.
        """
        try:
            if not event.at:
                event = Event(
                    host=event.host,
                    event=event.event,
                    email=event.email,
                    path=event.path,
                    outcome=event.outcome,
                    at=self._clock(),
                )
            if event.event == EVENT_ALLOW and self._recently_allowed(event):
                return
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._log.warning(
                "audit queue full, event dropped",
                extra={"host": event.host, "event": event.event},
            )
        except Exception as exc:  # pragma: no cover - defence in depth
            self._log.warning("audit record failed", extra={"reason": str(exc)})

    def _recently_allowed(self, event: Event) -> bool:
        key = (event.host, event.email)
        now = event.at

        last = self._seen.get(key)
        if last is not None and now - last < ALLOW_WINDOW:
            return True

        if len(self._seen) > 4096:
            self._seen = {
                k: seen for k, seen in self._seen.items() if now - seen < ALLOW_WINDOW
            }
        self._seen[key] = now
        return False

    # --- draining ----------------------------------------------------------

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._write(event)
            finally:
                self._queue.task_done()

    async def _write(self, event: Event) -> None:
        if self._sink is None:
            self._log.debug(
                "audit",
                extra={
                    "host": event.host,
                    "event": event.event,
                    "email": event.email,
                    "path": event.path,
                },
            )
            return
        try:
            await self._sink.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log.warning(
                "audit write failed",
                extra={"host": event.host, "event": event.event, "reason": str(exc)},
            )


class DynamoAuditSink:
    """Writes to shiny-proxy-audit: PK ``host`` (S), SK ``ts`` (S), TTL ``ttl``."""

    def __init__(self, client: Any, table: str) -> None:
        self._client = client
        self._table = table

    async def put(self, event: Event) -> None:
        at = event.at or time.time()
        item: dict[str, dict[str, Any]] = {
            "host": {"S": event.host},
            "ts": {"S": sort_key(at)},
            "event": {"S": event.event},
            "ts_epoch": {"N": str(int(at))},
            "ttl": {"N": str(int(at) + RETENTION_SECONDS)},
        }
        if event.email:
            item["email"] = {"S": event.email}
        if event.path:
            item["path"] = {"S": event.path}
        if event.outcome:
            item["outcome"] = {"S": event.outcome}

        await asyncio.to_thread(
            self._client.put_item, TableName=self._table, Item=item
        )


# --- reading the trail back (the portal's audit viewer) --------------------

#: Page size when the caller does not ask, and the ceiling when it asks for
#: too much. A DynamoDB Query page is capped at 1 MB anyway; this bounds the
#: JSON the browser has to render.
DEFAULT_AUDIT_LIMIT = 100
MAX_AUDIT_LIMIT = 500

#: A cursor is a base64-JSON LastEvaluatedKey and nothing else. Anything
#: bigger than this did not come from us.
MAX_CURSOR_BYTES = 2048


class CursorError(ValueError):
    """A cursor that did not come from :func:`encode_cursor` (or was edited)."""


def encode_cursor(key: dict[str, Any] | None) -> str | None:
    """Render a DynamoDB LastEvaluatedKey as the contract's opaque cursor.

    Opaque to the CLIENT, not signed: it is a table key, not a capability.
    The host it points into is already in the URL, and the handler re-checks
    the caller is an admin on every page.
    """
    if not key:
        return None
    raw = json.dumps(key, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any] | None:
    """Turn a cursor back into an ExclusiveStartKey, or raise.

    Validated rather than trusted: it is handed straight to DynamoDB, so it
    has to be a flat map of attribute values and nothing else -- never a
    nested structure someone hand-crafted to see what the SDK does with it.
    """
    text = (cursor or "").strip()
    if not text:
        return None
    if len(text) > MAX_CURSOR_BYTES:
        raise CursorError("cursor is not valid")

    try:
        padded = text + "=" * (-len(text) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise CursorError("cursor is not valid") from None

    if not isinstance(decoded, dict) or not decoded:
        raise CursorError("cursor is not valid")
    for name, value in decoded.items():
        if not isinstance(name, str) or not isinstance(value, dict) or len(value) != 1:
            raise CursorError("cursor is not valid")
        for kind, inner in value.items():
            if kind not in ("S", "N", "B") or not isinstance(inner, str):
                raise CursorError("cursor is not valid")
    return decoded


class DynamoAuditReader:
    """Reads shiny-proxy-audit back, newest first, for one host.

    Query, not Scan: the table is partitioned by host and the sort key is
    time-ordered, so "the last hundred events for this app" is one call
    against one partition. This is the only thing in the service that needs
    ``dynamodb:Query`` -- see the README's IAM contract.
    """

    def __init__(self, client: Any, table: str) -> None:
        self._client = client
        self._table = table

    async def events(
        self, host: str, limit: int = DEFAULT_AUDIT_LIMIT, start_key: dict | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "#host = :host",
            "ExpressionAttributeNames": {"#host": "host"},
            "ExpressionAttributeValues": {":host": {"S": host}},
            # Newest first, as the contract specifies: the sort key is
            # zero-padded epoch milliseconds, so descending is chronological.
            "ScanIndexForward": False,
            "Limit": max(1, min(int(limit), MAX_AUDIT_LIMIT)),
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key

        response = await asyncio.to_thread(self._client.query, **kwargs)
        events = [event_from_item(item) for item in response.get("Items", ())]
        return events, response.get("LastEvaluatedKey") or None


def event_from_item(item: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One audit row in the contract's JSON shape.

    ``ts`` comes from ``ts_epoch`` when present and is otherwise recovered
    from the sort key's millisecond prefix, so a row written by an older
    build still reads back with a timestamp.
    """
    return {
        "event": _item_s(item, "event"),
        "email": _item_s(item, "email"),
        "path": _item_s(item, "path"),
        "outcome": _item_s(item, "outcome"),
        "ts": _item_ts(item),
    }


def _item_s(item: dict[str, dict[str, Any]], name: str) -> str:
    return str((item.get(name) or {}).get("S") or "")


def _item_ts(item: dict[str, dict[str, Any]]) -> int | None:
    raw = (item.get("ts_epoch") or {}).get("N")
    if raw is not None:
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            pass
    sort = _item_s(item, "ts")
    head = sort.split("#", 1)[0]
    if head.isdigit():
        return int(head) // 1000
    return None
