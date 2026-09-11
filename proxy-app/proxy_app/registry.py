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
#: P2a creation states. A row exists from the moment the slug is reserved --
#: that reservation IS the mutex against two people creating the same
#: hostname -- so there are two statuses for "this row is not an app you can
#: reach yet". `access.decide` refuses both (an unrecognised status is a 403),
#: which is the correct answer for a host whose container does not exist.
STATUS_BUILDING = "building"
STATUS_BUILD_FAILED = "build_failed"

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

#: Rows whose partition key starts with this are CONFIGURATION, not apps.
#: The table has one item per app keyed by hostname, and a hostname can never
#: start with an underscore, so the namespace is free and needs no second
#: table. Everything that enumerates the table -- the registry, the sleeper,
#: the portal's menu and admin list -- must skip them, or the portal's own
#: settings row becomes a ghost app that the sleeper tries to scale and the
#: menu offers people a link to.
CONFIG_PREFIX = "__"

#: The one config row P1 defines: `admin_emails` (SS), managed as a Terraform
#: aws_dynamodb_table_item so the admin list is code-reviewed (ADR-0014).
#: P2a adds two more string sets to the SAME row: `creator_emails` (who may
#: create apps -- deliberately NOT implied by admin, see portal-p2a.md) and
#: `key_denylist` (substrings banned from public hostnames, editable without
#: a deploy). `staff_domains` is the fourth, and it gates both of the first
#: two: a control-plane permission is only ever granted to a staff address.
CONFIG_HOST = "__config__"

CONFIG_ADMIN_EMAILS = "admin_emails"
CONFIG_CREATOR_EMAILS = "creator_emails"
CONFIG_KEY_DENYLIST = "key_denylist"
CONFIG_STAFF_DOMAINS = "staff_domains"

#: The email domains that count as Stratevi staff when the ``__config__`` row
#: does not say -- because the attribute is missing, is an empty set, or could
#: not be read at all.
#:
#: THIS DEFAULT IS DELIBERATE AND IT IS NEITHER OF THE TWO OBVIOUS CHOICES.
#:
#: "Everyone is staff" (an empty set matching every address) would turn a
#: partial read of the config row into a silent removal of the whole staff
#: boundary, which is the one failure mode this gate exists to prevent. It is
#: not an option.
#:
#: "Nobody is staff" -- the fail-closed rule every other set in this row
#: follows -- is wrong HERE for a different reason: the staff check is ANDed
#: with `admin_emails`, so a blip that empties it locks every administrator
#: out of a live control plane, including the person who would fix it. Failing
#: closed on a *membership* list refuses one caller; failing closed on a
#: *domain* list refuses all of them at once, and there is no break-glass
#: account on this platform (ADR-0015).
#:
#: So the fallback is the narrowest possible non-empty answer: the one domain
#: this platform's staff actually sign in from. It cannot admit a stranger --
#: an outside address matches no domain here either way -- and it keeps
#: administration working while DynamoDB is unhappy. The config row can only
#: ever widen it, and widening it is a reviewed Terraform commit.
DEFAULT_STAFF_DOMAINS: tuple[str, ...] = ("stratevi.com",)


class HostTaken(Exception):
    """The conditional put lost: something already owns that hostname.

    Raised by :meth:`DynamoAppStore.reserve`. It is the ONLY thing standing
    between two simultaneous wizards and two apps claiming one public
    hostname, so it is an exception rather than a boolean -- a caller cannot
    forget to check it.
    """


def is_config_host(host: str) -> bool:
    """Is this partition key configuration rather than an app?

    Checked on the RAW value as well as the normalized one: normalization
    lowercases and strips a port, neither of which can remove a leading
    underscore, but a caller passing an already-normalized key should get the
    same answer as one passing a Host header.
    """
    return (host or "").strip().startswith(CONFIG_PREFIX) or normalize_host(
        host
    ).startswith(CONFIG_PREFIX)


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


def normalize_domain(domain: str | None) -> str:
    """Reduce one ``staff_domains`` entry to what an email can be compared to.

    Tolerates the three ways a human writes a domain into a Terraform list --
    ``stratevi.com``, ``@stratevi.com``, ``STRATEVI.COM.`` -- and nothing
    else. No wildcards: ``*.stratevi.com`` normalizes to a domain no address
    can equal, which is the safe way for an unsupported syntax to fail.
    """
    text = (domain or "").strip().lower()
    if text.startswith("@"):
        text = text[1:]
    return text.rstrip(".").strip()


