"""Header plumbing, the reserved /__proxy/ namespace, and the branded pages.

No sockets and no AWS: aiohttp's ``make_mocked_request`` is enough to drive
the parts of the request path that are decisions rather than I/O.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

from proxy_app import pages, server
from proxy_app.identity import NO_EMAIL_LABEL
from proxy_app.registry import App


class Stub:
    """A stand-in for the parts of a request ``_forwarded_for`` reads."""

    def __init__(self, headers, remote=None):
        self.headers = CIMultiDict(headers)
        self.remote = remote


def request(method="GET", path="/", headers=None):
    return make_mocked_request(
        method, path, headers=CIMultiDict(headers or {"Host": "model.tools.stratevi.com"})
    )


def proxy(**overrides) -> server.Proxy:
    defaults = dict(apps=None, tasks=None, activity=None, recorder=None, session=None)
    defaults.update(overrides)
    return server.Proxy(**defaults)


async def settle() -> None:
    """Let the proxy's fire-and-forget awake_since persist task run."""
    for _ in range(3):
        await asyncio.sleep(0)


class FakeRegistryForWake:
    """Just enough of RegistryLike to drive ``_wake``."""

    def __init__(self, *, fail: bool = False) -> None:
        self.awake_since_writes: list[tuple[str, int]] = []
        self.fail = fail

    async def app(self, host: str):
        return None

    async def set_awake_since(self, host: str, ts: int) -> None:
        if self.fail:
            raise RuntimeError("dynamodb is unhappy")
        self.awake_since_writes.append((host, ts))


class FakeTasksForWake:
    def __init__(self, *, woken: bool) -> None:
        self._woken = woken

    async def task_ip(self, service: str) -> str:
        return ""

    async def wake(self, service: str) -> bool:
        return self._woken

    def forget(self, service: str) -> None:
        pass


class FakeRecorderForWake:
    def __init__(self) -> None:
        self.events = []

    def record(self, event) -> None:
        self.events.append(event)


def wake_row() -> App:
    return App.create(host="model.tools.stratevi.com", app_key="model", ecs_service="shiny-model")


# --- the reserved namespace ------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_is_unconditionally_200_even_with_no_dependencies():
    async def always_broken():
        raise RuntimeError("dynamodb is unhappy")

    response = await proxy(ready=always_broken).handle(
        request(path="/__proxy/healthz")
    )
    assert response.status == 200
    assert response.text.strip() == "ok"


@pytest.mark.asyncio
async def test_healthz_works_on_any_host_including_one_with_no_app():
    response = await proxy().handle(
        request(path="/__proxy/healthz", headers={"Host": "nothing-here.example"})
    )
    assert response.status == 200


@pytest.mark.asyncio
async def test_readyz_probes_the_store():
    calls = []

    async def ping():
        calls.append(1)

    response = await proxy(ready=ping).handle(request(path="/__proxy/readyz"))
    assert response.status == 200
    assert response.text.strip() == "ready"
    assert calls == [1]


@pytest.mark.asyncio
async def test_readyz_is_503_when_the_store_is_unreachable():
    async def broken():
        raise RuntimeError("dynamodb is unhappy")

    response = await proxy(ready=broken).handle(request(path="/__proxy/readyz"))
    assert response.status == 503
    assert response.text.strip() == "not ready"


@pytest.mark.asyncio
async def test_readyz_times_out_rather_than_hanging_the_health_check(monkeypatch):
    async def never():
        await asyncio.sleep(3600)

    monkeypatch.setattr(server, "READY_TIMEOUT", 0.05)
    response = await proxy(ready=never).handle(request(path="/__proxy/readyz"))
    assert response.status == 503


@pytest.mark.asyncio
async def test_an_unknown_reserved_path_is_404_and_never_forwarded():
    """The prefix is reserved on every host; an app must not be able to shadow it."""
    response = await proxy().handle(request(path="/__proxy/anything-else"))
    assert response.status == 404
    assert response.text.strip() == "not found"


# --- wake persists awake_since (C1) -----------------------------------------


@pytest.mark.asyncio
async def test_a_real_wake_persists_awake_since_off_the_request_path():
    apps = FakeRegistryForWake()
    handler = proxy(
        apps=apps,
        tasks=FakeTasksForWake(woken=True),
        recorder=FakeRecorderForWake(),
        clock=lambda: 1_700_000_000.0,
    )

    response = await handler._wake("model.tools.stratevi.com", wake_row())
    assert response.status == 200  # the starting page; wake never fails the request

    await settle()
    assert apps.awake_since_writes == [("model.tools.stratevi.com", 1_700_000_000)]


