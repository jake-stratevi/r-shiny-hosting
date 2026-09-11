"""The portal: the menu everyone sees, and the admin control plane.

The contract is ``docs/design/portal-api.md`` and this module implements it
exactly -- the React bundle in ``portal-ui/`` is built against the same file,
so a change here that is not there is a bug in one of them.

It is not a second service. ADR-0014 pays $9/month for an always-on proxy
task; the portal is more routes on that task. What decides which face a
request gets is the Host header alone: a hostname listed in ``PORTAL_HOSTS``
is answered here, everything else is proxied to an app exactly as before.

Three things here are load-bearing:

* **The admin gate fails closed.** No ``__config__`` row, an unreadable one,
  or a caller with no resolvable email means nobody is an admin. The list is
  cached for ~30s so an admin page refresh is not a DynamoDB read per tile,
  and a *failure* is cached far more briefly than a success so a blip does
  not lock the control plane for half a minute.
* **``__``-prefixed rows are configuration, not apps.** They are skipped
  here, in `registry`, and in `sleeper`. The menu offering a tile called
  "__config__" would be funny once.
* **The operational pages stay plain HTML.** A 401 on a portal host renders
  the same embedded page the proxy serves everywhere else (portal.md's hard
  rule): those pages have to work when the JS bundle does not.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Protocol, Sequence
from urllib.parse import unquote

from aiohttp import web

from . import access, audit as audit_mod, identity, pages, provision, registry, security
from . import creation as creation_mod
from . import usage as usage_mod
from .audit import Event
from .ecsctl import ServiceState
from .identity import Principal
from .registry import App

#: Everything under here is JSON. Everything else on a portal host is the
#: React bundle (or its "not built yet" placeholder).
API_PREFIX = "/api/v1"

#: Vite's hashed build output. Treated differently from the rest of the
#: bundle in two ways: cached hard (the hash IS the cache key), and never
#: answered with index.html -- an SPA fallback on a missing asset turns a
#: broken deploy into a blank page and a "unexpected token '<'" in a console
#: nobody is reading.
ASSETS_PREFIX = "/assets/"

#: Presence is the whole check. The value is not a secret and is not verified:
#: a same-origin `fetch` can set a custom header trivially and a cross-site
#: HTML form cannot set one at all, which is exactly the attack this stops.
#: Auth remains the ALB's Cognito session; this only stops it being *used*
#: from another origin.
CSRF_HEADER = "X-Portal-Csrf"

#: The derived states, per the contract. `building` / `build_failed` are P2a's
#: additions -- a row exists from the moment its slug is reserved, minutes
#: before there is a service to describe.
LIVE_AWAKE = "awake"
LIVE_STARTING = "starting"
LIVE_ASLEEP = "asleep"
LIVE_DISABLED = "disabled"
LIVE_EXPIRED = "expired"
LIVE_BUILDING = "building"
LIVE_BUILD_FAILED = "build_failed"

#: Statuses the portal may write. `expired` is deliberately absent -- expiry
#: is the reaper's to declare, from the clock, and an admin who wants an app
#: gone sets `expires_at` or disables it.
WRITABLE_STATUSES = (registry.STATUS_ACTIVE, registry.STATUS_DISABLED)

#: Bounds from the contract.
MIN_IDLE_MINUTES = 1
MAX_IDLE_MINUTES = 1440
MIN_SESSION_HOURS = 0
MAX_SESSION_HOURS = 168

#: Not in the contract: DynamoDB items are capped at 400 KB and these strings
#: are rendered into a tile. Generous enough that no honest edit hits them.
MAX_LABEL_CHARS = 200
MAX_DESCRIPTION_CHARS = 2000
MAX_ALLOWED_EMAILS = 500

#: How long a `__config__` string set is trusted, and how long a FAILURE to
#: read one is. The second is much shorter on purpose: a cached success saves
#: reads, but a cached error locks every admin out of the control plane for
#: its duration.
ADMIN_TTL = 30.0
ADMIN_ERROR_TTL = 5.0

#: How many log lines the build screen shows. Enough to see the R error that
#: killed an install; not so many that a poll every few seconds ships a
#: megabyte.
BUILD_LOG_LINES = 50


# --- protocols the portal depends on ---------------------------------------


class PortalStore(Protocol):
    """The apps table, narrowed to what the portal does with it."""

    async def app(self, host: str) -> App | None: ...

    async def apps(self) -> list[App]: ...

    async def patch(self, host: str, changes: dict[str, Any]) -> None: ...


class StateSource(Protocol):
    """ECS, narrowed to the status badges."""

    async def states(self, services: Iterable[str]) -> dict[str, ServiceState]: ...


class AuditSource(Protocol):
    """The audit trail, narrowed to reading one host's page of events."""

    async def events(
        self, host: str, limit: int, start_key: dict | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]: ...


class RecorderLike(Protocol):
    def record(self, event: Event) -> None: ...


class AdminSource(Protocol):
    async def is_admin(self, email: str) -> bool: ...


class CreatorSource(Protocol):
    async def can_create(self, email: str) -> bool: ...


class UsageSource(Protocol):
    """The awake-hours ledger (usage.py). Absent means no costs screen."""

    async def month(
        self, apps: Sequence[App], year: int, month: int
    ) -> dict[str, "usage_mod.DayUsage"]: ...


class CreationLike(Protocol):
    """The P2a creation collaborator -- ``provision.Creation``."""

    domain: str
    uploads: Any
    provisioner: Any
    builds: Any
    denylist: Any


# --- who may do what -------------------------------------------------------


