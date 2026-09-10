"""Sign-out: cookie shards, the Cognito redirect, and who gets the routes.

The two halves are tested apart and together, because they fail
independently and each failure looks like the other from the outside. A
sign-out that expires only ``-0`` and one that never reaches Cognito both
present as "I clicked sign out and nothing happened".
"""

from __future__ import annotations

from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import pytest
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

from proxy_app import audit as audit_mod, config as config_mod, server, signout

PORTAL = "shinyplatform.tools.stratevi.com"

#: A real-shaped ALB identity header. Only the payload segment is read
#: (identity.decode_claims does not verify the signature -- see its banner).
OIDC_DATA = (
    "eyJhbGciOiJFUzI1NiJ9."
    # {"email":"jake@stratevi.com","sub":"microsoft365_gwww7jtfv4cb0km6"}
    "eyJlbWFpbCI6Impha2VAc3RyYXRldmkuY29tIiwic3ViIjoibWljcm9zb2Z0MzY1X2d3d3c3anRmdjRjYjBrbTYifQ"
    ".signature"
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[audit_mod.Event] = []

    def record(self, event: audit_mod.Event) -> None:
        self.events.append(event)


def settings(**overrides) -> config_mod.SignOut:
    defaults = dict(
        domain="stratevi-hub", client_id="7abc123clientid", region="us-east-1"
    )
    defaults.update(overrides)
    return config_mod.SignOut(**defaults)


def request(path=server.LOGOUT_PATH, *, host=PORTAL, cookies="", method="GET", auth=True):
    headers = CIMultiDict({"Host": host})
    if cookies:
        headers["Cookie"] = cookies
    if auth:
        headers["x-amzn-oidc-data"] = OIDC_DATA
    return make_mocked_request(method, path, headers=headers)


def proxy(*, recorder=None, signout_settings=None, hosts=(PORTAL,)) -> server.Proxy:
    return server.Proxy(
        apps=None,
        tasks=None,
        activity=None,
        recorder=recorder or Recorder(),
        session=None,
        portal_hosts=hosts,
        signout=signout_settings,
    )


def set_cookies(response) -> "SimpleCookie":
    jar: SimpleCookie = SimpleCookie()
    for value in response.headers.getall("Set-Cookie", ()):
        jar.load(value)
    return jar


# --- the cookie shards -----------------------------------------------------


def test_shard_names_includes_zero_even_when_the_browser_sent_nothing():
    assert signout.shard_names({}) == ["AWSELBAuthSessionCookie-0"]


def test_shard_names_finds_every_shard_present():
    names = signout.shard_names(
        {
            "AWSELBAuthSessionCookie-0": "a",
            "AWSELBAuthSessionCookie-1": "b",
            "AWSELBAuthSessionCookie-2": "c",
            "some_other_cookie": "d",
        }
    )
    assert names == [
        "AWSELBAuthSessionCookie-0",
        "AWSELBAuthSessionCookie-1",
        "AWSELBAuthSessionCookie-2",
    ]


def test_shard_names_sorts_numerically_not_lexicographically():
    names = signout.shard_names(
        {f"AWSELBAuthSessionCookie-{n}": "x" for n in (0, 1, 2, 9, 10, 11)}
    )
    assert names[-1] == "AWSELBAuthSessionCookie-11"
    assert names[-2] == "AWSELBAuthSessionCookie-10"
    assert names[3] == "AWSELBAuthSessionCookie-9"


def test_shard_names_ignores_lookalikes():
    names = signout.shard_names(
        {
            "AWSELBAuthSessionCookie-0": "real",
            "AWSELBAuthSessionCookie-x": "not a shard",
            "NotAWSELBAuthSessionCookie-1": "not ours",
            "AWSELBAuthSessionCookieExtra": "not ours either",
        }
    )
    assert names == ["AWSELBAuthSessionCookie-0"]


def test_expiry_headers_carry_the_attributes_a_deletion_needs():
    header = signout.expiry_headers({})[0]
    assert header.startswith("AWSELBAuthSessionCookie-0=;")
    assert "Path=/" in header
    assert "Max-Age=0" in header
    assert "Secure" in header
    assert "HttpOnly" in header
    assert "SameSite=Lax" in header
    # The ALB's cookie is host-only. A Domain attribute would expire a
    # DIFFERENT cookie and leave the session standing.
    assert "Domain" not in header


# --- the Cognito logout URL ------------------------------------------------


def test_hosted_ui_completes_a_prefix_with_the_region():
    assert (
        signout.hosted_ui(settings())
        == "https://stratevi-hub.auth.us-east-1.amazoncognito.com"
    )


def test_hosted_ui_takes_a_full_custom_domain_as_it_stands():
    assert (
        signout.hosted_ui(settings(domain="auth.stratevi.com"))
        == "https://auth.stratevi.com"
    )


def test_hosted_ui_tolerates_a_pasted_url():
    assert (
        signout.hosted_ui(settings(domain="https://auth.stratevi.com/"))
        == "https://auth.stratevi.com"
    )


def test_logout_url_shape_and_encoding():
    url = signout.logout_url(settings(), f"https://{PORTAL}/__proxy/signed-out")

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "stratevi-hub.auth.us-east-1.amazoncognito.com"
    assert parsed.path == "/logout"

    query = parse_qs(parsed.query)
    assert query["client_id"] == ["7abc123clientid"]
    assert query["logout_uri"] == [f"https://{PORTAL}/__proxy/signed-out"]

    # Fully percent-encoded, not quote_plus'd: Cognito compares the decoded
    # value against LogoutURLs exactly.
    assert "logout_uri=https%3A%2F%2F" in url
    assert "+" not in parsed.query


def test_logout_url_prefers_an_explicit_override():
    url = signout.logout_url(
        settings(signed_out_url="https://elsewhere.example/bye"),
        f"https://{PORTAL}/__proxy/signed-out",
    )
    assert parse_qs(urlparse(url).query)["logout_uri"] == [
        "https://elsewhere.example/bye"
    ]


# --- the route -------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_expires_every_shard_the_browser_sent():
    response = await proxy(signout_settings=settings()).handle(
        request(
            cookies=(
                "AWSELBAuthSessionCookie-0=aaa; "
                "AWSELBAuthSessionCookie-1=bbb; "
                "AWSELBAuthSessionCookie-2=ccc; "
                "other=keepme"
            )
        )
    )

    jar = set_cookies(response)
    assert sorted(jar) == [
        "AWSELBAuthSessionCookie-0",
        "AWSELBAuthSessionCookie-1",
        "AWSELBAuthSessionCookie-2",
    ]
    for name in jar:
        assert jar[name].value == ""
        assert jar[name]["max-age"] == "0"
        assert jar[name]["path"] == "/"
        assert jar[name]["secure"]
        assert jar[name]["httponly"]
    # A cookie that is not the ALB's is none of our business.
    assert "other" not in jar


@pytest.mark.asyncio
async def test_logout_expires_shard_zero_even_with_no_cookies_at_all():
    response = await proxy(signout_settings=settings()).handle(request())
    assert list(set_cookies(response)) == ["AWSELBAuthSessionCookie-0"]


@pytest.mark.asyncio
async def test_logout_redirects_to_the_cognito_logout_endpoint():
    response = await proxy(signout_settings=settings()).handle(request())

    assert response.status == 302
    assert response.headers["Cache-Control"] == "no-store"

    location = urlparse(response.headers["Location"])
    assert location.netloc == "stratevi-hub.auth.us-east-1.amazoncognito.com"
    assert location.path == "/logout"
    assert parse_qs(location.query)["logout_uri"] == [
        f"https://{PORTAL}/__proxy/signed-out"
    ]


@pytest.mark.asyncio
async def test_logout_audits_the_caller():
    recorder = Recorder()
    await proxy(recorder=recorder, signout_settings=settings()).handle(request())

    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.event == audit_mod.EVENT_SIGNED_OUT
    assert event.email == "jake@stratevi.com"
    assert event.host == PORTAL
    assert event.path == server.LOGOUT_PATH


@pytest.mark.asyncio
async def test_logout_still_audits_a_principal_with_no_email():
    recorder = Recorder()
    await proxy(recorder=recorder, signout_settings=settings()).handle(
        request(auth=False)
    )

    assert [event.event for event in recorder.events] == [audit_mod.EVENT_SIGNED_OUT]
    assert recorder.events[0].email == ""


@pytest.mark.asyncio
async def test_logout_without_cognito_config_still_clears_the_cookies():
    """The degraded mode: half a sign-out, and the page says which half."""
    response = await proxy(signout_settings=None).handle(request())

    assert response.status == 200
    assert list(set_cookies(response)) == ["AWSELBAuthSessionCookie-0"]
    assert "single sign-on session has been closed" not in response.text
    assert "Microsoft 365" in response.text


# --- who gets these routes -------------------------------------------------


@pytest.mark.asyncio
async def test_an_app_host_has_no_logout_route():
    recorder = Recorder()
    response = await proxy(recorder=recorder, signout_settings=settings()).handle(
        request(host="model.tools.stratevi.com")
    )

    assert response.status == 404
    assert response.headers.getall("Set-Cookie", []) == []
    assert recorder.events == []


@pytest.mark.asyncio
async def test_an_app_host_has_no_signed_out_page_either():
    response = await proxy(signout_settings=settings()).handle(
        request(server.SIGNED_OUT_PATH, host="model.tools.stratevi.com")
    )
    assert response.status == 404


@pytest.mark.asyncio
async def test_neither_route_exists_when_the_portal_is_off():
    off = proxy(hosts=(), signout_settings=settings())
    assert (await off.handle(request())).status == 404
    assert (await off.handle(request(server.SIGNED_OUT_PATH))).status == 404


@pytest.mark.asyncio
async def test_logout_refuses_a_method_that_is_not_a_navigation():
    recorder = Recorder()
    response = await proxy(recorder=recorder, signout_settings=settings()).handle(
        request(method="POST")
    )
    assert response.status == 405
    assert recorder.events == []


# --- the landing page ------------------------------------------------------


@pytest.mark.asyncio
async def test_the_signed_out_page_needs_no_identity():
    """The whole point: it renders for a browser with no session left."""
    response = await proxy(signout_settings=settings()).handle(
        request(server.SIGNED_OUT_PATH, auth=False)
    )

    assert response.status == 200
    assert response.content_type == "text/html"
    assert "You are signed out" in response.text


@pytest.mark.asyncio
async def test_the_signed_out_page_is_honest_about_the_microsoft_session():
    response = await proxy(signout_settings=settings()).handle(
        request(server.SIGNED_OUT_PATH, auth=False)
    )

    assert "Microsoft 365 sign-in on this device is still active" in response.text
    assert "will therefore not ask you for a password" in response.text
    assert f'href="https://{PORTAL}/"' in response.text
    assert "Sign in again" in response.text


@pytest.mark.asyncio
async def test_the_signed_out_page_never_caches():
    response = await proxy(signout_settings=settings()).handle(
        request(server.SIGNED_OUT_PATH, auth=False)
    )
    assert "no-store" in response.headers["Cache-Control"]


# --- the environment contract ----------------------------------------------


def test_config_reads_the_signout_block():
    cfg, error = config_mod.signout_from_env(
        {"COGNITO_DOMAIN": "stratevi-hub", "COGNITO_CLIENT_ID": "abc"}, "us-east-1"
    )
    assert error == ""
    assert cfg == config_mod.SignOut(
        domain="stratevi-hub", client_id="abc", region="us-east-1"
    )


def test_config_leaves_signout_off_when_nothing_is_set():
    assert config_mod.signout_from_env({}, "us-east-1") == (None, "")


def test_config_names_the_missing_half():
    cfg, error = config_mod.signout_from_env({"COGNITO_CLIENT_ID": "abc"}, "us-east-1")
    assert cfg is None
    assert "COGNITO_DOMAIN" in error


def test_config_refuses_to_guess_a_region_for_a_prefix():
    cfg, error = config_mod.signout_from_env(
        {"COGNITO_DOMAIN": "stratevi-hub", "COGNITO_CLIENT_ID": "abc"}, ""
    )
    assert cfg is None
    assert "AWS_REGION" in error


def test_config_needs_no_region_for_a_full_domain():
    cfg, error = config_mod.signout_from_env(
        {"COGNITO_DOMAIN": "auth.stratevi.com", "COGNITO_CLIENT_ID": "abc"}, ""
    )
    assert error == ""
    assert cfg is not None and cfg.domain == "auth.stratevi.com"


def test_from_env_wires_signout_through():
    cfg = config_mod.from_env(
        {
            "ECS_CLUSTER": "shiny-cluster",
            "APPS_TABLE": "shiny-proxy-apps",
            "AUDIT_TABLE": "shiny-proxy-audit",
            "AWS_REGION": "us-east-1",
            "COGNITO_DOMAIN": "stratevi-hub",
            "COGNITO_CLIENT_ID": "abc",
        }
    )
    assert cfg.signout_enabled()
    assert cfg.signout_error == ""
    assert cfg.signout is not None and cfg.signout.region == "us-east-1"


def test_a_half_configured_signout_is_not_a_startup_failure():
    cfg = config_mod.from_env(
        {
            "ECS_CLUSTER": "shiny-cluster",
            "APPS_TABLE": "shiny-proxy-apps",
            "AUDIT_TABLE": "shiny-proxy-audit",
            "AWS_REGION": "us-east-1",
            "COGNITO_CLIENT_ID": "abc",
        }
    )
    assert not cfg.signout_enabled()
    assert "COGNITO_DOMAIN" in cfg.signout_error