def email_domain(email: str | None) -> str:
    """The domain part of an address, or ``""`` if there isn't exactly one.

    An address with no ``@``, with two of them, or with an empty local part
    has no domain this module will vouch for, and returning "" means every
    caller refuses it.
    """
    address = (email or "").strip().lower()
    if address.count("@") != 1:
        return ""
    local, _, domain = address.partition("@")
    if not local.strip():
        return ""
    return normalize_domain(domain)


def is_staff_email(email: str | None, domains: Iterable[str]) -> bool:
    """Is this address in one of ``domains``?

    **Exact domain equality, never a suffix test.** ``endswith(".com")``-style
    matching is how ``notstratevi.com`` and ``stratevi.com.evil.test`` get in;
    both must fail, and they do because the whole domain is compared as one
    string. Case-insensitive on both sides.
    """
    found = email_domain(email)
    if not found:
        return False
    return found in {
        cleaned for cleaned in (normalize_domain(d) for d in (domains or ())) if cleaned
    }


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
    # Presentation, for the portal's menu and admin list (ADR-0014). Both are
    # optional: the access decision never reads them, and an app seeded before
    # the portal existed simply has none. The menu falls back to app_key.
    label: str = ""
    description: str = ""
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
    # --- P2a creation (portal-p2a.md) ---------------------------------------
    # Everything below is written once by the creation wizard and read by the
    # build watcher. A row seeded before P2a has none of it, which is exactly
    # what a hand-provisioned app should look like: `status` is `active`,
    # there is no build to poll, and the watcher never touches it.
    #
    # cpu/memory are kept on the row rather than only in the task definition
    # because the task definition is registered AFTER the build succeeds --
    # by then the wizard's answer has to have survived somewhere.
    cpu: int = 0  # Fargate CPU units; 0 means "not portal-created"
    memory: int = 0  # MiB
    packages: tuple[str, ...] = field(default_factory=tuple)
    upload_key: str = ""  # uploads/<uuid>.zip, the build's input
    release_tag: str = ""  # r1, r2, ... -- P2b's version history hangs off this
    image: str = ""  # full ECR image URI including the tag
    build_id: str = ""  # CodeBuild build id, polled by the sleeper loop
    build_started_at: int = 0  # epoch seconds; the 45-minute reaper reads this
    build_error: str = ""  # why the last build or provisioning step failed
    created_by: str = ""  # the creator's email, for the audit trail
    created_at: int = 0  # epoch seconds

    @classmethod
    def create(
        cls,
        *,
        host: str,
        app_key: str = "",
        label: str = "",
        description: str = "",
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
        cpu: int | None = None,
        memory: int | None = None,
        packages: Iterable[str] | None = None,
        upload_key: str = "",
        release_tag: str = "",
        image: str = "",
        build_id: str = "",
        build_started_at: int | None = None,
        build_error: str = "",
        created_by: str = "",
        created_at: int | None = None,
    ) -> "App":
        """Build a row with defaults applied and comparisons pre-normalized."""
        return cls(
            host=normalize_host(host),
            app_key=(app_key or "").strip(),
            label=(label or "").strip(),
            description=(description or "").strip(),
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
            cpu=int(cpu or 0),
            memory=int(memory or 0),
            # Package NAMES only, order preserved: the wizard confirmed this
            # exact list and a rebuild has to reproduce it (portal-p2a.md).
            packages=tuple(
                cleaned for cleaned in (str(p).strip() for p in (packages or ())) if cleaned
            ),
            upload_key=(upload_key or "").strip(),
            release_tag=(release_tag or "").strip(),
            image=(image or "").strip(),
            build_id=(build_id or "").strip(),
            build_started_at=int(build_started_at or 0),
            build_error=(build_error or "").strip(),
            created_by=(created_by or "").strip().lower(),
            created_at=int(created_at or 0),
        )

    def display_label(self) -> str:
        """What a human should see this app called.

        Rows seeded before ``label`` existed have none, and a menu tile
        reading "" helps nobody.
        """
        return self.label or self.app_key or self.host

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

    async def patch(self, host: str, changes: dict[str, Any]) -> None:
        # Invalidated FIRST and again after: an admin who has just revoked
        # someone's access should not have to wait out a cache TTL, and a
        # concurrent request that repopulated the entry mid-write would
        # otherwise leave the old row cached for another ten seconds.
        self.invalidate(host)
        try:
            await self._store.patch(host, changes)  # type: ignore[attr-defined]
        finally:
            self.invalidate(host)

    async def admin_emails(self) -> tuple[str, ...]:
        # Not cached here: `portal.AdminList` owns that cache, because it is
        # the one that has to fail closed on an error rather than serve a
        # stale answer the way app rows do.
        return await self._store.admin_emails()  # type: ignore[attr-defined]

    async def creator_emails(self) -> tuple[str, ...]:
        # Same reasoning as admin_emails: `portal.CreatorList` owns the cache.
        return await self._store.creator_emails()  # type: ignore[attr-defined]

    async def key_denylist(self) -> tuple[str, ...]:
        return await self._store.key_denylist()  # type: ignore[attr-defined]

    async def staff_domains(self) -> tuple[str, ...]:
        # Same reasoning again: `portal.StaffDomains` owns the cache, and it is
        # the one that applies DEFAULT_STAFF_DOMAINS when this comes back empty
        # or raises.
        return await self._store.staff_domains()  # type: ignore[attr-defined]

    async def reserve(self, app: App) -> None:
        # Invalidated after, not before: until the conditional put succeeds
        # there is nothing new to see, and a cached MISS for this host is
        # exactly what a concurrent request should keep seeing while the
        # write is in flight.
        try:
            await self._store.reserve(app)  # type: ignore[attr-defined]
        finally:
            self.invalidate(app.host)

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
    if app.label:
        # Omitted rather than written empty, like every other optional
        # attribute here: an absent attribute and an empty string mean the
        # same thing and only one of them costs a byte.
        item["label"] = {"S": app.label}
    if app.description:
        item["description"] = {"S": app.description}
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
    # P2a creation attributes. Same rule as everything above: absent rather
    # than empty, so a hand-seeded row and a portal-created one that happens
    # to be uncapped are byte-identical.
    if app.packages:
        item["packages"] = {"SS": list(app.packages)}
    for name, text in (
        ("upload_key", app.upload_key),
        ("release_tag", app.release_tag),
        ("image", app.image),
        ("build_id", app.build_id),
        ("build_error", app.build_error),
        ("created_by", app.created_by),
    ):
        if text:
            item[name] = {"S": text}
    for name, number in (
        ("cpu", app.cpu),
        ("memory", app.memory),
        ("build_started_at", app.build_started_at),
        ("created_at", app.created_at),
    ):
        if number:
            item[name] = {"N": str(number)}
    return item


