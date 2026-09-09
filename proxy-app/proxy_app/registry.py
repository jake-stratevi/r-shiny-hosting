"""The app entitlements table: what apps exist, where their tasks live, who
may use them, and when they expire.

:class:`App`, :func:`normalize_host` and :class:`CachedRegistry` are free of
AWS -- they are what the access decision and its tests need. The DynamoDB
implementation of the :class:`AppStore` protocol is :class:`DynamoAppStore` at
the bottom of the module, and it is the only part that imports boto3.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, Sequence

# --- the row ---------------------------------------------------------------

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUS_EXPIRED = "expired"

#: The proxy implements all_users and users. The remaining three are reserved
#: by ADR-0014 so the schema does not change when the portal phase lands. An
#: app carrying a reserved mode is refused, not admitted -- see `access`.
MODE_ALL_USERS = "all_users"
MODE_USERS = "users"
MODE_TEAM = "team"
MODE_ORGANIZATIONS = "organizations"
MODE_CLIENT_MAGIC_LINK = "client_magic_link"

IMPLEMENTED_MODES = frozenset({MODE_ALL_USERS, MODE_USERS})
RESERVED_MODES = frozenset({MODE_TEAM, MODE_ORGANIZATIONS, MODE_CLIENT_MAGIC_LINK})
KNOWN_MODES = IMPLEMENTED_MODES | RESERVED_MODES

#: Defaults applied to any item that omits the attribute.
DEFAULT_CONTAINER_PORT = 3838
DEFAULT_IDLE_MINUTES = 15
DEFAULT_MAX_SESSION_HOURS = 0  # 0/absent means uncapped -- see App.has_session_cap

#: A key that will never exist. /__proxy/readyz reads it because GetItem is
#: already in the task's IAM policy and DescribeTable deliberately is not --
#: readiness must not be the reason the policy grows.
READYZ_SENTINEL_HOST = "__readyz__"


def normalize_host(host: str | None) -> str:
    """Reduce a Host header to the table's partition key.

    The same app arrives as "model.tools.stratevi.com", with an explicit
    ":443", with a trailing root dot, and in whatever case the client typed.
    All four are one row.
    """
    text = (host or "").strip()
    if not text:
        return ""

    if text.startswith("["):
        # [::1]:8080 -- a bracketed IPv6 literal, with or without a port.
        closing = text.find("]")
        if closing != -1:
            text = text[1:closing]
    elif text.count(":") == 1:
        # One colon is host:port. Several means a bare IPv6 literal, which has
        # no port to strip.
        text = text.split(":", 1)[0]

    return text.rstrip(".").lower()


@dataclass(frozen=True)
class App:
    """One row of the shiny-proxy-apps table.

    Frozen on purpose: the read-through cache hands the same instance to every
    concurrent request, and an immutable row means it cannot hand out one that
    another request has edited. Build one with :meth:`create`, which applies
    the defaults and normalizes everything that is compared later.
    """

    host: str
    app_key: str = ""
    ecs_service: str = ""
    container_port: int = DEFAULT_CONTAINER_PORT
    status: str = STATUS_ACTIVE
    access_mode: str = MODE_USERS
    allowed_emails: tuple[str, ...] = field(default_factory=tuple)
    idle_minutes: int = DEFAULT_IDLE_MINUTES
    expires_at: int = 0  # epoch seconds; 0 means never
    last_active: int = 0  # epoch seconds, written at most once a minute
    # --- force-sleep cap (C1) ------------------------------------------------
    # The sleeper treats an open websocket as activity (activity.py), so a
    # browser tab left open keeps an expensive app -- the model at $0.233/hr --
    # awake indefinitely. max_session_hours is the hard ceiling: once a service
    # has been continuously awake longer than the cap, the sleeper force-sleeps
    # it even with open sockets or recent requests. Users lose their session
    # (acceptable -- the wake page is one refresh away); money stops burning.
    max_session_hours: int = DEFAULT_MAX_SESSION_HOURS  # hours; 0/absent = uncapped
    awake_since: int = 0  # epoch seconds; 0 means "not currently tracked awake"

    @classmethod
    def create(
        cls,
        *,
        host: str,
        app_key: str = "",
        ecs_service: str = "",
        container_port: int | None = None,
        status: str | None = None,
        access_mode: str | None = None,
        allowed_emails: Iterable[str] | None = None,
        idle_minutes: int | None = None,
        expires_at: int | None = None,
        last_active: int | None = None,
        max_session_hours: int | None = None,
        awake_since: int | None = None,
    ) -> "App":
        """Build a row with defaults applied and comparisons pre-normalized."""
        return cls(
            host=normalize_host(host),
            app_key=(app_key or "").strip(),
            ecs_service=(ecs_service or "").strip(),
            container_port=int(container_port) if container_port else DEFAULT_CONTAINER_PORT,
            status=(status or "").strip().lower() or STATUS_ACTIVE,
            access_mode=(access_mode or "").strip().lower() or MODE_USERS,
            allowed_emails=tuple(
                cleaned
                for cleaned in (str(e).strip().lower() for e in (allowed_emails or ()))
                if cleaned
            ),
            idle_minutes=int(idle_minutes) if idle_minutes else DEFAULT_IDLE_MINUTES,
            expires_at=int(expires_at or 0),
            last_active=int(last_active or 0),
            # Unlike idle_minutes, a falsy value here is a real answer (no
            # cap), not "unset -- fall back to a default" -- the default is
            # already 0.
            max_session_hours=int(max_session_hours or 0),
            awake_since=int(awake_since or 0),
        )

    def allows(self, email: str) -> bool:
        """Is this address on the app's list?"""
        if not email:
            return False
        return email.strip().lower() in self.allowed_emails

    def is_expired(self, now: float) -> bool:
        """True once ``expires_at`` has passed.

        Regardless of whether the reaper has got round to flipping ``status``
        yet: enforcement must not wait for a background loop.
        """
        return self.expires_at > 0 and now >= self.expires_at

    def idle_after_seconds(self) -> float:
        """How long with no request and no open websocket before sleeping."""
        minutes = self.idle_minutes if self.idle_minutes > 0 else DEFAULT_IDLE_MINUTES
        return minutes * 60.0

    def has_session_cap(self) -> bool:
        """Does this app have a hard force-sleep ceiling on awake time?"""
        return self.max_session_hours > 0

    def max_session_seconds(self) -> float:
        """The cap in seconds. Meaningless when :meth:`has_session_cap` is False."""
        return self.max_session_hours * 3600.0


