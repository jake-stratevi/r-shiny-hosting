"""Framing protection: every response class, and the proxied path for real.

The unit half drives `security` and the places that call it. The integration
half (bottom of the file) runs an actual Shiny-shaped upstream behind an
actual proxy over an actual socket, because two of the claims here cannot be
proved with a mocked request:

* that the headers survive the streaming response path, where
  `Proxy._proxy_http` prepares and writes the response itself -- the reason
  this is not an aiohttp middleware;
* that adding them did not disturb the websocket upgrade, which is how every
  Shiny session actually talks.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, make_mocked_request
from multidict import CIMultiDict

from proxy_app import pages, portal, security, server
from proxy_app.registry import App

CSP = security.CSP_HEADER
XFO = security.FRAME_OPTIONS_HEADER
REFERRER = security.REFERRER_HEADER
NOSNIFF = security.CONTENT_TYPE_OPTIONS_HEADER

HOST = "tarpeyo-4m2xqz.tools.stratevi.com"
PORTAL_HOST = "shinyplatform.tools.stratevi.com"


def assert_framing_denied(headers) -> None:
    """The contract, in one place: exactly one of each, saying no."""
    policies = headers.getall(CSP)
    # Exactly one CSP header, whatever the app sent, and ours is the last
    # policy in it -- see `security.harden`.
    assert len(policies) == 1, policies
    assert policies[0].endswith(security.FRAME_ANCESTORS)
    assert headers.getall(XFO) == [security.FRAME_OPTIONS]
    assert len(headers.getall(REFERRER)) == 1


# --- the headers themselves -------------------------------------------------


def test_the_policy_is_frame_ancestors_and_nothing_else():
    """Deliberate. A `script-src` here would break every Shiny app on the
    platform -- Shiny writes inline <script> into the document it serves."""
    assert security.FRAME_ANCESTORS == "frame-ancestors 'none'"
    assert "script-src" not in security.FRAME_ANCESTORS
    assert "default-src" not in security.FRAME_ANCESTORS


def test_every_response_this_service_builds_carries_all_four():
    assert security.headers() == {
        CSP: "frame-ancestors 'none'",
        XFO: "DENY",
        REFERRER: "strict-origin-when-cross-origin",
        NOSNIFF: "nosniff",
    }


def test_a_caller_can_add_its_own_headers_without_losing_the_security_ones():
    built = security.headers({"Cache-Control": "no-store", "Retry-After": "3"})
    assert built["Cache-Control"] == "no-store"
    assert built[CSP] == security.FRAME_ANCESTORS
    assert built[XFO] == "DENY"


# --- relayed responses: what happens to what the app sent -------------------


def harden(pairs) -> CIMultiDict:
    out = CIMultiDict(pairs)
    security.harden(out)
    return out


def test_an_app_that_sends_nothing_gets_the_full_set():
    out = harden([("Content-Type", "text/html")])
    assert out[CSP] == security.FRAME_ANCESTORS
    assert out[XFO] == "DENY"
    assert out[REFERRER] == security.REFERRER_POLICY
    assert out["Content-Type"] == "text/html"


def test_an_apps_own_x_frame_options_is_replaced_not_joined():
    """Two X-Frame-Options values is not "more secure", it is undefined --
    the header has no composition rule. The app's value goes."""
    out = harden([(XFO, "SAMEORIGIN"), ("Content-Type", "text/html")])
    assert out.getall(XFO) == ["DENY"]


def test_an_app_cannot_opt_its_users_out_of_framing_protection():
    """The whole reason the proxy overrides rather than defers."""
    out = harden([(XFO, "ALLOWALL"), (CSP, "frame-ancestors *")])
    assert out.getall(XFO) == ["DENY"]
    assert out.getall(CSP) == ["frame-ancestors 'none'"]


def test_an_apps_other_csp_directives_are_kept_and_ours_is_appended():
    """We are not in the business of deleting an app's own script-src."""
    out = harden([(CSP, "default-src 'self'; img-src data:")])
    assert out.getall(CSP) == [
        "default-src 'self'; img-src data:, frame-ancestors 'none'"
    ]


def test_an_apps_frame_ancestors_is_stripped_out_of_its_own_policy():
    out = harden([(CSP, "default-src 'self'; frame-ancestors https://evil.example")])
    assert out.getall(CSP) == ["default-src 'self', frame-ancestors 'none'"]
    assert "evil.example" not in out[CSP]


def test_several_upstream_csp_headers_collapse_to_exactly_one():
    """Duplicates are what the review asked about. One header, every policy."""
    out = harden([(CSP, "default-src 'self'"), (CSP, "frame-ancestors *")])
    assert out.getall(CSP) == ["default-src 'self', frame-ancestors 'none'"]


def test_a_csp_that_said_only_frame_ancestors_leaves_no_empty_policy_behind():
    out = harden([(CSP, "frame-ancestors 'self'")])
    assert out.getall(CSP) == ["frame-ancestors 'none'"]