def app_from_item(item: dict[str, dict[str, Any]]) -> App:
    """Decode a DynamoDB item into a normalized row."""
    return App.create(
        host=_read_s(item, "host"),
        app_key=_read_s(item, "app_key"),
        label=_read_s(item, "label"),
        description=_read_s(item, "description"),
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
        cpu=_read_n(item, "cpu"),
        memory=_read_n(item, "memory"),
        packages=_read_strings(item, "packages"),
        upload_key=_read_s(item, "upload_key"),
        release_tag=_read_s(item, "release_tag"),
        image=_read_s(item, "image"),
        build_id=_read_s(item, "build_id"),
        build_started_at=_read_n(item, "build_started_at"),
        build_error=_read_s(item, "build_error"),
        created_by=_read_s(item, "created_by"),
        created_at=_read_n(item, "created_at"),
    )


def apps_from_items(
    items: Iterable[dict[str, dict[str, Any]]], log: Any | None = None
) -> list[App]:
    """Decode a table enumeration into app rows, skipping what is not an app.

    Two kinds of item must never reach a caller that thinks it is holding an
    app: a ``__``-prefixed configuration row (the portal's ``__config__``
    admin list lives in this table -- see :data:`CONFIG_PREFIX`), and an item
    so malformed that decoding it raises. The second is skipped rather than
    propagated because the caller is usually the sleeper, and one bad hand-
    edited row must not stop the whole platform being scaled down.
    """
    rows: list[App] = []
    for item in items:
        host = _read_s(item, "host")
        if not host or is_config_host(host):
            continue
        try:
            rows.append(app_from_item(item))
        except Exception as exc:  # pragma: no cover - defence in depth
            if log is not None:
                log.warning(
                    "skipping unparseable app row",
                    extra={"host": host, "reason": str(exc)},
                )
    return rows


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


# --- partial writes (the portal's PATCH) -----------------------------------

#: The attributes the portal's admin API may rewrite, in the contract's order
#: (docs/design/portal-api.md). Everything else about a row -- host, app_key,
#: ecs_service, container_port -- is provisioning, not configuration, and P2's
#: creation wizard owns it. Validation of the VALUES lives in `portal`; this
#: is only the encoding.
PATCHABLE_ATTRIBUTES = (
    "label",
    "description",
    "access_mode",
    "allowed_emails",
    "idle_minutes",
    "max_session_hours",
    "expires_at",
    "status",
)