# --- persistence contract --------------------------------------------------


class AppStore(Protocol):
    """Persistence for app rows.

    ``app()`` returns ``None`` for a host that is not in the table -- an
    unknown host is an answer, not a failure.
    """

    async def app(self, host: str) -> App | None: ...

    async def apps(self) -> list[App]: ...

    async def set_last_active(self, host: str, ts: int) -> None: ...

    async def set_awake_since(self, host: str, ts: int) -> None: ...

    async def set_status(self, host: str, status: str) -> None: ...

    async def ping(self) -> None: ...


# --- read-through cache ----------------------------------------------------

DEFAULT_CACHE_TTL = 10.0


class CachedRegistry:
    """A short-TTL read-through cache in front of an :class:`AppStore`.

    Every request for every asset on a Shiny page hits the registry, so the
    uncached cost would be a DynamoDB read per image. The TTL is short (~10s)
    because it also bounds how long a revoked entitlement keeps working;
    misses are cached too, so an unknown host cannot be used to hammer the
    table.
    """

    def __init__(
        self,
        store: AppStore,
        ttl: float = DEFAULT_CACHE_TTL,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._ttl = ttl if ttl > 0 else DEFAULT_CACHE_TTL
        self._clock = clock
        self._entries: dict[str, tuple[App | None, float]] = {}

    async def app(self, host: str) -> App | None:
        host = normalize_host(host)

        cached = self._entries.get(host)
        if cached is not None and self._clock() - cached[1] < self._ttl:
            return cached[0]

        try:
            found = await self._store.app(host)
        except Exception:
            # Serve a stale entry rather than a 503 if we have one: ADR-0014
            # wants the branded pages to work when the database is unhappy.
            # With nothing cached the error propagates and the caller fails
            # closed -- never open.
            if cached is not None:
                return cached[0]
            raise

        self._entries[host] = (found, self._clock())
        return found

    def invalidate(self, host: str) -> None:
        """Drop one host, so the next read is authoritative."""
        self._entries.pop(normalize_host(host), None)

    async def apps(self) -> list[App]:
        return await self._store.apps()

    async def set_last_active(self, host: str, ts: int) -> None:
        await self._store.set_last_active(host, ts)

    async def set_awake_since(self, host: str, ts: int) -> None:
        # Does not affect the access decision, so no cache invalidation --
        # same as set_last_active.
        await self._store.set_awake_since(host, ts)

    async def set_status(self, host: str, status: str) -> None:
        self.invalidate(host)
        await self._store.set_status(host, status)

    async def ping(self) -> None:
        await self._store.ping()


# --- DynamoDB item shape ---------------------------------------------------


def app_item(app: App) -> dict[str, dict[str, Any]]:
    """Encode a row as DynamoDB AttributeValues.

    Public so the seed tool's ``--dry-run`` prints exactly what a real run
    would PutItem.
    """
    item: dict[str, dict[str, Any]] = {
        "host": {"S": app.host},
        "app_key": {"S": app.app_key},
        "ecs_service": {"S": app.ecs_service},
        "container_port": {"N": str(app.container_port)},
        "status": {"S": app.status},
        "access_mode": {"S": app.access_mode},
        "idle_minutes": {"N": str(app.idle_minutes)},
    }
    if app.allowed_emails:
        # A DynamoDB string set cannot be empty, hence the guard.
        item["allowed_emails"] = {"SS": list(app.allowed_emails)}
    if app.expires_at:
        item["expires_at"] = {"N": str(app.expires_at)}
    if app.last_active:
        item["last_active"] = {"N": str(app.last_active)}
    if app.max_session_hours:
        item["max_session_hours"] = {"N": str(app.max_session_hours)}
    if app.awake_since:
        item["awake_since"] = {"N": str(app.awake_since)}
    return item


def app_from_item(item: dict[str, dict[str, Any]]) -> App:
    """Decode a DynamoDB item into a normalized row."""
    return App.create(
        host=_read_s(item, "host"),
        app_key=_read_s(item, "app_key"),
        ecs_service=_read_s(item, "ecs_service"),
        container_port=_read_n(item, "container_port"),
        status=_read_s(item, "status"),
        access_mode=_read_s(item, "access_mode"),
        allowed_emails=_read_strings(item, "allowed_emails"),
        idle_minutes=_read_n(item, "idle_minutes"),
        expires_at=_read_n(item, "expires_at"),
        last_active=_read_n(item, "last_active"),
        max_session_hours=_read_n(item, "max_session_hours"),
        awake_since=_read_n(item, "awake_since"),
    )


def _read_s(item: dict[str, dict[str, Any]], name: str) -> str:
    value = item.get(name) or {}
    return str(value.get("S") or "")


def _read_n(item: dict[str, dict[str, Any]], name: str) -> int:
    value = item.get(name) or {}
    raw = value.get("N")
    if raw is None:
        return 0
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _read_strings(item: dict[str, dict[str, Any]], name: str) -> Sequence[str]:
    value = item.get(name) or {}
    if "SS" in value:
        return list(value["SS"] or ())
    if "L" in value:
        # Tolerate a list-of-strings written by hand in the console.
        return [entry.get("S", "") for entry in (value["L"] or ()) if isinstance(entry, dict)]
    return ()


# --- DynamoDB store --------------------------------------------------------


class DynamoAppStore:
    """:class:`AppStore` over shiny-proxy-apps, on a plain boto3 client.

    boto3 is synchronous, so every call goes through ``asyncio.to_thread``.
    That is deliberate: a handful of concurrent users at ~1 DynamoDB call per
    10s per host does not justify a second AWS SDK (aioboto3) in the image.
    botocore clients are safe to share across threads.
    """

    def __init__(self, client: Any, table: str) -> None:
        self._client = client
        self._table = table

    async def app(self, host: str) -> App | None:
        response = await asyncio.to_thread(
            self._client.get_item,
            TableName=self._table,
            Key={"host": {"S": normalize_host(host)}},
        )
        item = response.get("Item")
        if not item:
            return None  # an unknown host is an answer, not a failure
        return app_from_item(item)

    async def apps(self) -> list[App]:
        """Enumerate the table for the sleeper loop.

        This is a Scan, which needs ``dynamodb:Scan`` in the task policy --
        the design spec's IAM sketch lists only *Item and Query. There is no
        partition key to query on here, and the table has one row per app, so
        the Scan is a handful of items once a minute.
        """
        found: list[App] = []
        start_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {"TableName": self._table}
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            response = await asyncio.to_thread(self._client.scan, **kwargs)
            for item in response.get("Items", ()):
                found.append(app_from_item(item))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return found

    async def set_last_active(self, host: str, ts: int) -> None:
        await asyncio.to_thread(
            self._client.update_item,
            TableName=self._table,
            Key={"host": {"S": normalize_host(host)}},
            UpdateExpression="SET last_active = :ts",
            ConditionExpression="attribute_exists(#host)",
            ExpressionAttributeNames={"#host": "host"},
            ExpressionAttributeValues={":ts": {"N": str(int(ts))}},
        )

    async def set_awake_since(self, host: str, ts: int) -> None:
        """Best-effort, same pattern as :meth:`set_last_active`.

        Called with ``ts=0`` to clear the attribute once a service is observed
        asleep -- a plain SET rather than REMOVE, so this stays one shape.
        """
        await asyncio.to_thread(
            self._client.update_item,
            TableName=self._table,
            Key={"host": {"S": normalize_host(host)}},
            UpdateExpression="SET awake_since = :ts",
            ConditionExpression="attribute_exists(#host)",
            ExpressionAttributeNames={"#host": "host"},
            ExpressionAttributeValues={":ts": {"N": str(int(ts))}},
        )

    async def set_status(self, host: str, status: str) -> None:
        # `status` is a DynamoDB reserved word; it has to go through a name
        # placeholder or the update expression is rejected.
        await asyncio.to_thread(
            self._client.update_item,
            TableName=self._table,
            Key={"host": {"S": normalize_host(host)}},
            UpdateExpression="SET #status = :status",
            ConditionExpression="attribute_exists(#host)",
            ExpressionAttributeNames={"#status": "status", "#host": "host"},
            ExpressionAttributeValues={":status": {"S": status}},
        )

    async def ping(self) -> None:
        await asyncio.to_thread(
            self._client.get_item,
            TableName=self._table,
            Key={"host": {"S": READYZ_SENTINEL_HOST}},
        )

    async def put(self, app: App) -> None:
        """Write a whole row. Used by the seed tool, not by the request path."""
        await asyncio.to_thread(
            self._client.put_item, TableName=self._table, Item=app_item(app)
        )


__all__ = [
    "App",
    "AppStore",
    "CachedRegistry",
    "DynamoAppStore",
    "app_from_item",
    "app_item",
    "normalize_host",
    "DEFAULT_CACHE_TTL",
    "DEFAULT_CONTAINER_PORT",
    "DEFAULT_IDLE_MINUTES",
    "DEFAULT_MAX_SESSION_HOURS",
    "IMPLEMENTED_MODES",
    "KNOWN_MODES",
    "MODE_ALL_USERS",
    "MODE_CLIENT_MAGIC_LINK",
    "MODE_ORGANIZATIONS",
    "MODE_TEAM",
    "MODE_USERS",
    "READYZ_SENTINEL_HOST",
    "RESERVED_MODES",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "STATUS_EXPIRED",
]