class ConfigList:
    """One ``__config__`` string set, cached and fail-closed.

    The sets are Terraform-managed ``aws_dynamodb_table_item`` attributes
    (ADR-0014), so granting yourself admin -- or the right to create apps --
    is a code review, not a console edit. Every way a read can go wrong (no
    row, no attribute, a DynamoDB error, a caller with no resolvable email)
    resolves to "no".
    """

    #: Named in the warning line, so "the portal is locked" and "creation is
    #: locked" are distinguishable in CloudWatch at 2am.
    attribute = registry.CONFIG_ADMIN_EMAILS

    def __init__(
        self,
        reader: Callable[[], Awaitable[Sequence[str]]],
        *,
        ttl: float = ADMIN_TTL,
        error_ttl: float = ADMIN_ERROR_TTL,
        log: logging.Logger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._reader = reader
        self._ttl = ttl if ttl > 0 else ADMIN_TTL
        self._error_ttl = error_ttl if error_ttl > 0 else ADMIN_ERROR_TTL
        self._log = log or logging.getLogger("proxy.portal")
        self._clock = clock
        self._cached: frozenset[str] = frozenset()
        self._read_at: float | None = None
        self._cached_ttl = 0.0
        #: False when the last read RAISED, as opposed to returning nothing.
        #: An empty membership list and an unreadable one both mean "no" for
        #: `has()`, but a validator that has to distinguish "this name is
        #: fine" from "we could not check" needs to tell them apart.
        self._ok = True

    @property
    def ok(self) -> bool:
        return self._ok

    async def emails(self) -> frozenset[str]:
        if self._read_at is not None and self._clock() - self._read_at < self._cached_ttl:
            return self._cached

        try:
            found = await self._reader()
            self._cached = frozenset(
                cleaned
                for cleaned in (str(e).strip().lower() for e in (found or ()))
                if cleaned
            )
            self._cached_ttl = self._ttl
            self._ok = True
            if not self._cached:
                # Not an error, but worth a line: it is the difference between
                # "the portal is locked" and "the portal is broken".
                self._log.warning(f"no {self.attribute} in the __config__ row")
        except Exception as exc:
            self._cached = frozenset()
            self._cached_ttl = self._error_ttl
            self._ok = False
            self._log.warning(
                f"cannot read {self.attribute}; failing closed",
                extra={"reason": str(exc)},
            )

        self._read_at = self._clock()
        return self._cached

    async def has(self, email: str) -> bool:
        address = (email or "").strip().lower()
        if not address:
            # A federated principal with only a synthetic Cognito username is
            # authenticated (and may see the menu) but can never be an admin
            # or a creator: there is no address to match against a reviewed
            # list.
            return False
        return address in await self.emails()


class AdminList(ConfigList):
    """``__config__.admin_emails``: who may edit apps that already exist."""

    attribute = registry.CONFIG_ADMIN_EMAILS

    async def is_admin(self, email: str) -> bool:
        return await self.has(email)


class CreatorList(ConfigList):
    """``__config__.creator_emails``: who may put a new hostname on the net.

    A SEPARATE permission from admin, and portal-p2a.md is explicit that
    admin does not imply it: editing who may open an app that exists is a
    different act from provisioning IAM, ECR and DNS-visible infrastructure
    and running an uploaded bundle in it. An admin who is not on this list
    gets a 403 from every P2a route, and `/me` reports `can_create: false`
    so the UI hides the affordance rather than dangling one.
    """

    attribute = registry.CONFIG_CREATOR_EMAILS

    async def can_create(self, email: str) -> bool:
        return await self.has(email)


class DenyList(ConfigList):
    """``__config__.key_denylist``: substrings banned from a hostname.

    Same machinery, same fail-closed rule, enforced through :attr:`ok`: a
    read that RAISED must not be reported as "no banned terms matched". A
    denylist that silently empties itself during a DynamoDB blip is how a
    client's name ends up in a public hostname, and unlike every other
    mistake in this pipeline that one cannot be taken back -- the name has
    already been in DNS and in somebody's browser history.
    """

    attribute = registry.CONFIG_KEY_DENYLIST

    async def terms(self) -> frozenset[str]:
        return await self.emails()


# --- derived state ---------------------------------------------------------


def live_state(app: App, state: ServiceState | None, now: float) -> str:
    """The badge: what is true about this app right now.

    Row first, ECS second, and expiry is read from the CLOCK rather than the
    status attribute -- the same rule the access decision uses, so the portal
    never shows "asleep" for an app that has in fact just lapsed and is being
    refused at the door.

    The two P2a states come FIRST, ahead of even expiry: a row that is still
    building has no service to describe and no meaningful expiry yet, and
    "asleep" for an app whose container does not exist would send a creator
    to click a link that 403s.
    """
    if app.status == registry.STATUS_BUILDING:
        return LIVE_BUILDING
    if app.status == registry.STATUS_BUILD_FAILED:
        return LIVE_BUILD_FAILED
    if app.status == registry.STATUS_EXPIRED or app.is_expired(now):
        return LIVE_EXPIRED
    if app.status == registry.STATUS_DISABLED:
        return LIVE_DISABLED
    if state is None or not state.exists or state.desired <= 0:
        return LIVE_ASLEEP
    return LIVE_AWAKE if state.running > 0 else LIVE_STARTING


def app_json(app: App, state: ServiceState | None, now: float) -> dict[str, Any]:
    """One app in the contract's admin shape.

    Epoch attributes are ``null`` rather than 0 when absent: 0 is a real
    moment in 1970 and a UI that formats it says so.
    """
    state = state or ServiceState()
    return {
        "host": app.host,
        "app_key": app.app_key,
        "label": app.label,
        "description": app.description,
        "ecs_service": app.ecs_service,
        "container_port": app.container_port,
        "status": app.status,
        "live_state": live_state(app, state, now),
        "access_mode": app.access_mode,
        "allowed_emails": list(app.allowed_emails),
        "idle_minutes": app.idle_minutes,
        "max_session_hours": app.max_session_hours,
        "expires_at": app.expires_at or None,
        "last_active": app.last_active or None,
        "awake_since": app.awake_since or None,
        "desired_count": state.desired,
        "running_count": state.running,
    }


def menu_json(app: App, state: ServiceState | None, now: float) -> dict[str, Any]:
    """One app in the contract's menu shape: what a tile needs, nothing more.

    Notably no ``allowed_emails``: the menu is served to every authenticated
    user, and who else can see an app is not their business.

    ``expires_at`` and ``last_active`` ARE here, though they read like admin
    detail. "The tool I use disappears in six days" is the reader's business
    too, and the tile shows it. Neither leaks anything: the caller is
    already entitled to open this app.
    """
    return {
        "host": app.host,
        "label": app.display_label(),
        "description": app.description,
        "url": f"https://{app.host}",
        "live_state": live_state(app, state, now),
        "expires_at": app.expires_at or None,
        "last_active": app.last_active or None,
    }


# --- PATCH validation ------------------------------------------------------


class PatchError(ValueError):
    """A body the contract does not allow. Always a 400, never a 500."""


def validate_patch(body: Any) -> dict[str, Any]:
    """Check a PATCH body against the contract and normalize what survives.

    Strict on purpose, and in this order: unknown fields are rejected before
    anything is written, so a typo ("idle_mins") fails loudly instead of
    silently changing nothing. Reserved access modes are rejected here rather
    than being stored and refused later by the gate -- a row the proxy would
    403 every request to is not a state an admin should be able to reach
    through the UI.
    """
    if not isinstance(body, dict):
        raise PatchError("body must be a JSON object")

    unknown = [name for name in body if name not in registry.PATCHABLE_ATTRIBUTES]
    if unknown:
        raise PatchError("unknown field(s): " + ", ".join(sorted(unknown)))

    if not body:
        raise PatchError("no fields to change")

    changes: dict[str, Any] = {}
    for name in registry.PATCHABLE_ATTRIBUTES:  # a stable order for the audit
        if name in body:
            changes[name] = _VALIDATORS[name](body[name])
    return changes


def _text(name: str, limit: int) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if not isinstance(value, str):
            raise PatchError(f"{name} must be a string")
        text = value.strip()
        if len(text) > limit:
            raise PatchError(f"{name} must be at most {limit} characters")
        return text

    return check


def _validate_access_mode(value: Any) -> str:
    if not isinstance(value, str):
        raise PatchError("access_mode must be a string")
    mode = value.strip().lower()
    if mode in registry.RESERVED_MODES:
        raise PatchError(
            f"access_mode {mode} is reserved for a later phase and the proxy "
            "refuses every request to an app carrying it"
        )
    if mode not in registry.IMPLEMENTED_MODES:
        raise PatchError("access_mode must be all_users or users")
    return mode


def _validate_allowed_emails(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise PatchError("allowed_emails must be a list")
    if len(value) > MAX_ALLOWED_EMAILS:
        raise PatchError(f"allowed_emails must hold at most {MAX_ALLOWED_EMAILS} addresses")

    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise PatchError("allowed_emails must be a list of strings")
        address = entry.strip().lower()
        if not address:
            continue
        if "@" not in address:
            raise PatchError(f"{entry!r} is not an email address")
        if address not in cleaned:
            cleaned.append(address)
    return tuple(cleaned)


def _whole_number(name: str, low: int, high: int) -> Callable[[Any], int]:
    def check(value: Any) -> int:
        # bool is an int in Python, and `"idle_minutes": true` is a mistake,
        # not a request for one minute.
        if isinstance(value, bool) or not isinstance(value, int):
            raise PatchError(f"{name} must be a whole number")
        if not low <= value <= high:
            raise PatchError(f"{name} must be between {low} and {high}")
        return value

    return check


def _validate_expires_at(value: Any) -> int:
    # null is the contract's "never", and 0 means the same thing on the row.
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise PatchError("expires_at must be epoch seconds or null")
    if value < 0:
        raise PatchError("expires_at must be epoch seconds or null")
    return value


def _validate_status(value: Any) -> str:
    if not isinstance(value, str):
        raise PatchError("status must be a string")
    status = value.strip().lower()
    if status not in WRITABLE_STATUSES:
        raise PatchError(
            "status must be active or disabled -- expiry is set through "
            "expires_at and declared by the reaper"
        )
    return status


_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "label": _text("label", MAX_LABEL_CHARS),
    "description": _text("description", MAX_DESCRIPTION_CHARS),
    "access_mode": _validate_access_mode,
    "allowed_emails": _validate_allowed_emails,
    "idle_minutes": _whole_number("idle_minutes", MIN_IDLE_MINUTES, MAX_IDLE_MINUTES),
    "max_session_hours": _whole_number(
        "max_session_hours", MIN_SESSION_HOURS, MAX_SESSION_HOURS
    ),
    "expires_at": _validate_expires_at,
    "status": _validate_status,
}


# --- the handler -----------------------------------------------------------


class Portal:
    """Serves every request that arrives on a portal hostname."""

    def __init__(
        self,
        *,
        apps: PortalStore,
        tasks: StateSource,
        admins: AdminSource,
        recorder: RecorderLike,
        audit: AuditSource | None = None,
        creators: CreatorSource | None = None,
        creation: CreationLike | None = None,
        usage: UsageSource | None = None,
        dist: str | Path | None = None,
        log: logging.Logger | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._apps = apps
        self._tasks = tasks
        self._admins = admins
        self._recorder = recorder
        self._audit = audit
        # Two separate collaborators on purpose: `creators` is the
        # PERMISSION (a `__config__` set, live the moment Terraform writes
        # it) and `creation` is the PIPELINE (env-driven, absent until the
        # P2a stack is applied). Someone can be a creator before there is
        # anything to create with, and the two failure messages are
        # different: 403 versus 503.
        self._creators = creators
        self._creation = creation
        # Absent on a deployment whose proxy predates the ledger, or in a
        # test that does not care: /costs answers 503 and nothing else
        # changes. The costs screen is a report, never a dependency.
        self._usage = usage
        self._dist = Path(dist).resolve() if dist else None
        self._log = log or logging.getLogger("proxy.portal")
        self._clock = clock

    # --- entry point -------------------------------------------------------

    async def handle(self, request: web.Request) -> web.StreamResponse:
        principal = identity.from_headers(request.headers)
        path = request.path

        if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
            return await self._api(request, principal, path[len(API_PREFIX) :])

        if not principal.authenticated:
            # The branded page, not JSON: this is a browser asking for the
            # app shell, and portal.md's hard rule keeps the operational
            # pages independent of the bundle.
            return pages.not_signed_in()

        return await self._static(request)

    # --- the JSON API ------------------------------------------------------

    async def _api(
        self, request: web.Request, principal: Principal, rest: str
    ) -> web.StreamResponse:
        if not principal.authenticated:
            # Should never happen behind the ALB's authenticate-cognito
            # action; it exists because failing closed means having something
            # to fail closed to.
            return _error(401, "not signed in")

        if rest in ("/me", "/me/"):
            return await self._me(request, principal)
        if rest in ("/menu", "/menu/"):
            return await self._menu(request, principal)
        if rest in ("/uploads", "/uploads/"):
            return await self._upload_url(request, principal)
        if rest in ("/costs", "/costs/"):
            return await self._costs(request, principal)
        # Matched BEFORE the `/apps/{host}` dispatcher, which would otherwise
        # read "validate-key" as a hostname and 404 it.
        if rest in ("/apps/validate-key", "/apps/validate-key/"):
            return await self._validate_key(request, principal)
        if rest in ("/apps", "/apps/"):
            return await self._apps_list(request, principal)
        if rest.startswith("/apps/"):
            return await self._one_app(request, principal, rest[len("/apps/") :])

        return _error(404, "no such endpoint")

    async def _me(self, request: web.Request, principal: Principal) -> web.Response:
        if request.method not in ("GET", "HEAD"):
            return _error(405, "method not allowed")
        return _json(
            200,
            {
                "email": principal.email,
                "is_admin": await self._admins.is_admin(principal.email),
                # Independent of is_admin, both ways: a creator need not be
                # an admin, and an admin is not a creator by default.
                "can_create": await self._may_create(principal),
            },
        )

    async def _menu(self, request: web.Request, principal: Principal) -> web.Response:
        """Every app this caller is entitled to, by the proxy's own rules.

        `access.decide` is reused rather than reimplemented, so the menu can
        never offer a tile that the proxy would then refuse -- which is the
        drift ADR-0013's catalog.yaml suffered from and this replaces.
        """
        if request.method not in ("GET", "HEAD"):
            return _error(405, "method not allowed")

        try:
            rows = await self._apps.apps()
        except Exception as exc:
            self._log.error("menu: cannot list apps", extra={"reason": str(exc)})
            return _error(503, "the application list is temporarily unavailable")

        now = self._clock()
        entitled = [
            app
            for app in rows
            if not registry.is_config_host(app.host)
            and access.decide(app, principal, now).allow
        ]
        states = await self._states_for(entitled)
        tiles = [menu_json(app, states.get(app.ecs_service), now) for app in entitled]
        tiles.sort(key=lambda tile: (tile["label"].lower(), tile["host"]))
        return _json(200, {"apps": tiles})

    async def _apps_list(
        self, request: web.Request, principal: Principal
    ) -> web.Response:
        if request.method == "POST":
            return await self._create_app(request, principal)
        if request.method not in ("GET", "HEAD"):
            return _error(405, "method not allowed")
        refused = await self._require_admin(principal)
        if refused is not None:
            return refused

        try:
            rows = [
                app
                for app in await self._apps.apps()
                if not registry.is_config_host(app.host)
            ]
        except Exception as exc:
            self._log.error("apps: cannot list apps", extra={"reason": str(exc)})
            return _error(503, "the application list is temporarily unavailable")

        now = self._clock()
        states = await self._states_for(rows)
        rows.sort(key=lambda app: (app.display_label().lower(), app.host))
        return _json(200, [app_json(app, states.get(app.ecs_service), now) for app in rows])

    async def _one_app(
        self, request: web.Request, principal: Principal, tail: str
    ) -> web.Response:
        """Dispatch ``/apps/{host}`` and ``/apps/{host}/audit``.

        Parsed by hand rather than through aiohttp's router because the proxy
        registers exactly one catch-all route: every host and every path
        reaches one handler, and adding a second router for one hostname
        would put two different route tables in the request path.

        The path is parsed BEFORE the gate, because the two gates differ:
        ``/build`` is a P2a route and wants creator permission (an admin
        alone is 403, per the contract), everything else wants admin.
        """
        segments = [unquote(part) for part in tail.split("/") if part]
        if not segments or len(segments) > 2:
            return _error(404, "no such endpoint")

        host = registry.normalize_host(segments[0])
        if not host or registry.is_config_host(host):
            # `__config__` is configuration, not an app, and must not be
            # editable through the app API.
            return _error(404, "no such application")

        if len(segments) == 2 and segments[1] == "build":
            if request.method not in ("GET", "HEAD"):
                return _error(405, "method not allowed")
            refused = await self._require_creator(principal)
            if refused is not None:
                return refused
            return await self._build_status(host)

        refused = await self._require_admin(principal)
        if refused is not None:
            return refused

        if len(segments) == 2:
            if segments[1] == "costs":
                if request.method not in ("GET", "HEAD"):
                    return _error(405, "method not allowed")
                return await self._app_costs(host)
            if segments[1] != "audit":
                return _error(404, "no such endpoint")
            if request.method not in ("GET", "HEAD"):
                return _error(405, "method not allowed")
            return await self._audit_page(request, host)

        if request.method in ("GET", "HEAD"):
            return await self._show_app(host)
        if request.method == "PATCH":
            return await self._patch_app(request, principal, host)
        return _error(405, "method not allowed")

    async def _show_app(self, host: str) -> web.Response:
        app, failure = await self._load(host)
        if failure is not None:
            return failure
        assert app is not None

        states = await self._states_for([app])
        return _json(200, app_json(app, states.get(app.ecs_service), self._clock()))

    async def _patch_app(
        self, request: web.Request, principal: Principal, host: str
    ) -> web.Response:
        # Before the body is even read: a cross-site form can POST a body,
        # but it cannot set a custom header.
        if not request.headers.get(CSRF_HEADER, "").strip():
            return _error(403, f"missing {CSRF_HEADER} header")

        try:
            body = json.loads(await request.text() or "null")
        except (ValueError, UnicodeDecodeError):
            return _error(400, "body must be a JSON object")

        try:
            changes = validate_patch(body)
        except PatchError as exc:
            return _error(400, str(exc))

        app, failure = await self._load(host)
        if failure is not None:
            return failure
        assert app is not None

        try:
            await self._apps.patch(host, changes)
        except Exception as exc:
            self._log.error(
                "patch failed",
                extra={"host": host, "email": principal.email, "reason": str(exc)},
            )
            return _error(503, "the change could not be saved")

        # Field NAMES, never values: an audit row must not become somewhere an
        # allowlist can be read out of. Best-effort like every other audit
        # write, and never deduplicated -- two identical edits a second apart
        # are two decisions somebody made.
        self._recorder.record(
            Event(
                host=host,
                event=audit_mod.EVENT_CONFIG_CHANGE,
                email=principal.email,
                path=",".join(changes),
            )
        )
        self._log.info(
            "config changed",
            extra={"host": host, "email": principal.email, "fields": list(changes)},
        )

        updated, failure = await self._load(host)
        if failure is not None:
            return failure
        assert updated is not None
        states = await self._states_for([updated])
        return _json(
            200, app_json(updated, states.get(updated.ecs_service), self._clock())
        )

    async def _audit_page(self, request: web.Request, host: str) -> web.Response:
        if self._audit is None:
            return _error(503, "the audit trail is unavailable")

        try:
            limit = int(request.query.get("limit") or audit_mod.DEFAULT_AUDIT_LIMIT)
        except ValueError:
            return _error(400, "limit must be a whole number")
        if limit < 1:
            return _error(400, "limit must be a whole number")

        try:
            start_key = audit_mod.decode_cursor(request.query.get("cursor") or "")
        except audit_mod.CursorError as exc:
            return _error(400, str(exc))

        try:
            events, last_key = await self._audit.events(host, limit, start_key)
        except Exception as exc:
            self._log.error(
                "audit read failed", extra={"host": host, "reason": str(exc)}
            )
            return _error(503, "the audit trail is temporarily unavailable")

        return _json(
            200, {"events": events, "cursor": audit_mod.encode_cursor(last_key)}
        )

    # --- costs -------------------------------------------------------------

    async def _costs(
        self, request: web.Request, principal: Principal
    ) -> web.Response:
        """Per-app awake hours and estimated compute cost, plus the shared line.

        Derived from the audit trail's wake/sleep events, not from Cost
        Explorer -- see usage.py's module docstring for why. Everything here
        is labelled an estimate on the wire, because it is one.
        """
        if request.method not in ("GET", "HEAD"):
            return _error(405, "method not allowed")
        refused = await self._require_admin(principal)
        if refused is not None:
            return refused
        if self._usage is None:
            return _error(503, "cost reporting is not configured on this deployment")

        try:
            rows = [
                app
                for app in await self._apps.apps()
                if not registry.is_config_host(app.host)
            ]
        except Exception as exc:
            self._log.error("costs: cannot list apps", extra={"reason": str(exc)})
            return _error(503, "the application list is temporarily unavailable")

        now = self._clock()
        periods, stale = await self._periods(rows, now)
        return _json(
            200,
            {
                "currency": "USD",
                "basis": "awake_time",
                "generated_at": int(now),
                "stale": stale,
                "rates": usage_mod.rates_block(),
                "disclaimer": usage_mod.DISCLAIMER,
                **periods,
            },
        )

    async def _app_costs(self, host: str) -> web.Response:
        """The same two periods, for one app, with a per-day breakdown."""
        if self._usage is None:
            return _error(503, "cost reporting is not configured on this deployment")

        app, failure = await self._load(host)
        if failure is not None:
            return failure
        assert app is not None

        now = self._clock()
        year, month = usage_mod.month_of(now)
        prev_year, prev_month = usage_mod.previous_month(year, month)

        try:
            current = await self._usage.month([app], year, month)
            previous = await self._usage.month([app], prev_year, prev_month)
        except Exception as exc:
            self._log.error(
                "costs: cannot read usage", extra={"host": host, "reason": str(exc)}
            )
            return _error(503, "cost data is temporarily unavailable")

        return _json(
            200,
            {
                "currency": "USD",
                "basis": "awake_time",
                "generated_at": int(now),
                "rates": usage_mod.rates_block(),
                "disclaimer": usage_mod.DISCLAIMER,
                "month_to_date": usage_mod.app_costs(app, current, year, month),
                "previous_month": usage_mod.app_costs(
                    app, previous, prev_year, prev_month
                ),
            },
        )

    async def _periods(
        self, rows: Sequence[App], now: float
    ) -> tuple[dict[str, Any], bool]:
        """Both blocks. A ledger failure degrades to zeroes plus ``stale``.

        Deliberately not a 503: an admin looking at a costs screen is better
        served by "these numbers may be incomplete" than by an error page,
        and the overhead line -- which is the larger number on this platform
        -- does not depend on the ledger at all.
        """
        year, month = usage_mod.month_of(now)
        prev_year, prev_month = usage_mod.previous_month(year, month)

        stale = False
        try:
            current = await self._usage.month(rows, year, month)  # type: ignore[union-attr]
        except Exception as exc:
            self._log.error("costs: month-to-date failed", extra={"reason": str(exc)})
            current, stale = {}, True
        try:
            previous = await self._usage.month(rows, prev_year, prev_month)  # type: ignore[union-attr]
        except Exception as exc:
            self._log.error("costs: previous month failed", extra={"reason": str(exc)})
            previous, stale = {}, True

        return (
            {
                "month_to_date": usage_mod.month_payload(
                    rows, current, year, month, now
                ),
                "previous_month": usage_mod.month_payload(
                    rows, previous, prev_year, prev_month, now
                ),
            },
            stale,
        )

    # --- P2a: creation -----------------------------------------------------

    async def _validate_key(
        self, request: web.Request, principal: Principal
    ) -> web.Response:
        """Is this key available? Always 200 -- it is a form affordance.

        No CSRF header required, and deliberately: this mutates nothing and
        discloses nothing a caller did not already supply. Every other P2a
        route does require it.

        It answers with ``host_preview``, not ``host``, and the difference is
        the honest part. A created app's hostname carries a random suffix
        minted at CREATE time (`creation.host_suffix`), and this route
        reserves nothing -- so there is no hostname yet to report. Returning
        a suffix here would either be a different one from the one the app
        gets, or would have to be sent back by the browser on create, which
        would let a caller choose it. Both defeat the point. So the wizard is
        given the SHAPE plus ``suffix_chars``, and says in words that the real
        suffix is added on create; the real hostname reaches the user on the
        build screen, which is where the finished link lives anyway.
        """
        if request.method != "POST":
            return _error(405, "method not allowed")

        refused = await self._require_creator(principal)
        if refused is not None:
            return refused
        assert self._creation is not None  # _require_creator checked

        body = await _body(request)
        if body is _BAD_JSON:
            return _error(400, "body must be a JSON object")
        if not isinstance(body, dict):
            return _error(400, "body must be a JSON object")

        key = body.get("key")
        reason = await self._key_problem(key)
        if reason is not None:
            return _json(200, {"ok": False, "reason": reason})

        return _json(
            200,
            {
                "ok": True,
                "host_preview": creation_mod.host_preview(
                    str(key).strip(), self._creation.domain
                ),
                "suffix_chars": creation_mod.HOST_SUFFIX_CHARS,
            },
        )

    async def _upload_url(
        self, request: web.Request, principal: Principal
    ) -> web.Response:
        """A presigned PUT for the bundle. The API never proxies the bytes."""
        if request.method != "POST":
            return _error(405, "method not allowed")

        refused = await self._require_creator(principal)
        if refused is not None:
            return refused
        assert self._creation is not None

        # CSRF matters more here than anywhere else in the portal: without
        # it, a page on another origin could make the victim's browser mint
        # a write grant into our bucket.
        if not request.headers.get(CSRF_HEADER, "").strip():
            return _error(403, f"missing {CSRF_HEADER} header")

        body = await _body(request)
        if body is _BAD_JSON:
            return _error(400, "body must be a JSON object")

        try:
            filename, size = creation_mod.validate_upload(body)
        except creation_mod.CreateError as exc:
            # Checked BEFORE anything is issued: an over-size bundle must be
            # a legible 400, not an S3 rejection the browser reports as an
            # opaque CORS error twenty minutes into an upload.
            return _error(400, str(exc))

        upload_key = self._creation.uploads.new_key()
        try:
            url = self._creation.uploads.presign(upload_key)
        except Exception as exc:
            self._log.error("cannot presign an upload", extra={"reason": str(exc)})
            return _error(503, "the upload could not be prepared")

        self._log.info(
            "upload url issued",
            # NOT `filename`: logging reserves that attribute on a LogRecord
            # and an `extra` that collides with one raises at the call site.
            extra={"email": principal.email, "key": upload_key,
                   "bundle": filename, "size": size},
        )
        return _json(
            200,
            {
                "upload_key": upload_key,
                "url": url,
                "expires_in": provision.UPLOAD_URL_SECONDS,
            },
        )

    async def _create_app(
        self, request: web.Request, principal: Principal
    ) -> web.Response:
        """The whole wizard, validated and then provisioned. 202 on success."""
        refused = await self._require_creator(principal)
        if refused is not None:
            return refused
        assert self._creation is not None

        if not request.headers.get(CSRF_HEADER, "").strip():
            return _error(403, f"missing {CSRF_HEADER} header")

        body = await _body(request)
        if body is _BAD_JSON:
            return _error(400, "body must be a JSON object")

        try:
            spec = creation_mod.validate_create(body)
        except creation_mod.CreateError as exc:
            return _error(400, str(exc))

        # The availability check again, server-side. The wizard's live check
        # is a courtesy; this is the one that counts -- and it is still not
        # the mutex. That is the conditional put below.
        reason = await self._key_problem(spec.key)
        if reason is not None:
            return _error(400, reason)

        try:
            created = await self._creation.provisioner.create(spec, principal.email)
        except registry.HostTaken:
            # Two wizards, one key, one winner. The loser provisioned
            # nothing: the conditional put is the first step for exactly
            # this reason.
            return _error(409, "that name was taken while you were submitting")
        except provision.ProvisionError as exc:
            # The row is already `build_failed` with the reason recorded, and
            # whatever was created is left for inspection (portal-p2a.md).
            return _error(503, f"the app could not be provisioned: {exc}")
        except Exception as exc:
            self._log.error(
                "create failed", extra={"email": principal.email, "reason": str(exc)}
            )
            return _error(503, "the app could not be created")

        return _json(202, app_json(created, None, self._clock()))

    async def _build_status(self, host: str) -> web.Response:
        """Build state for the wizard's build screen.

        Honest rather than reassuring (portal-p2a.md: "Do not fake a
        progress bar"): the CodeBuild phase, the elapsed seconds, a console
        deep link, and the tail of the log. The tail is best effort -- a
        build screen with no tail beats a 503.
        """
        assert self._creation is not None

        app, failure = await self._load(host)
        if failure is not None:
            return failure
        assert app is not None

        started = app.build_started_at or app.created_at
        payload: dict[str, Any] = {
            "state": _build_state(app.status),
            "phase": "",
            "started_at": started or None,
            "elapsed_s": max(0, int(self._clock() - started)) if started else 0,
            "log_url": self._creation.build_console_url(app.build_id)
            if hasattr(self._creation, "build_console_url")
            else "",
            "log_tail": [],
        }
        if app.build_error:
            payload["reason"] = app.build_error

        if not app.build_id:
            return _json(200, payload)

        try:
            status = await self._creation.builds.status(app.build_id)
        except Exception as exc:
            # The row's own status still answers the question well enough to
            # render a screen; only the phase and the tail are missing.
            self._log.warning(
                "cannot read build status",
                extra={"host": host, "build_id": app.build_id, "reason": str(exc)},
            )
            return _json(200, payload)

        payload["phase"] = status.phase
        if status.started_at:
            payload["started_at"] = status.started_at
            payload["elapsed_s"] = max(0, int(self._clock() - status.started_at))
        if status.log_url:
            payload["log_url"] = status.log_url
        # CodeBuild is the fresher answer while a build is live; the row only
        # catches up on the next sweep, up to a minute later.
        if app.status == registry.STATUS_BUILDING and status.finished():
            payload["state"] = "succeeded" if status.succeeded() else "failed"

        try:
            payload["log_tail"] = await self._creation.builds.log_tail(
                status, BUILD_LOG_LINES
            )
        except Exception as exc:  # pragma: no cover - log_tail is best effort
            self._log.warning("cannot read build logs", extra={"reason": str(exc)})

        return _json(200, payload)

    async def _key_problem(self, key: Any) -> str | None:
        """``None`` when the key may be used, otherwise the human reason."""
        assert self._creation is not None

        denylist = self._creation.denylist
        terms = await denylist.emails()
        if not getattr(denylist, "ok", True):
            # An unreadable denylist must never read as "nothing matched".
            # See DenyList's docstring: a name that reaches DNS cannot be
            # taken back.
            return "name checks are temporarily unavailable -- try again in a moment"

        try:
            rows = await self._apps.apps()
        except Exception as exc:
            self._log.error("cannot check key availability", extra={"reason": str(exc)})
            return "name checks are temporarily unavailable -- try again in a moment"

        taken = creation_mod.reserved_labels(
            (row.host for row in rows), (row.app_key for row in rows)
        )
        return creation_mod.check_key(key, taken=taken, denylist=terms)

    # --- shared helpers ----------------------------------------------------

    async def _may_create(self, principal: Principal) -> bool:
        if self._creators is None:
            return False
        try:
            return await self._creators.can_create(principal.email)
        except Exception as exc:  # pragma: no cover - the list fails closed itself
            self._log.warning(
                "cannot resolve create permission", extra={"reason": str(exc)}
            )
            return False

    async def _require_creator(self, principal: Principal) -> web.Response | None:
        """``None`` when the caller may create, otherwise the refusal.

        Two different refusals, deliberately: 403 means "you personally may
        not" and 503 means "nobody can yet, the pipeline is not deployed".
        Collapsing them would have a creator reading a permissions error
        during the window before the P2a Terraform is applied.
        """
        if not await self._may_create(principal):
            self._log.info(
                "portal create refused",
                extra={"email": principal.email, "sub": principal.sub},
            )
            return _error(403, "you are not permitted to create applications")
        if self._creation is None:
            return _error(503, "app creation is not configured on this deployment")
        return None

    async def _require_admin(self, principal: Principal) -> web.Response | None:
        """``None`` when the caller may proceed, otherwise the 403 to send."""
        if await self._admins.is_admin(principal.email):
            return None
        self._log.info(
            "portal admin refused",
            extra={"email": principal.email, "sub": principal.sub},
        )
        return _error(403, "you are not a platform administrator")

    async def _load(self, host: str) -> tuple[App | None, web.Response | None]:
        try:
            app = await self._apps.app(host)
        except Exception as exc:
            self._log.error(
                "app lookup failed", extra={"host": host, "reason": str(exc)}
            )
            return None, _error(503, "the application list is temporarily unavailable")
        if app is None:
            return None, _error(404, "no such application")
        return app, None

    async def _states_for(self, apps: Sequence[App]) -> dict[str, ServiceState]:
        """One batched, cached ECS read for a whole page of tiles.

        Never raises: the badge is the least important thing on the page, and
        `Controller.states` already degrades a describe failure to an empty
        state rather than an exception.
        """
        try:
            return await self._tasks.states([app.ecs_service for app in apps])
        except Exception as exc:
            self._log.warning("cannot describe services", extra={"reason": str(exc)})
            return {}

    # --- the React bundle --------------------------------------------------

    async def _static(self, request: web.Request) -> web.StreamResponse:
        """Serve the built bundle, with an SPA fallback to index.html.

        aiohttp serves files natively (``FileResponse`` handles range requests
        and conditional GETs), so the portal needs no web framework and no
        second dependency -- which is the whole reason the runtime image is
        still three pinned packages.
        """
        if request.method not in ("GET", "HEAD"):
            return _error(405, "method not allowed")

        index = self._dist / "index.html" if self._dist else None
        is_asset = request.path.startswith(ASSETS_PREFIX)

        if self._dist is not None:
            found = self._within_dist(request.path)
            if found is not None and found.is_file():
                return _file(found, immutable=is_asset)
            if not is_asset and index is not None and index.is_file():
                # Client-side routing: /admin, /apps/model, a deep link a user
                # bookmarked -- all of them are the same document.
                return _file(index, immutable=False)

        if is_asset:
            # An asset that is genuinely missing is a broken build, and
            # answering it with HTML only hides that.
            return _error(404, "not found")
        return _not_built()

    def _within_dist(self, path: str) -> Path | None:
        """Resolve a URL path inside the bundle directory, or ``None``.

        The containment check is on the RESOLVED path, so ``..`` segments and
        symlinks both land outside and are refused. Nothing here is served
        from user input otherwise -- but this is the one handler in the
        service that touches the filesystem, so it gets the paranoid version.
        """
        if self._dist is None:
            return None
        relative = path.lstrip("/")
        if not relative:
            return None
        try:
            candidate = (self._dist / relative).resolve()
        except (OSError, ValueError):
            return None
        if candidate == self._dist or self._dist in candidate.parents:
            return candidate
        return None


# --- request bodies --------------------------------------------------------

#: Sentinel: the body was not JSON at all, which is a different 400 from "the
#: JSON was JSON but wrong". A module-level object rather than an exception
#: so the read stays one line at every call site.
_BAD_JSON = object()


async def _body(request: web.Request) -> Any:
    try:
        return json.loads(await request.text() or "null")
    except (ValueError, UnicodeDecodeError):
        return _BAD_JSON


#: Row status -> the contract's build states. `active` is what a finished
#: build looks like from the row's side.
_BUILD_STATES = {
    registry.STATUS_BUILDING: "building",
    registry.STATUS_BUILD_FAILED: "failed",
}


def _build_state(status: str) -> str:
    return _BUILD_STATES.get(status, "succeeded")


# --- responses -------------------------------------------------------------


def _json(status: int, payload: Any) -> web.Response:
    return web.Response(
        status=status,
        text=json.dumps(payload, default=str),
        content_type="application/json",
        charset="utf-8",
        headers=security.headers(
            {
                # Entitlements and live state; a cached copy is a wrong copy.
                "Cache-Control": "no-store",
            }
        ),
    )


def _error(status: int, message: str) -> web.Response:
    """The contract's error body: ``{"error": "human-readable message"}``."""
    return _json(status, {"error": message})


def _file(path: Path, *, immutable: bool) -> web.FileResponse:
    # index.html is the portal document itself -- the one page on the platform
    # that carries the admin control plane -- so it gets the same
    # frame-ancestors treatment as everything else rather than being the one
    # response that is framable.
    headers = security.headers()
    if immutable:
        # Vite puts a content hash in every asset filename, so the URL changes
        # whenever the bytes do and a year is safe.
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        # index.html must NOT be cached, or a browser keeps loading the old
        # document (and its now-deleted asset URLs) after a deploy.
        headers["Cache-Control"] = "no-store"
    return web.FileResponse(path, headers=headers)


def _not_built() -> web.Response:
    """The placeholder for a backend deployed before the UI exists.

    Deliberately the same embedded template as the proxy's other operational
    pages: it renders with no bundle, no database and no ECS, which is the
    entire point of it.
    """
    return pages.render(
        200,
        title="Portal",
        heading="The portal interface is not built yet",
        paragraphs=[
            "The portal service is running and its API is answering. The "
            "web interface has not been built into this image.",
            "This is expected before the first portal-ui release; the JSON "
            "API under /api/v1 works regardless.",
        ],
        mono="portal-dist/index.html is missing",
    )


__all__ = [
    "API_PREFIX",
    "AdminList",
    "CSRF_HEADER",
    "ConfigList",
    "CreatorList",
    "DenyList",
    "LIVE_BUILDING",
    "LIVE_BUILD_FAILED",
    "PatchError",
    "Portal",
    "app_json",
    "live_state",
    "menu_json",
    "validate_patch",
]