#: Attributes the CREATION pipeline rewrites on a row it already reserved.
#: Deliberately NOT in :data:`PATCHABLE_ATTRIBUTES`: no admin PATCH may reach
#: them, and `portal.validate_patch` rejects them as unknown fields. They are
#: encodable here because the provisioner and the build watcher write them
#: through the same UpdateItem path.
#:
#: `host`, `app_key`, `ecs_service`, `container_port`, `cpu`, `memory`,
#: `packages` and `upload_key` are absent on purpose -- they are written once
#: by the conditional put that reserves the slug and are never edited. What a
#: build was made from must not change under it.
BUILD_ATTRIBUTES = (
    "build_id",
    "build_started_at",
    "build_error",
    "image",
)


def patch_value(name: str, value: Any) -> dict[str, Any] | None:
    """Encode one patched attribute, or ``None`` to REMOVE it.

    ``None`` is a real answer, not a failure: DynamoDB has no empty string
    set, and "no expiry" / "uncapped" / "no label" are all *absence* on the
    row -- exactly what :func:`app_item` writes for a fresh row. Encoding an
    empty value as a removal is what keeps a patched row byte-identical to a
    seeded one.
    """
    if name in ("label", "description", "access_mode", "status", "image", "build_id", "build_error"):
        text = str(value or "").strip()
        return {"S": text} if text else None
    if name == "build_started_at":
        number = int(value or 0)
        return {"N": str(number)} if number else None
    if name == "allowed_emails":
        emails = [str(e).strip().lower() for e in (value or ())]
        emails = [e for e in emails if e]
        return {"SS": emails} if emails else None
    if name == "idle_minutes":
        # Unlike the rest, absent does not mean "off" -- it means the
        # DEFAULT_IDLE_MINUTES fallback -- so this one is always written.
        return {"N": str(int(value))}
    if name in ("max_session_hours", "expires_at"):
        number = int(value or 0)
        return {"N": str(number)} if number else None
    raise KeyError(f"{name} is not a patchable attribute")


def patch_expression(
    changes: dict[str, Any],
) -> tuple[str, dict[str, str], dict[str, dict[str, Any]]]:
    """Build the UpdateItem arguments for a set of patched attributes.

    Every attribute goes through a ``#name`` placeholder rather than being
    interpolated: ``status`` is a DynamoDB reserved word, and so is anything
    else someone adds to :data:`PATCHABLE_ATTRIBUTES` in future without
    checking the (long) list.
    """
    sets: list[str] = []
    removes: list[str] = []
    names: dict[str, str] = {"#host": "host"}
    values: dict[str, dict[str, Any]] = {}

    for index, (name, value) in enumerate(changes.items()):
        encoded = patch_value(name, value)
        placeholder = f"#a{index}"
        names[placeholder] = name
        if encoded is None:
            removes.append(placeholder)
        else:
            sets.append(f"{placeholder} = :v{index}")
            values[f":v{index}"] = encoded

    clauses = []
    if sets:
        clauses.append("SET " + ", ".join(sets))
    if removes:
        clauses.append("REMOVE " + ", ".join(removes))
    return " ".join(clauses), names, values


# --- DynamoDB store --------------------------------------------------------