def test_frame_ancestors_is_matched_as_a_directive_not_a_substring():
    """`frame-src` is a different directive and is none of our business."""
    out = harden([(CSP, "frame-src https://ok.example")])
    assert out[CSP] == "frame-src https://ok.example, frame-ancestors 'none'"


def test_an_apps_own_referrer_policy_wins_because_it_may_be_stricter():
    """Not a platform control, and `no-referrer` is tighter than our floor."""
    out = harden([(REFERRER, "no-referrer")])
    assert out.getall(REFERRER) == ["no-referrer"]


def test_report_only_csp_is_left_alone_because_it_enforces_nothing():
    out = harden([("Content-Security-Policy-Report-Only", "default-src 'none'")])
    assert out["Content-Security-Policy-Report-Only"] == "default-src 'none'"


def test_set_cookie_duplicates_still_survive_the_hardening():
    """`_downstream_headers` preserves them on purpose; harden must not
    flatten the multidict on its way past."""
    out = harden([("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")])
    assert out.getall("Set-Cookie") == ["a=1", "b=2"]


def test_the_relayed_header_builder_hardens_what_it_relays():
    out = server._downstream_headers(
        CIMultiDict([("Content-Type", "text/html"), (XFO, "SAMEORIGIN")])
    )
    assert_framing_denied(out)


# --- the branded operational pages ------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        pages.starting("tarpeyo"),
        pages.not_signed_in(),
        pages.no_access("someone@example.com"),
        pages.expired(),
        pages.expired(disabled=True),
        pages.unknown_host(HOST),
        pages.unhealthy(),
        pages.signed_out("https://x/"),
    ],
    ids=[
        "starting",
        "401",
        "403",
        "410-expired",
        "410-disabled",
        "404",
        "503",
        "signed-out",
    ],
)
def test_every_branded_page_refuses_to_be_framed(response):
    assert_framing_denied(response.headers)
    assert response.headers[NOSNIFF] == "nosniff"


def test_the_pages_keep_their_own_cache_and_retry_headers():
    starting = pages.starting()
    assert starting.headers["Cache-Control"] == "no-store, must-revalidate"
    assert starting.headers["Retry-After"] == "3"
    assert_framing_denied(starting.headers)


# --- the portal: API, bundle, placeholder -----------------------------------


def test_the_json_api_refuses_to_be_framed():
    response = portal._json(200, {"apps": []})
    assert_framing_denied(response.headers)
    assert response.headers["Cache-Control"] == "no-store"


def test_an_api_error_body_is_hardened_too():
    assert_framing_denied(portal._error(403, "nope").headers)


def test_the_portal_document_and_its_assets_are_hardened():
    # Any real file will do -- `_file` decides headers, `FileResponse` reads
    # the bytes later. The packaged page is one that is certainly there.
    document = Path(pages.__file__).with_name("page.html")
    assert document.is_file()

    served = portal._file(document, immutable=False)
    assert_framing_denied(served.headers)
    assert served.headers["Cache-Control"] == "no-store"

    asset = portal._file(document, immutable=True)
    assert_framing_denied(asset.headers)
    assert "immutable" in asset.headers["Cache-Control"]


def test_the_not_built_placeholder_is_hardened():
    assert_framing_denied(portal._not_built().headers)


# --- the reserved /__proxy/ namespace ---------------------------------------


def reserved(path: str, host: str = PORTAL_HOST):
    handler = server.Proxy(
        apps=None,
        tasks=None,
        activity=None,
        recorder=None,
        session=None,
        portal_hosts=[PORTAL_HOST],
    )
    return asyncio.run(
        handler.handle(make_mocked_request("GET", path, headers={"Host": host}))
    )


@pytest.mark.parametrize(
    "path", ["/__proxy/healthz", "/__proxy/readyz", "/__proxy/nope"]
)
def test_the_plain_reserved_replies_are_hardened(path):
    assert_framing_denied(reserved(path).headers)


def test_the_signed_out_page_is_hardened_even_without_a_session():
    """The ONE page on the platform served with no authenticate action on its
    listener rule (priority 4900). If anything needed framing protection it
    is the page that anybody on the internet can fetch."""
    response = reserved(server.SIGNED_OUT_PATH)
    assert response.status == 200
    assert_framing_denied(response.headers)


def test_the_logout_redirect_is_hardened_and_keeps_its_cookies():
    from proxy_app.config import SignOut

    handler = server.Proxy(
        apps=None,
        tasks=None,
        activity=None,
        recorder=_NullRecorder(),
        session=None,
        portal_hosts=[PORTAL_HOST],
        signout=SignOut(domain="https://auth.example", client_id="abc"),
    )
    request = make_mocked_request(
        "GET",
        server.LOGOUT_PATH,
        headers={
            "Host": PORTAL_HOST,
            "Cookie": "AWSELBAuthSessionCookie-0=x; AWSELBAuthSessionCookie-1=y",
        },
    )
    response = asyncio.run(handler.handle(request))

    assert response.status == 302
    assert_framing_denied(response.headers)
    assert len(response.headers.getall("Set-Cookie")) == 2