@pytest.mark.asyncio
async def test_an_idempotent_wake_does_not_re_persist_awake_since():
    """Every request during a 30-60s cold start calls _wake; only the one that
    actually transitions desiredCount 0->1 should touch the row."""
    apps = FakeRegistryForWake()
    handler = proxy(
        apps=apps,
        tasks=FakeTasksForWake(woken=False),
        recorder=FakeRecorderForWake(),
        clock=lambda: 1_700_000_000.0,
    )

    await handler._wake("model.tools.stratevi.com", wake_row())
    await settle()

    assert apps.awake_since_writes == []


@pytest.mark.asyncio
async def test_a_failing_awake_since_write_does_not_escape_the_wake_path():
    apps = FakeRegistryForWake(fail=True)
    handler = proxy(
        apps=apps,
        tasks=FakeTasksForWake(woken=True),
        recorder=FakeRecorderForWake(),
        clock=lambda: 1_700_000_000.0,
    )

    response = await handler._wake("model.tools.stratevi.com", wake_row())
    await settle()  # must not raise

    assert response.status == 200


# --- header plumbing -------------------------------------------------------


def test_hop_by_hop_headers_are_not_relayed_upstream():
    out = server._upstream_headers(
        request(
            headers={
                "Host": "model.tools.stratevi.com",
                "Connection": "keep-alive, x-custom-hop",
                "Keep-Alive": "timeout=5",
                "Transfer-Encoding": "chunked",
                "X-Custom-Hop": "dropped by the Connection list",
                "Accept": "text/html",
            }
        )
    )
    for gone in ("Connection", "Keep-Alive", "Transfer-Encoding", "X-Custom-Hop"):
        assert gone not in out
    assert out["Accept"] == "text/html"


def test_the_alb_identity_headers_are_forwarded_untouched():
    """access.R still makes its own decision behind this gate (ADR-0008)."""
    out = server._upstream_headers(
        request(
            headers={
                "Host": "model.tools.stratevi.com",
                "x-amzn-oidc-data": "header.payload.signature",
                "x-amzn-oidc-identity": "sub-123",
                "x-amzn-oidc-accesstoken": "a.b.c",
            }
        )
    )
    assert out["x-amzn-oidc-data"] == "header.payload.signature"
    assert out["x-amzn-oidc-identity"] == "sub-123"
    assert out["x-amzn-oidc-accesstoken"] == "a.b.c"


def test_the_app_sees_the_hostname_the_user_typed():
    out = server._upstream_headers(request(headers={"Host": "model.tools.stratevi.com"}))
    assert out["Host"] == "model.tools.stratevi.com"
    assert out["X-Forwarded-Host"] == "model.tools.stratevi.com"


def test_x_forwarded_proto_defaults_to_https_and_respects_the_albs_value():
    out = server._upstream_headers(request(headers={"Host": "a.b"}))
    assert out["X-Forwarded-Proto"] == "https"

    out = server._upstream_headers(
        request(headers={"Host": "a.b", "X-Forwarded-Proto": "https"})
    )
    assert out.getall("X-Forwarded-Proto") == ["https"]


def test_x_forwarded_for_is_appended_not_replaced():
    assert server._forwarded_for(Stub({"X-Forwarded-For": "203.0.113.7"}, "10.0.1.5")) == (
        "203.0.113.7, 10.0.1.5"
    )
    assert server._forwarded_for(Stub({}, "10.0.1.5")) == "10.0.1.5"
    assert server._forwarded_for(Stub({"X-Forwarded-For": "203.0.113.7"}, None)) == (
        "203.0.113.7"
    )


def test_websocket_negotiation_headers_stop_at_the_proxy():
    out = server._upstream_headers(
        request(
            headers={
                "Host": "a.b",
                "Upgrade": "websocket",
                "Connection": "Upgrade",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Protocol": "shiny",
            }
        ),
        websocket=True,
    )
    for gone in (
        "Upgrade",
        "Connection",
        "Sec-WebSocket-Key",
        "Sec-WebSocket-Version",
        "Sec-WebSocket-Protocol",
    ):
        assert gone not in out


def test_response_headers_drop_the_frame_and_keep_every_set_cookie():
    upstream = CIMultiDict(
        [
            ("Content-Length", "42"),
            ("Transfer-Encoding", "chunked"),
            ("Connection", "keep-alive"),
            ("Content-Type", "text/html"),
            ("Set-Cookie", "a=1"),
            ("Set-Cookie", "b=2"),
        ]
    )
    out = server._downstream_headers(upstream)
    assert "Content-Length" not in out
    assert "Transfer-Encoding" not in out
    assert "Connection" not in out
    assert out["Content-Type"] == "text/html"
    assert out.getall("Set-Cookie") == ["a=1", "b=2"]


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"Upgrade": "websocket", "Connection": "Upgrade"}, True),
        ({"Upgrade": "WebSocket", "Connection": "keep-alive, Upgrade"}, True),
        ({"Upgrade": "websocket"}, False),
        ({"Connection": "Upgrade"}, False),
        ({}, False),
        ({"Upgrade": "h2c", "Connection": "Upgrade"}, False),
    ],
)
def test_websocket_upgrade_detection(headers, expected):
    headers = {"Host": "a.b", **headers}
    assert server._is_websocket_upgrade(request(headers=headers)) is expected