class DynamoAppStore:
    """:class:`AppStore` over shiny-proxy-apps, on a plain boto3 client.

    boto3 is synchronous, so every call goes through ``asyncio.to_thread``.
    That is deliberate: a handful of concurrent users at ~1 DynamoDB call per
    10s per host does not justify a second AWS SDK (aioboto3) in the image.
    botocore clients are safe to share across threads.
    """

    def __init__(self, client: Any, table: str, log: Any | None = None) -> None:
        self._client = client
        self._table = table
        self._log = log

    async def app(self, host: str) -> App | None:
        if is_config_host(host):
            # A Host header can never contain an underscore, so this can only
            # be a bug or someone poking at the table's namespace. Either way
            # a config row is not an app and must not be served as one.
            return None

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

        Configuration rows are filtered out here rather than at every call
        site: this is the only place the whole table is enumerated, so it is
        the one place that has to remember.
        """
        found: list[App] = []
        start_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {"TableName": self._table}
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            response = await asyncio.to_thread(self._client.scan, **kwargs)
            found.extend(apps_from_items(response.get("Items", ()), self._log))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return found

    async def admin_emails(self) -> tuple[str, ...]:
        """The ``__config__`` row's ``admin_emails`` string set.

        Raises rather than returning () on a read failure: the caller
        (`portal.AdminList`) has to be able to tell "the row says nobody" from
        "we could not ask", and only one of those should be cached.
        """
        return await self._config_strings(CONFIG_ADMIN_EMAILS)

    async def creator_emails(self) -> tuple[str, ...]:
        """The ``__config__`` row's ``creator_emails`` string set.

        A SEPARATE set from ``admin_emails`` (portal-p2a.md's decision): being
        an admin -- editing access, expiry and settings on apps that exist --
        does not grant the right to put a new hostname on the public internet
        and run someone's uploaded code in it. Same fail-closed handling.
        """
        return await self._config_strings(CONFIG_CREATOR_EMAILS)

    async def key_denylist(self) -> tuple[str, ...]:
        """Substrings banned from a public hostname.

        In the row rather than in code so brand, molecule and client names can
        be added without a deploy. Read the same way as the email sets, and
        the same fail-closed rule applies -- a read error means no key
        validates, not that every key does.
        """
        return await self._config_strings(CONFIG_KEY_DENYLIST)

    async def staff_domains(self) -> tuple[str, ...]:
        """The ``__config__`` row's ``staff_domains`` string set, as written.

        Returned RAW -- empty when the attribute is absent or empty, and
        raising when the read fails -- because this is the reader, not the
        policy. :data:`DEFAULT_STAFF_DOMAINS` is what "the row did not say"
        means, and `portal.StaffDomains` applies it: the caller has to be able
        to tell the two apart before it can decide.

        Entries are normalized on the way out (lowercased, a leading ``@`` and
        a trailing root dot removed) so a Terraform list written
        ``@Stratevi.com`` still matches an address.
        """
        return tuple(
            cleaned
            for cleaned in (
                normalize_domain(entry)
                for entry in await self._config_strings(CONFIG_STAFF_DOMAINS)
            )
            if cleaned
        )

    async def _config_strings(self, attribute: str) -> tuple[str, ...]:
        response = await asyncio.to_thread(
            self._client.get_item,
            TableName=self._table,
            Key={"host": {"S": CONFIG_HOST}},
        )
        item = response.get("Item") or {}
        return tuple(
            cleaned
            for cleaned in (
                str(e).strip().lower() for e in _read_strings(item, attribute)
            )
            if cleaned
        )

    async def reserve(self, app: App) -> None:
        """Write a NEW row, or raise :class:`HostTaken`.

        This conditional put is the whole concurrency story of app creation
        (portal-p2a.md, provisioning step 1). Two wizards submitting the same
        key at the same moment both pass the validate-key check -- it is a
        form affordance, not a lock -- and exactly one of them wins here. The
        loser gets a 409 and has provisioned nothing, because nothing else is
        attempted until this returns.
        """
        try:
            await asyncio.to_thread(
                self._client.put_item,
                TableName=self._table,
                Item=app_item(app),
                ConditionExpression="attribute_not_exists(#host)",
                ExpressionAttributeNames={"#host": "host"},
            )
        except Exception as exc:
            # Matched on the error CODE rather than the exception class, so
            # this module still imports nothing from botocore.
            code = ""
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                code = str((response.get("Error") or {}).get("Code") or "")
            if code == "ConditionalCheckFailedException":
                raise HostTaken(app.host) from exc
            raise

    async def patch(self, host: str, changes: dict[str, Any]) -> None:
        """Rewrite a subset of one row's attributes.

        Conditional on the row existing, so a PATCH against a host that was
        deleted between the read and the write fails loudly instead of
        creating a half-built app row with no ecs_service.
        """
        if not changes:
            return
        expression, names, values = patch_expression(changes)
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "Key": {"host": {"S": normalize_host(host)}},
            "UpdateExpression": expression,
            "ConditionExpression": "attribute_exists(#host)",
            "ExpressionAttributeNames": names,
        }
        if values:
            kwargs["ExpressionAttributeValues"] = values
        await asyncio.to_thread(self._client.update_item, **kwargs)

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
    "BUILD_ATTRIBUTES",
    "CONFIG_ADMIN_EMAILS",
    "CONFIG_CREATOR_EMAILS",
    "CONFIG_KEY_DENYLIST",
    "CONFIG_STAFF_DOMAINS",
    "CachedRegistry",
    "DEFAULT_STAFF_DOMAINS",
    "DynamoAppStore",
    "HostTaken",
    "STATUS_BUILDING",
    "STATUS_BUILD_FAILED",
    "app_from_item",
    "app_item",
    "apps_from_items",
    "email_domain",
    "is_config_host",
    "is_staff_email",
    "normalize_domain",
    "normalize_host",
    "patch_expression",
    "patch_value",
    "CONFIG_HOST",
    "CONFIG_PREFIX",
    "PATCHABLE_ATTRIBUTES",
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