class _NullRecorder:
    def record(self, event) -> None:
        pass


# --- the proxied path, over a real socket -----------------------------------


class _Tasks:
    def __init__(self, ip: str) -> None:
        self._ip = ip

    async def task_ip(self, service: str) -> str:
        return self._ip

    async def wake(self, service: str) -> bool:
        return True

    def forget(self, service: str) -> None:
        pass


class _Activity:
    def __init__(self) -> None:
        self.sockets = 0

    def touch(self, host: str) -> None:
        pass

    def open_socket(self, host: str):
        tracker = self

        class _Span:
            def __enter__(self):
                tracker.sockets += 1
                return self

            def __exit__(self, *exc):
                return False

        return _Span()


class _Apps:
    def __init__(self, app: App) -> None:
        self._app = app

    async def app(self, host: str):
        return self._app if host == self._app.host else None

    async def set_awake_since(self, host: str, ts: int) -> None:
        pass


class _AlwaysReady(server.Prober):
    async def ready(self, ip: str, port: int) -> bool:
        return True


def oidc_header() -> dict[str, str]:
    claims = {"email": "jake@stratevi.com", "sub": "sub-1"}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return {"x-amzn-oidc-data": f"header.{payload}.signature"}


async def upstream_app(*, framing_headers: bool) -> web.Application:
    """A stand-in Shiny: one document, one websocket, one download."""

    async def document(request: web.Request) -> web.Response:
        headers = {}
        if framing_headers:
            # An app that "helpfully" sets its own, less strict, policy.
            headers = {
                XFO: "SAMEORIGIN",
                CSP: "default-src 'self'; frame-ancestors https://evil.example",
            }
        return web.Response(text="<html>shiny</html>", content_type="text/html",
                            headers=headers)

    async def socket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type == aiohttp.WSMsgType.TEXT:
                await ws.send_str(f"echo:{message.data}")
                break
        await ws.close()
        return ws

    application = web.Application()
    application.router.add_get("/", document)
    application.router.add_get("/websocket/", socket)
    return application


class Proxied:
    """A proxy in front of a live fake app, both on real sockets."""

    def __init__(self, framing_headers: bool) -> None:
        self._framing_headers = framing_headers

    async def __aenter__(self):
        self._upstream = TestServer(await upstream_app(
            framing_headers=self._framing_headers
        ))
        await self._upstream.start_server()

        row = App.create(
            host=HOST,
            app_key="tarpeyo",
            ecs_service="shiny-tarpeyo",
            container_port=self._upstream.port,
            status="active",
            access_mode="all_users",
        )
        self._session = server.make_session()
        self.activity = _Activity()
        proxy = server.Proxy(
            apps=_Apps(row),
            tasks=_Tasks("127.0.0.1"),
            activity=self.activity,
            recorder=_NullRecorder(),
            session=self._session,
            prober=_AlwaysReady(),
        )
        self._front = TestServer(server.create_app(proxy))
        await self._front.start_server()
        self.client = aiohttp.ClientSession()
        self.base = f"http://127.0.0.1:{self._front.port}"
        return self

    async def __aexit__(self, *exc):
        await self.client.close()
        await self._front.close()
        await self._session.close()
        await self._upstream.close()
        return False


@pytest.mark.asyncio
async def test_a_proxied_app_response_carries_the_framing_headers():
    async with Proxied(framing_headers=False) as env:
        async with env.client.get(
            env.base + "/", headers={"Host": HOST, **oidc_header()}
        ) as response:
            assert response.status == 200
            assert await response.text() == "<html>shiny</html>"
            assert_framing_denied(response.headers)


@pytest.mark.asyncio
async def test_an_apps_own_headers_never_reach_the_browser_alongside_ours():
    """The duplicate question, end to end: the app sets SAMEORIGIN and a
    frame-ancestors allowing another origin, and the browser sees neither."""
    async with Proxied(framing_headers=True) as env:
        async with env.client.get(
            env.base + "/", headers={"Host": HOST, **oidc_header()}
        ) as response:
            assert response.headers.getall(XFO) == ["DENY"]
            assert response.headers.getall(CSP) == [
                "default-src 'self', frame-ancestors 'none'"
            ]
            assert "evil.example" not in response.headers[CSP]
            assert "SAMEORIGIN" not in str(response.headers)


@pytest.mark.asyncio
async def test_the_websocket_upgrade_still_works():
    """Every Shiny session is this handshake. The 101 is a protocol switch,
    not a document, so it deliberately carries no security headers -- and
    nothing added for framing may disturb the negotiated ones."""
    async with Proxied(framing_headers=False) as env:
        async with env.client.ws_connect(
            env.base + "/websocket/", headers={"Host": HOST, **oidc_header()}
        ) as socket:
            await socket.send_str("hello")
            assert (await socket.receive()).data == "echo:hello"

        # And it was counted as activity, which is what retires the ADR-0006
        # heartbeat for proxied apps.
        assert env.activity.sockets == 1
