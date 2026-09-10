"""The request path: resolve the host, decide, wake, proxy.

Everything the handler needs from the outside world arrives through the small
protocols in `registry`, `ecsctl`, `activity` and `audit`, so it can be driven
with fakes and no AWS.

Two things here are load-bearing and easy to "tidy" into a bug:

* **No timeouts that can kill work.** The upstream ``ClientSession`` is built
  with ``ClientTimeout(total=None, sock_read=None)`` -- a Shiny
  ``downloadHandler`` can compute for minutes before it writes a single
  header, and a websocket is meant to stay open for hours. Only the TCP
  connect is bounded. Idle cleanup is the sleeper's job, not a socket timer's.
* **Nothing is buffered.** Request and response bodies are streamed chunk by
  chunk; Shiny sends progress output, SSE and download bodies in pieces that
  must reach the browser as they are produced.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Iterable, Protocol

import aiohttp
from aiohttp import WSMsgType, web
from multidict import CIMultiDict

from . import access, audit as audit_mod, identity, pages, registry
from .audit import Event
from .registry import App

#: The proxy's own namespace, reserved on EVERY host and never forwarded to an
#: app: the ALB health check needs somewhere to reach that does not depend on
#: any app existing, and an app must not be able to shadow it.
RESERVED_PREFIX = "/__proxy/"

#: Per RFC 9110; these describe a single hop and must not be relayed.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

#: Set by this proxy, so the incoming values are handled explicitly instead of
#: being copied twice.
_FORWARDED_HEADERS = frozenset({"host", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"})

#: The client negotiates these with the proxy, not with the app.
_WEBSOCKET_HEADERS = frozenset(
    {
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
        "sec-websocket-accept",
    }
)

#: Statuses that must not carry a body, whatever the upstream says.
_BODILESS_STATUSES = frozenset({204, 304})

#: Bounded so a wedged host cannot hold a request forever, but long enough for
#: a Fargate ENI that is still attaching.
CONNECT_TIMEOUT = 5.0

#: How long a readyz DynamoDB probe may take before it counts as not ready.
READY_TIMEOUT = 3.0

#: Detached wake calls get their own budget -- see :meth:`Proxy._wake`.
WAKE_TIMEOUT = 10.0


# --- protocols the handler depends on --------------------------------------


class RegistryLike(Protocol):
    async def app(self, host: str) -> App | None: ...

    async def set_awake_since(self, host: str, ts: int) -> None: ...


class TasksLike(Protocol):
    async def task_ip(self, service: str) -> str: ...

    async def wake(self, service: str) -> bool: ...

    def forget(self, service: str) -> None: ...


class ActivityLike(Protocol):
    def touch(self, host: str) -> None: ...

    def open_socket(self, host: str) -> Any: ...


class RecorderLike(Protocol):
    def record(self, event: Event) -> None: ...


class PortalLike(Protocol):
    """The portal, which answers everything on a portal hostname."""

    async def handle(self, request: web.Request) -> web.StreamResponse: ...


# --- "is anything listening yet" -------------------------------------------


class Prober:
    """Answers "is anything listening yet" for a task address.

    A freshly woken Fargate task has an ENI address a minute before R has
    finished loading packages, so an IP from ECS is not the same thing as a
    working app. One TCP connect settles it, and the answer is cached so a
    page full of assets does not dial forty times.
    """

    def __init__(
        self,
        *,
        ttl: float = 15.0,
        miss_ttl: float = 1.0,
        dial_timeout: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        dial: Callable[[str, int, float], Any] | None = None,
    ) -> None:
        self._ttl = ttl
        self._miss_ttl = miss_ttl
        self._dial_timeout = dial_timeout
        self._clock = clock
        self._dial = dial or _dial_tcp
        self._entries: dict[tuple[str, int], tuple[bool, float]] = {}

    async def ready(self, ip: str, port: int) -> bool:
        key = (ip, port)
        cached = self._entries.get(key)
        if cached is not None:
            ttl = self._ttl if cached[0] else self._miss_ttl
            if self._clock() - cached[1] < ttl:
                return cached[0]

        up = await self._dial(ip, port, self._dial_timeout)
        self._entries[key] = (up, self._clock())
        return up

    def forget(self, ip: str, port: int) -> None:
        self._entries.pop((ip, port), None)


async def _dial_tcp(ip: str, port: int, timeout: float) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout
        )
    except (OSError, asyncio.TimeoutError, TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:  # the peer hung up first; we only wanted the handshake
        pass
    return True


# --- the handler -----------------------------------------------------------


class Proxy:
    """Serves every request that reaches the proxy."""

    def __init__(
        self,
        *,
        apps: RegistryLike,
        tasks: TasksLike,
        activity: ActivityLike,
        recorder: RecorderLike,
        session: aiohttp.ClientSession,
        ready: Callable[[], Any] | None = None,
        log: logging.Logger | None = None,
        clock: Callable[[], float] = time.time,
        prober: Prober | None = None,
        portal: PortalLike | None = None,
        portal_hosts: Iterable[str] = (),
    ) -> None:
        self._apps = apps
        self._tasks = tasks
        self._activity = activity
        self._audit = recorder
        self._session = session
        self._ready = ready
        self._log = log or logging.getLogger("proxy.server")
        self._clock = clock
        self._prober = prober or Prober()
        self._portal = portal
        # Normalized the same way an incoming Host header is, so the
        # comparison in `handle` is a set membership test and not a parse.
        self._portal_hosts = frozenset(
            normalized
            for normalized in (registry.normalize_host(h) for h in portal_hosts)
            if normalized
        )
        # Strong refs to in-flight awake_since persists, same reason as
        # activity.Tracker's _pending: asyncio only holds weak refs to tasks,
        # and a garbage-collected one is a silently dropped write.
        self._pending: set[asyncio.Task[None]] = set()

    # --- entry point -------------------------------------------------------

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if request.path.startswith(RESERVED_PREFIX):
            return await self._reserved(request)

        host = registry.normalize_host(request.host)

        # A portal hostname is answered by the control plane, not proxied to
        # an app -- and is looked up BEFORE the apps table, so a portal host
        # needs no row and a row could not shadow it either way.
        if self._portal is not None and host in self._portal_hosts:
            return await self._portal.handle(request)

        try:
            app = await self._apps.app(host)
        except Exception as exc:
            # Fail closed. A registry we cannot read is a 503, never an
            # unauthenticated pass-through.
            self._log.error(
                "registry lookup failed", extra={"host": host, "reason": str(exc)}
            )
            return pages.unhealthy()

        principal = identity.from_headers(request.headers)
        decision = access.decide(app, principal, self._clock())

        if not decision.allow:
            return self._refuse(request, host, principal, decision)

        assert app is not None  # decide() 404s a missing row before allowing

        # A request counts as activity from the moment it is allowed, before
        # the app is even awake: someone waiting on the starting page is a user.
        self._activity.touch(host)
        self._audit.record(
            Event(
                host=host,
                event=audit_mod.EVENT_ALLOW,
                email=principal.email,
                path=request.path,
            )
        )

        try:
            ip = await self._tasks.task_ip(app.ecs_service)
        except Exception as exc:
            self._log.error(
                "task discovery failed",
                extra={"host": host, "service": app.ecs_service, "reason": str(exc)},
            )
            return pages.unhealthy()

        if not ip:
            return await self._wake(host, app)

        if not await self._prober.ready(ip, app.container_port):
            # The task exists but nothing is listening yet -- R is still
            # loading. Same page as a cold start, because to the person
            # waiting it is one.
            return pages.starting(app.app_key)

        if _is_websocket_upgrade(request):
            return await self._proxy_websocket(request, app, host, ip)
        return await self._proxy_http(request, app, ip)

    # --- refusal and wake --------------------------------------------------

    def _refuse(
        self,
        request: web.Request,
        host: str,
        principal: identity.Principal,
        decision: access.Decision,
    ) -> web.Response:
        self._audit.record(
            Event(
                host=host,
                event=audit_mod.EVENT_DENY,
                email=principal.email,
                path=request.path,
                outcome=decision.outcome,
            )
        )
        self._log.info(
            "refused",
            extra={
                "host": host,
                "outcome": decision.outcome,
                "status": decision.status,
                "email": principal.email,
                "sub": principal.sub,
                "path": request.path,
            },
        )

        if decision.outcome == access.OUTCOME_UNKNOWN_HOST:
            return pages.unknown_host(host)
        if decision.outcome == access.OUTCOME_UNAUTHENTICATED:
            return pages.not_signed_in()
        if decision.outcome == access.OUTCOME_EXPIRED:
            return pages.expired(disabled=False)
        if decision.outcome == access.OUTCOME_DISABLED:
            return pages.expired(disabled=True)
        return pages.no_access(principal.who())

    async def _wake(self, host: str, app: App) -> web.Response:
        """Ask ECS for one task and serve the starting page.

        Shielded from the request: if the user gives up and closes the tab
        mid-UpdateService the service should still come up -- they will be
        back, and a half-started wake is the worst of both worlds. Wake is
        idempotent, so every request during the 30-60s cold start lands here
        and only the one that actually changed desiredCount is audited.
        """
        try:
            woken = await asyncio.wait_for(
                asyncio.shield(self._tasks.wake(app.ecs_service)), WAKE_TIMEOUT
            )
        except Exception as exc:
            self._log.error(
                "wake failed",
                extra={"host": host, "service": app.ecs_service, "reason": str(exc)},
            )
            return pages.unhealthy()

        if woken:
            self._audit.record(Event(host=host, event=audit_mod.EVENT_WAKE))
            self._log.info(
                "waking app",
                extra={"host": host, "app_key": app.app_key, "service": app.ecs_service},
            )
            # C1's session cap is measured from here. Off the request path,
            # same best-effort pattern as activity.Tracker's last_active
            # writes: a failure here must not cost the user their page load.
            self._persist_awake_since(host)
        return pages.starting(app.app_key)

    def _persist_awake_since(self, host: str) -> None:
        now = self._clock()
        try:
            task = asyncio.get_running_loop().create_task(
                self._write_awake_since(host, now)
            )
        except RuntimeError:
            return  # no loop (a unit test calling _wake synchronously)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _write_awake_since(self, host: str, at: float) -> None:
        try:
            await self._apps.set_awake_since(host, int(at))
        except Exception as exc:  # never allowed to escape into the loop
            self._log.warning(
                "awake_since write failed", extra={"host": host, "reason": str(exc)}
            )

    # --- reserved endpoints ------------------------------------------------

    async def _reserved(self, request: web.Request) -> web.Response:
        if request.path == RESERVED_PREFIX + "healthz":
            # Liveness only, and unconditionally 200 while the process can
            # answer: the ALB target group uses this, and a proxy that
            # deregisters itself because DynamoDB blipped takes every app on
            # the platform down at once.
            return _plain(200, "ok")

        if request.path == RESERVED_PREFIX + "readyz":
            if self._ready is None:
                return _plain(200, "ready")
            try:
                await asyncio.wait_for(self._ready(), READY_TIMEOUT)
            except Exception as exc:
                self._log.warning("readyz failed", extra={"reason": str(exc)})
                return _plain(503, "not ready")
            return _plain(200, "ready")

        return _plain(404, "not found")

    # --- proxying ----------------------------------------------------------

    async def _proxy_http(
        self, request: web.Request, app: App, ip: str
    ) -> web.StreamResponse:
        target = request.url.with_scheme("http").with_host(ip).with_port(
            app.container_port
        )
        headers = _upstream_headers(request)

        try:
            upstream = await self._session.request(
                request.method,
                target,
                headers=headers,
                # Streamed straight through: an upload to a Shiny app is never
                # read into this process's memory.
                data=request.content if request.can_read_body else None,
                allow_redirects=False,
                # A 302 from the app belongs to the browser, not to us.
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=CONNECT_TIMEOUT, sock_read=None
                ),
            )
        except Exception as exc:
            return self._upstream_failed(request, app, ip, exc)

        async with upstream:
            response = web.StreamResponse(
                status=upstream.status,
                reason=upstream.reason,
                headers=_downstream_headers(upstream.headers),
            )

            body_allowed = (
                request.method != "HEAD"
                and upstream.status not in _BODILESS_STATUSES
                and not 100 <= upstream.status < 200
            )
            length = upstream.headers.get("Content-Length")
            if body_allowed:
                if length is not None:
                    try:
                        response.content_length = int(length)
                    except ValueError:
                        response.enable_chunked_encoding()
                else:
                    response.enable_chunked_encoding()

            await response.prepare(request)

            if body_allowed:
                try:
                    async for chunk in upstream.content.iter_any():
                        await response.write(chunk)
                except asyncio.CancelledError:
                    raise
                except (aiohttp.ClientError, OSError) as exc:
                    # Headers are already on the wire, so there is no branded
                    # page to serve; drop the caches and let the connection end.
                    self._forget(app, ip)
                    self._log.warning(
                        "upstream stream failed",
                        extra={"host": request.host, "path": request.path, "reason": str(exc)},
                    )
                    return response

            await response.write_eof()
            return response

    async def _proxy_websocket(
        self, request: web.Request, app: App, host: str, ip: str
    ) -> web.StreamResponse:
        target = request.url.with_scheme("http").with_host(ip).with_port(
            app.container_port
        )
        protocols = _requested_subprotocols(request)

        # max_msg_size=0 is unlimited on both halves: aiohttp's 4 MB default
        # would sever a Shiny session that ships a large plot or data frame.
        downstream = web.WebSocketResponse(
            protocols=protocols, max_msg_size=0, autoping=True, heartbeat=None
        )
        await downstream.prepare(request)

        # Counted for the whole life of the socket: an app with an open socket
        # is never slept, which is what retires the ADR-0006 heartbeat.
        with self._activity.open_socket(host):
            try:
                async with self._session.ws_connect(
                    target,
                    headers=_upstream_headers(request, websocket=True),
                    protocols=protocols,
                    max_msg_size=0,
                    autoping=True,
                    heartbeat=None,
                ) as upstream:
                    await _bridge(downstream, upstream)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._forget(app, ip)
                self._log.warning(
                    "websocket upstream failed",
                    extra={"host": host, "path": request.path, "reason": str(exc)},
                )
            finally:
                if not downstream.closed:
                    await downstream.close()

        return downstream

    def _upstream_failed(
        self, request: web.Request, app: App, ip: str, exc: BaseException
    ) -> web.Response:
        self._forget(app, ip)
        self._log.warning(
            "upstream failed",
            extra={"host": request.host, "path": request.path, "reason": str(exc)},
        )
        return pages.unhealthy()

    def _forget(self, app: App, ip: str) -> None:
        # The task has probably been replaced underneath us. Drop both caches
        # so the next request rediscovers rather than retrying a dead address
        # for another ten seconds.
        self._tasks.forget(app.ecs_service)
        self._prober.forget(ip, app.container_port)


# --- header plumbing -------------------------------------------------------


def _upstream_headers(request: web.Request, *, websocket: bool = False) -> CIMultiDict:
    """Headers to send to the app: everything except this hop's own."""
    skip = set(HOP_BY_HOP) | set(_FORWARDED_HEADERS) | _connection_tokens(request.headers)
    if websocket:
        skip |= _WEBSOCKET_HEADERS

    out: CIMultiDict = CIMultiDict()
    for name, value in request.headers.items():
        if name.lower() in skip:
            continue
        out.add(name, value)

    # The app sees the hostname the user typed, so anything it builds an
    # absolute URL from stays correct.
    out["Host"] = request.host
    out["X-Forwarded-Host"] = request.headers.get("X-Forwarded-Host") or request.host
    # Appended, not replaced: the ALB's entry has to survive or the app loses
    # the client address entirely.
    out["X-Forwarded-For"] = _forwarded_for(request)
    # The proxy is only ever reached through the ALB's HTTPS listener; the
    # ALB's own value wins when it sent one.
    out["X-Forwarded-Proto"] = request.headers.get("X-Forwarded-Proto") or "https"

    # The x-amzn-oidc-* headers are forwarded untouched. Each app keeps its own
    # allowlist behind this gate (ADR-0008's defence in depth stays), and
    # access.R needs them to make the same decision again.
    return out