# --- the branded pages -----------------------------------------------------


def test_the_starting_page_is_200_with_retry_after_and_a_meta_refresh():
    """503 makes some browsers cache the failure; 200 + meta-refresh does not."""
    response = pages.starting("model")
    assert response.status == 200
    assert response.headers["Retry-After"] == "3"
    assert '<meta http-equiv="refresh" content="3">' in response.text
    assert "30 to 60 seconds" in response.text
    assert "<code>model</code>" in response.text


@pytest.mark.parametrize(
    "response,status",
    [
        (pages.not_signed_in(), 401),
        (pages.no_access("jake@stratevi.com"), 403),
        (pages.expired(disabled=False), 410),
        (pages.expired(disabled=True), 410),
        (pages.unknown_host("nope.tools.stratevi.com"), 404),
        (pages.unhealthy(), 503),
    ],
)
def test_each_page_carries_its_status_and_is_never_cached(response, status):
    assert response.status == status
    assert response.content_type == "text/html"
    assert response.headers["Cache-Control"] == "no-store, must-revalidate"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Stratevi" in response.text
    # No external assets: these have to render when the database is unhappy
    # and every app is asleep.
    assert "http://" not in response.text and "https://" not in response.text


def test_the_expired_and_disabled_pages_say_different_things():
    assert "expired" in pages.expired(disabled=False).text
    assert "turned off" in pages.expired(disabled=True).text


def test_untrusted_values_are_escaped_into_the_page():
    response = pages.unknown_host('evil".tools<script>alert(1)</script>')
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_the_no_access_page_never_shows_a_synthetic_username_as_a_name():
    assert NO_EMAIL_LABEL in pages.no_access(NO_EMAIL_LABEL).text


# --- portal hosts (ADR-0014's second half) ---------------------------------


class FakePortal:
    """Stands in for portal.Portal behind server.PortalLike."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    async def handle(self, request):
        self.paths.append(request.path)
        return web.Response(status=200, text="portal")


class FakeRegistryReturningNothing:
    async def app(self, host: str):
        return None

    async def set_awake_since(self, host: str, ts: int) -> None:
        pass


def routed(portal_hosts=("dashboards.tools.stratevi.com",)):
    portal = FakePortal()
    handler = proxy(
        apps=FakeRegistryReturningNothing(),
        recorder=FakeRecorderForWake(),
        portal=portal,
        portal_hosts=portal_hosts,
    )
    return handler, portal


@pytest.mark.asyncio
async def test_a_portal_host_is_answered_by_the_portal_not_proxied():
    handler, portal = routed()
    response = await handler.handle(
        request(path="/admin", headers={"Host": "dashboards.tools.stratevi.com"})
    )
    assert response.status == 200
    assert portal.paths == ["/admin"]


@pytest.mark.parametrize(
    "host",
    ["Dashboards.Tools.Stratevi.com", "dashboards.tools.stratevi.com:443",
     "dashboards.tools.stratevi.com."],
)
@pytest.mark.asyncio
async def test_a_portal_host_is_matched_however_it_arrives(host):
    handler, portal = routed()
    await handler.handle(request(path="/", headers={"Host": host}))
    assert portal.paths == ["/"]


@pytest.mark.asyncio
async def test_an_app_host_is_still_proxied_with_the_portal_running():
    handler, portal = routed()
    response = await handler.handle(
        request(path="/", headers={"Host": "model.tools.stratevi.com"})
    )
    # No row for that host, so the branded 404 -- the app path, untouched.
    assert response.status == 404
    assert portal.paths == []


@pytest.mark.asyncio
async def test_the_reserved_namespace_wins_even_on_a_portal_host():
    """The ALB health check must not depend on the portal answering."""
    handler, portal = routed()
    response = await handler.handle(
        request(
            path="/__proxy/healthz", headers={"Host": "dashboards.tools.stratevi.com"}
        )
    )
    assert response.status == 200
    assert response.text.strip() == "ok"
    assert portal.paths == []


@pytest.mark.asyncio
async def test_with_no_portal_configured_every_host_is_an_app_host():
    handler = proxy(
        apps=FakeRegistryReturningNothing(), recorder=FakeRecorderForWake()
    )
    response = await handler.handle(
        request(path="/", headers={"Host": "dashboards.tools.stratevi.com"})
    )
    assert response.status == 404