def _downstream_headers(headers: Any) -> CIMultiDict:
    """Headers to relay back to the browser.

    Content-Length and Transfer-Encoding are dropped and re-derived: this hop
    decides its own framing. Duplicates (Set-Cookie) are preserved.
    """
    skip = set(HOP_BY_HOP) | {"content-length"} | _connection_tokens(headers)
    out: CIMultiDict = CIMultiDict()
    for name, value in headers.items():
        if name.lower() in skip:
            continue
        out.add(name, value)
    return out


def _connection_tokens(headers: Any) -> set[str]:
    tokens: set[str] = set()
    for value in headers.getall("Connection", ()) if hasattr(headers, "getall") else ():
        for token in value.split(","):
            token = token.strip().lower()
            if token:
                tokens.add(token)
    return tokens


def _forwarded_for(request: web.Request) -> str:
    chain = [
        value.strip()
        for value in request.headers.getall("X-Forwarded-For", ())
        if value.strip()
    ]
    if request.remote:
        chain.append(request.remote)
    return ", ".join(chain)


def _requested_subprotocols(request: web.Request) -> list[str]:
    raw = request.headers.get("Sec-WebSocket-Protocol", "")
    return [token.strip() for token in raw.split(",") if token.strip()]


def _is_websocket_upgrade(request: web.Request) -> bool:
    """Is this request trying to become a websocket?

    Which is every Shiny session, and the reason idle detection could never be
    done from HTTP request counts alone (ADR-0006).
    """
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return False
    return "upgrade" in _connection_tokens(request.headers)


async def _bridge(
    downstream: web.WebSocketResponse, upstream: aiohttp.ClientWebSocketResponse
) -> None:
    """Copy frames both ways until either side closes."""
    pumps = [
        asyncio.ensure_future(_pump(downstream, upstream)),
        asyncio.ensure_future(_pump(upstream, downstream)),
    ]
    try:
        await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for pump in pumps:
            pump.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)


async def _pump(source: Any, sink: Any) -> None:
    async for message in source:
        if message.type == WSMsgType.TEXT:
            await sink.send_str(message.data)
        elif message.type == WSMsgType.BINARY:
            await sink.send_bytes(message.data)
        else:
            # CLOSE / CLOSING / CLOSED / ERROR: the conversation is over.
            # PING and PONG never surface here -- autoping handles them.
            break


# --- plumbing --------------------------------------------------------------


def _plain(status: int, body: str) -> web.Response:
    return web.Response(
        status=status,
        text=body + "\n",
        content_type="text/plain",
        charset="utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def make_session() -> aiohttp.ClientSession:
    """The upstream client, configured for long-running Shiny work.

    * ``total=None`` / ``sock_read=None``: no deadline on a response. A model
      run that takes twenty minutes must not be truncated at ninety seconds.
    * ``auto_decompress=False``: whatever encoding the app chose is relayed
      byte for byte.
    * ``skip_auto_headers``: aiohttp would otherwise add its own
      ``Accept-Encoding`` and ``User-Agent``, and an app could then gzip a
      response for a client that never asked for gzip.
    """
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(
            total=None, sock_connect=CONNECT_TIMEOUT, sock_read=None
        ),
        auto_decompress=False,
        skip_auto_headers=("Accept-Encoding", "User-Agent"),
        connector=aiohttp.TCPConnector(limit=0, force_close=False),
    )


def create_app(proxy: Proxy, *, client_max_size: int = 1024**3) -> web.Application:
    """One catch-all route: every host, every path, every method.

    ``client_max_size`` only bounds aiohttp's own body readers, which this
    handler does not use -- bodies are streamed. It is set high anyway so a
    large upload to a Shiny app cannot be refused before it starts.
    """
    application = web.Application(client_max_size=client_max_size)
    application.router.add_route("*", "/{tail:.*}", proxy.handle)
    return application


__all__ = [
    "PortalLike",
    "Prober",
    "Proxy",
    "RESERVED_PREFIX",
    "create_app",
    "make_session",
]
