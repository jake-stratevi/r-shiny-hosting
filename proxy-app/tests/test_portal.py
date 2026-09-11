"""The portal: admin gating, the menu, PATCH validation, audit paging, static.

Driven exactly like the proxy's own tests -- aiohttp's ``make_mocked_request``
plus fakes behind the module's protocols, no AWS, no sockets, no bundle. The
contract these assertions encode is ``docs/design/portal-api.md``; if one of
them has to change, that file changes first.
"""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from aiohttp import streams, web
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

from proxy_app import audit, portal, registry
from proxy_app.ecsctl import ServiceState
from proxy_app.registry import App

NOW = 1_800_000_000.0
ADMIN = "jake@stratevi.com"
USER = "nick@stratevi.com"

PORTAL_HOST = "dashboards.tools.stratevi.com"

#: An address outside every staff domain. On `admin_emails` in some tests
#: below and refused anyway -- that is the point of the staff gate.
OUTSIDER = "consultant@notstratevi.com"

#: "the fixture did not mention staff domains", which is a different thing
#: from "the config row holds an empty set".
_UNSET = object()


# --- fakes -----------------------------------------------------------------


class FakeStore:
    """The apps table. Deliberately returns `__`-rows from ``apps()``, so the
    portal's own filtering is what the tests exercise."""

    def __init__(self, rows: list[App] | None = None) -> None:
        self.rows = {row.host: row for row in (rows or [])}
        self.patches: list[tuple[str, dict]] = []
        self.fail_list = False
        self.fail_get = False
        self.fail_patch = False

    async def app(self, host: str) -> App | None:
        if self.fail_get:
            raise RuntimeError("dynamodb is unhappy")
        return self.rows.get(registry.normalize_host(host))

    async def apps(self) -> list[App]:
        if self.fail_list:
            raise RuntimeError("dynamodb is unhappy")
        return list(self.rows.values())

    async def patch(self, host: str, changes: dict) -> None:
        if self.fail_patch:
            raise RuntimeError("dynamodb is unhappy")
        self.patches.append((host, dict(changes)))
        row = self.rows[registry.normalize_host(host)]
        self.rows[row.host] = App.create(**{**row.__dict__, **changes})


class FakeTasks:
    def __init__(self, states: dict[str, ServiceState] | None = None) -> None:
        self.states_map = states or {}
        self.calls: list[list[str]] = []
        self.fail = False

    async def states(self, services) -> dict[str, ServiceState]:
        asked = list(services)
        self.calls.append(asked)
        if self.fail:
            raise RuntimeError("ecs is unhappy")
        return {s: self.states_map[s] for s in asked if s in self.states_map}


class FakeAuditReader:
    def __init__(self, pages=None) -> None:
        self.pages = pages or [([], None)]
        self.calls: list[tuple[str, int, dict | None]] = []
        self.fail = False

    async def events(self, host, limit, start_key):
        self.calls.append((host, limit, start_key))
        if self.fail:
            raise RuntimeError("dynamodb is unhappy")
        return self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]


class FakeRecorder:
    def __init__(self) -> None:
        self.events: list[audit.Event] = []

    def record(self, event: audit.Event) -> None:
        self.events.append(event)


class Clock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# --- request plumbing ------------------------------------------------------


def oidc_headers(email: str | None) -> dict[str, str]:
    """A hand-made x-amzn-oidc-data, exactly as the README documents."""
    if email is None:
        return {"Host": PORTAL_HOST}
    claims = {"sub": "cognito-sub-1", "email": email} if email else {"sub": "cognito-sub-1"}
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return {"Host": PORTAL_HOST, "x-amzn-oidc-data": f"header.{payload}.signature"}


def request(path="/", *, method="GET", email=ADMIN, body=None, headers=None):
    combined = CIMultiDict(oidc_headers(email))
    for name, value in (headers or {}).items():
        combined[name] = value

    payload = None
    if body is not None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        protocol = mock.Mock(_reading_paused=False)
        payload = streams.StreamReader(
            protocol, 2**16, loop=asyncio.get_event_loop()
        )
        payload.feed_data(raw)
        payload.feed_eof()

    return make_mocked_request(method, path, headers=combined, payload=payload)


def body_of(response: web.Response):
    return json.loads(response.text)


def app_row(**overrides) -> App:
    defaults = dict(
        host="model.tools.stratevi.com",
        app_key="model",
        label="Microsimulation Model",
        description="Patient-level microsimulation.",
        ecs_service="shiny-model",
        access_mode=registry.MODE_USERS,
        allowed_emails=[ADMIN],
    )
    defaults.update(overrides)
    return App.create(**defaults)


def portal_for(
    *,
    store=None,
    tasks=None,
    admins=(ADMIN,),
    staff=_UNSET,
    recorder=None,
    audit_reader=None,
    dist=None,
    clock=None,
):
    async def reader():
        if isinstance(admins, Exception):
            raise admins
        return admins

    def domain_reader(value):
        async def read():
            if isinstance(value, Exception):
                raise value
            return value

        return read

    return portal.Portal(
        apps=store if store is not None else FakeStore([app_row()]),
        tasks=tasks if tasks is not None else FakeTasks(),
        admins=portal.AdminList(reader, clock=Clock(0.0)),
        # `staff` unset means no collaborator at all, which is how most of
        # this file's fixtures are built: the Portal then evaluates
        # registry.DEFAULT_STAFF_DOMAINS, and every address here is
        # @stratevi.com, so the gate is transparent unless a test says
        # otherwise.
        staff=(
            None
            if staff is _UNSET
            else portal.StaffDomains(domain_reader(staff), clock=Clock(0.0))
        ),
        recorder=recorder or FakeRecorder(),
        audit=audit_reader,
        dist=dist,
        clock=clock or Clock(),
    )


# --- the admin list: cached, and closed when anything goes wrong -----------


@pytest.mark.asyncio
async def test_an_address_in_the_config_row_is_an_admin():
    admins = portal.AdminList(lambda: _resolved([ADMIN, "Yi@Stratevi.com"]))
    assert await admins.is_admin(ADMIN) is True
    assert await admins.is_admin("YI@stratevi.com") is True
    assert await admins.is_admin(USER) is False


@pytest.mark.asyncio
async def test_a_principal_with_no_email_is_never_an_admin():
    """A federated user arrives with only a synthetic Cognito username. They
    can see the menu; they cannot reach the control plane."""
    admins = portal.AdminList(lambda: _resolved([ADMIN]))
    assert await admins.is_admin("") is False
    assert await admins.is_admin("   ") is False


@pytest.mark.asyncio
async def test_a_missing_config_row_means_nobody_is_an_admin():
    admins = portal.AdminList(lambda: _resolved([]))
    assert await admins.emails() == frozenset()
    assert await admins.is_admin(ADMIN) is False


@pytest.mark.asyncio
async def test_an_unreadable_config_row_fails_closed():
    async def broken():
        raise RuntimeError("dynamodb is unhappy")

    admins = portal.AdminList(broken)
    assert await admins.is_admin(ADMIN) is False


@pytest.mark.asyncio
async def test_the_admin_list_is_cached_rather_than_read_per_request():
    calls = []

    async def reader():
        calls.append(1)
        return [ADMIN]

    clock = Clock(0.0)
    admins = portal.AdminList(reader, ttl=30.0, clock=clock)
    for _ in range(20):
        assert await admins.is_admin(ADMIN) is True
    assert len(calls) == 1

    clock.t += 31
    await admins.is_admin(ADMIN)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_failed_read_is_cached_far_more_briefly_than_a_good_one():
    """A blip must not lock every admin out for the full 30 seconds."""
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("dynamodb is unhappy")
        return [ADMIN]

    clock = Clock(0.0)
    admins = portal.AdminList(flaky, ttl=30.0, error_ttl=5.0, clock=clock)

    assert await admins.is_admin(ADMIN) is False
    clock.t += 6
    assert await admins.is_admin(ADMIN) is True
    assert len(calls) == 2


def _resolved(value):
    future: asyncio.Future = asyncio.Future()
    future.set_result(value)
    return future


# --- the staff gate: a list entry is necessary, never sufficient -----------


def _staff(value=("stratevi.com",)):
    async def read():
        if isinstance(value, Exception):
            raise value
        return value

    return portal.StaffDomains(read, clock=Clock(0.0))


@pytest.mark.asyncio
async def test_an_address_in_a_staff_domain_is_staff():
    staff = _staff()
    assert await staff.is_staff(ADMIN) is True
    assert await staff.is_staff("YI@STRATEVI.COM") is True
    assert await staff.is_staff("  nick@Stratevi.com  ") is True


@pytest.mark.asyncio
async def test_a_lookalike_domain_is_never_a_staff_domain():
    """Exact domain equality, not a suffix test.

    `notstratevi.com` ends with the staff domain and
    `stratevi.com.evil.test` begins with it; a substring or endswith check
    admits one or the other, and both are registrable by anybody.
    """
    staff = _staff()
    for address in (
        "someone@notstratevi.com",
        "someone@stratevi.com.evil.test",
        "someone@xstratevi.com",
        "someone@mail.stratevi.com",  # a subdomain is a different domain
        "someone@stratevi.co",
        "stratevi.com@evil.test",  # the staff domain in the LOCAL part
    ):
        assert await staff.is_staff(address) is False, address


@pytest.mark.asyncio
async def test_a_malformed_address_is_never_staff():
    staff = _staff()
    for address in ("", "   ", "jake", "@stratevi.com", "a@b@stratevi.com"):
        assert await staff.is_staff(address) is False, address


@pytest.mark.asyncio
async def test_a_missing_staff_domains_attribute_falls_back_to_the_default():
    """Not to "everyone", and not to "nobody" either.

    An empty or absent attribute must not read as "no domain restriction" --
    that would delete the boundary during a partial read. It must also not
    lock every administrator out of a live deployment, which is what the
    fail-closed rule the other __config__ sets follow would do here.
    """
    staff = _staff(())
    assert await staff.domains() == frozenset(registry.DEFAULT_STAFF_DOMAINS)
    assert await staff.is_staff(ADMIN) is True
    assert await staff.is_staff(OUTSIDER) is False


@pytest.mark.asyncio
async def test_an_unreadable_staff_domains_attribute_falls_back_to_the_default():
    staff = _staff(RuntimeError("dynamodb is unhappy"))
    assert await staff.domains() == frozenset(registry.DEFAULT_STAFF_DOMAINS)
    assert await staff.is_staff(ADMIN) is True
    assert await staff.is_staff(OUTSIDER) is False


@pytest.mark.asyncio
async def test_the_config_row_can_widen_the_staff_domains():
    staff = _staff(("stratevi.com", "@Assembled.example"))
    assert await staff.is_staff("someone@assembled.example") is True
    assert await staff.is_staff(ADMIN) is True
    assert await staff.is_staff(OUTSIDER) is False


@pytest.mark.asyncio
async def test_a_non_staff_address_on_the_admin_list_is_still_refused():
    """The bug this gate exists for.

    Being named in `admin_emails` is necessary and NOT sufficient: the
    guarantee is a property of the service, not of whoever last pruned the
    Terraform list.
    """
    handler = portal_for(admins=(ADMIN, OUTSIDER), staff=("stratevi.com",))

    refused = await handler.handle(request("/api/v1/apps", email=OUTSIDER))
    assert refused.status == 403
    assert body_of(refused)["error"] == "you are not a platform administrator"

    allowed = await handler.handle(request("/api/v1/apps", email=ADMIN))
    assert allowed.status == 200


ADMIN_ROUTES = [
    ("/api/v1/apps", "GET", None),
    ("/api/v1/apps/model.tools.stratevi.com", "GET", None),
    ("/api/v1/apps/model.tools.stratevi.com", "PATCH", {"label": "Mine now"}),
    ("/api/v1/apps/model.tools.stratevi.com/audit", "GET", None),
    ("/api/v1/apps/model.tools.stratevi.com/costs", "GET", None),
    ("/api/v1/costs", "GET", None),
]


@pytest.mark.parametrize("path,method,body", ADMIN_ROUTES)
@pytest.mark.asyncio
async def test_a_non_staff_admin_is_403_on_every_admin_route(path, method, body):
    handler = portal_for(
        admins=(ADMIN, OUTSIDER),
        staff=("stratevi.com",),
        audit_reader=FakeAuditReader(),
    )
    response = await handler.handle(
        request(path, method=method, email=OUTSIDER, body=body,
                headers={portal.CSRF_HEADER: "1"})
    )
    assert response.status == 403


@pytest.mark.asyncio
async def test_me_collapses_admin_to_false_for_a_non_staff_address():
    """The UI must not be told about a permission the next request refuses."""
    handler = portal_for(admins=(ADMIN, OUTSIDER), staff=("stratevi.com",))
    assert body_of(await handler.handle(request("/api/v1/me", email=OUTSIDER))) == {
        "email": OUTSIDER,
        "is_admin": False,
        "can_create": False,
        "is_staff": False,
    }


@pytest.mark.asyncio
async def test_the_staff_gate_does_not_touch_the_menu():
    """Entitlement to USE an app is the app row's business.

    Clients sign in to the same pool (ADR-0015's two populations) and must
    keep seeing the dashboards they are entitled to. The staff gate is about
    the control plane only.
    """
    store = FakeStore(
        [
            app_row(
                host="dashboard.tools.stratevi.com",
                ecs_service="shiny-dashboard",
                access_mode=registry.MODE_ALL_USERS,
                allowed_emails=[],
            ),
            app_row(
                host="client.tools.stratevi.com",
                label="Client Dashboard",
                ecs_service="shiny-client",
                access_mode=registry.MODE_USERS,
                allowed_emails=[OUTSIDER],
            ),
        ]
    )
    handler = portal_for(store=store, staff=("stratevi.com",))
    response = await handler.handle(request("/api/v1/menu", email=OUTSIDER))
    assert response.status == 200
    assert sorted(tile["host"] for tile in body_of(response)["apps"]) == [
        "client.tools.stratevi.com",
        "dashboard.tools.stratevi.com",
    ]


@pytest.mark.asyncio
async def test_a_portal_wired_without_a_staff_list_still_enforces_the_default():
    """There is no configuration in which the check is simply absent."""
    handler = portal_for(admins=(ADMIN, OUTSIDER))
    assert (await handler.handle(request("/api/v1/apps", email=OUTSIDER))).status == 403
    assert (await handler.handle(request("/api/v1/apps", email=ADMIN))).status == 200


# --- /api/v1/me ------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_reports_the_caller_and_whether_they_are_an_admin():
    response = await portal_for().handle(request("/api/v1/me"))
    assert response.status == 200
    # can_create is P2a's addition and is INDEPENDENT of is_admin: this
    # portal has no creator list, so an admin still cannot create.
    assert body_of(response) == {
        "email": ADMIN,
        "is_admin": True,
        "can_create": False,
        # The UI hides staff-only screens on this, so it is reported on its
        # own rather than inferred from the two permissions above.
        "is_staff": True,
    }


@pytest.mark.asyncio
async def test_me_reports_a_non_admin_as_one():
    response = await portal_for().handle(request("/api/v1/me", email=USER))
    assert body_of(response) == {
        "email": USER,
        "is_admin": False,
        "can_create": False,
        "is_staff": True,
    }


@pytest.mark.asyncio
async def test_an_api_request_with_no_identity_at_all_is_401_json():
    response = await portal_for().handle(request("/api/v1/me", email=None))
    assert response.status == 401
    assert response.content_type == "application/json"
    assert "error" in body_of(response)


@pytest.mark.asyncio
async def test_an_unknown_api_endpoint_is_404_json_not_the_react_bundle():
    response = await portal_for().handle(request("/api/v1/nope"))
    assert response.status == 404
    assert response.content_type == "application/json"


# --- /api/v1/menu ----------------------------------------------------------


def menu_store() -> FakeStore:
    return FakeStore(
        [
            app_row(),  # users mode, ADMIN only
            app_row(
                host="dashboard.tools.stratevi.com",
                app_key="dashboard",
                label="Treatment Pathway Dashboard",
                ecs_service="shiny-dashboard",
                access_mode=registry.MODE_ALL_USERS,
                allowed_emails=[],
            ),
            app_row(
                host="off.tools.stratevi.com",
                app_key="off",
                label="Disabled App",
                ecs_service="shiny-off",
                access_mode=registry.MODE_ALL_USERS,
                status=registry.STATUS_DISABLED,
            ),
            app_row(
                host="gone.tools.stratevi.com",
                app_key="gone",
                label="Lapsed App",
                ecs_service="shiny-gone",
                access_mode=registry.MODE_ALL_USERS,
                expires_at=int(NOW - 1),
            ),
            # Configuration, not an app. A tile called "__config__" would be
            # funny exactly once.
            App.create(host=registry.CONFIG_HOST),
        ]
    )


@pytest.mark.asyncio
async def test_the_menu_shows_only_what_the_caller_is_entitled_to():
    response = await portal_for(store=menu_store()).handle(request("/api/v1/menu"))
    hosts = [tile["host"] for tile in body_of(response)["apps"]]
    # Sorted by label: "Microsimulation Model" before "Treatment Pathway
    # Dashboard", so the tiles do not shuffle between refreshes.
    assert hosts == ["model.tools.stratevi.com", "dashboard.tools.stratevi.com"]


@pytest.mark.asyncio
async def test_a_non_entitled_user_sees_only_the_all_users_apps():
    response = await portal_for(store=menu_store()).handle(
        request("/api/v1/menu", email=USER)
    )
    hosts = [tile["host"] for tile in body_of(response)["apps"]]
    assert hosts == ["dashboard.tools.stratevi.com"]


@pytest.mark.asyncio
async def test_disabled_and_expired_apps_are_not_offered_in_the_menu():
    """The menu must never offer a tile the proxy would then refuse -- that
    drift is exactly what ADR-0013's catalog.yaml suffered from."""
    response = await portal_for(store=menu_store()).handle(request("/api/v1/menu"))
    hosts = [tile["host"] for tile in body_of(response)["apps"]]
    assert "off.tools.stratevi.com" not in hosts
    assert "gone.tools.stratevi.com" not in hosts


@pytest.mark.asyncio
async def test_config_rows_never_appear_in_the_menu():
    response = await portal_for(store=menu_store()).handle(request("/api/v1/menu"))
    assert all(
        not tile["host"].startswith("__") for tile in body_of(response)["apps"]
    )


@pytest.mark.asyncio
async def test_a_menu_tile_carries_a_url_and_a_live_state():
    tasks = FakeTasks({"shiny-model": ServiceState(exists=True, desired=1, running=1)})
    response = await portal_for(store=FakeStore([app_row()]), tasks=tasks).handle(
        request("/api/v1/menu")
    )
    tile = body_of(response)["apps"][0]
    assert tile == {
        "host": "model.tools.stratevi.com",
        "label": "Microsimulation Model",
        "description": "Patient-level microsimulation.",
        "url": "https://model.tools.stratevi.com",
        "live_state": portal.LIVE_AWAKE,
        # The tile shows both, so the payload carries both. Null rather than
        # absent when unset -- the card renders "Never expires" / "Not opened
        # yet" from them and must not have to guess which case it is in.
        "expires_at": None,
        "last_active": None,
    }


@pytest.mark.asyncio
async def test_a_menu_tile_carries_expiry_and_last_activity_when_the_row_has_them():
    # Both relative to NOW: an expiry in the past would make the app expired
    # and drop it from the menu entirely, which is a different test.
    expires = int(NOW) + 86_400
    seen = int(NOW) - 3_600
    store = FakeStore([app_row(expires_at=expires, last_active=seen)])
    response = await portal_for(store=store).handle(request("/api/v1/menu"))
    tile = body_of(response)["apps"][0]
    assert tile["expires_at"] == expires
    assert tile["last_active"] == seen


@pytest.mark.asyncio
async def test_a_row_with_no_label_falls_back_to_its_key_rather_than_an_empty_tile():
    store = FakeStore([app_row(label="", description="")])
    response = await portal_for(store=store).handle(request("/api/v1/menu"))
    assert body_of(response)["apps"][0]["label"] == "model"


@pytest.mark.asyncio
async def test_the_menu_needs_no_admin_rights():
    store = FakeStore(
        [app_row(access_mode=registry.MODE_ALL_USERS, allowed_emails=[])]
    )
    response = await portal_for(store=store, admins=()).handle(
        request("/api/v1/menu", email=USER)
    )
    assert response.status == 200
    assert len(body_of(response)["apps"]) == 1


@pytest.mark.asyncio
async def test_an_unreadable_table_does_not_render_an_empty_menu():
    store = menu_store()
    store.fail_list = True
    response = await portal_for(store=store).handle(request("/api/v1/menu"))
    assert response.status == 503


# --- the admin gate --------------------------------------------------------


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/v1/apps", "GET"),
        ("/api/v1/apps/model.tools.stratevi.com", "GET"),
        ("/api/v1/apps/model.tools.stratevi.com", "PATCH"),
        ("/api/v1/apps/model.tools.stratevi.com/audit", "GET"),
    ],
)
@pytest.mark.asyncio
async def test_every_apps_endpoint_is_403_for_a_non_admin(path, method):
    response = await portal_for().handle(
        request(path, method=method, email=USER, body={"idle_minutes": 30},
                headers={portal.CSRF_HEADER: "1"})
    )
    assert response.status == 403
    assert "error" in body_of(response)


@pytest.mark.asyncio
async def test_an_unreadable_admin_list_closes_the_control_plane_to_everyone():
    """Fail closed: no config row, or a DynamoDB error, means no admins."""
    broken = portal_for(admins=RuntimeError("dynamodb is unhappy"))
    assert (await broken.handle(request("/api/v1/apps"))).status == 403

    empty = portal_for(admins=())
    assert (await empty.handle(request("/api/v1/apps"))).status == 403


# --- /api/v1/apps ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_admin_list_returns_full_app_objects():
    tasks = FakeTasks({"shiny-model": ServiceState(exists=True, desired=1, running=1)})
    store = FakeStore([app_row(last_active=int(NOW - 30), awake_since=int(NOW - 60))])
    response = await portal_for(store=store, tasks=tasks).handle(
        request("/api/v1/apps")
    )

    assert response.status == 200
    assert body_of(response) == [
        {
            "host": "model.tools.stratevi.com",
            "app_key": "model",
            "label": "Microsimulation Model",
            "description": "Patient-level microsimulation.",
            "ecs_service": "shiny-model",
            "container_port": 3838,
            "status": "active",
            "live_state": "awake",
            "access_mode": "users",
            "allowed_emails": [ADMIN],
            "idle_minutes": 15,
            "max_session_hours": 0,
            "expires_at": None,
            "last_active": int(NOW - 30),
            "awake_since": int(NOW - 60),
            "desired_count": 1,
            "running_count": 1,
        }
    ]


@pytest.mark.asyncio
async def test_the_admin_list_skips_config_rows_too():
    response = await portal_for(store=menu_store()).handle(request("/api/v1/apps"))
    hosts = [app["host"] for app in body_of(response)]
    assert registry.CONFIG_HOST not in hosts
    assert len(hosts) == 4


@pytest.mark.asyncio
async def test_the_admin_list_asks_ecs_once_for_the_whole_page():
    """One batched, deduplicated describe per page load, not one per tile."""
    tasks = FakeTasks()
    await portal_for(store=menu_store(), tasks=tasks).handle(request("/api/v1/apps"))
    assert len(tasks.calls) == 1
    assert len(tasks.calls[0]) == 4


@pytest.mark.asyncio
async def test_an_ecs_failure_degrades_the_badge_rather_than_the_page():
    tasks = FakeTasks()
    tasks.fail = True
    response = await portal_for(tasks=tasks).handle(request("/api/v1/apps"))
    assert response.status == 200
    assert body_of(response)[0]["live_state"] == portal.LIVE_ASLEEP


@pytest.mark.asyncio
async def test_one_app_by_host_and_a_404_for_an_unknown_one():
    handler = portal_for()
    found = await handler.handle(request("/api/v1/apps/model.tools.stratevi.com"))
    assert found.status == 200
    assert body_of(found)["app_key"] == "model"

    missing = await handler.handle(request("/api/v1/apps/nope.tools.stratevi.com"))
    assert missing.status == 404
    assert "error" in body_of(missing)


@pytest.mark.asyncio
async def test_the_config_row_is_not_reachable_through_the_app_api():
    store = FakeStore([app_row(), App.create(host=registry.CONFIG_HOST)])
    response = await portal_for(store=store).handle(
        request(f"/api/v1/apps/{registry.CONFIG_HOST}")
    )
    assert response.status == 404


# --- live_state derivation -------------------------------------------------


@pytest.mark.parametrize(
    "row,state,expected",
    [
        (app_row(), ServiceState(exists=True, desired=1, running=1), "awake"),
        (app_row(), ServiceState(exists=True, desired=1, running=0), "starting"),
        (app_row(), ServiceState(exists=True, desired=0, running=0), "asleep"),
        (app_row(), ServiceState(exists=False), "asleep"),
        (app_row(), None, "asleep"),
        (
            app_row(status=registry.STATUS_DISABLED),
            ServiceState(exists=True, desired=1, running=1),
            "disabled",
        ),
        (
            app_row(status=registry.STATUS_EXPIRED),
            ServiceState(exists=True, desired=1, running=1),
            "expired",
        ),
        # Lapsed on the clock but the reaper has not run yet: the badge must
        # agree with the door, which refuses on the clock.
        (
            app_row(expires_at=int(NOW - 1)),
            ServiceState(exists=True, desired=1, running=1),
            "expired",
        ),
        (app_row(expires_at=int(NOW + 3600)), ServiceState(exists=False), "asleep"),
    ],
)
def test_live_state_derivation(row, state, expected):
    assert portal.live_state(row, state, NOW) == expected


# --- PATCH: the validation matrix ------------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"label": "  New name  "}, {"label": "New name"}),
        ({"description": "What it does"}, {"description": "What it does"}),
        ({"access_mode": "all_users"}, {"access_mode": "all_users"}),
        ({"access_mode": "USERS"}, {"access_mode": "users"}),
        (
            {"allowed_emails": [" JAKE@Stratevi.com ", "nick@stratevi.com", ""]},
            {"allowed_emails": ("jake@stratevi.com", "nick@stratevi.com")},
        ),
        ({"allowed_emails": []}, {"allowed_emails": ()}),
        ({"idle_minutes": 1}, {"idle_minutes": 1}),
        ({"idle_minutes": 1440}, {"idle_minutes": 1440}),
        ({"max_session_hours": 0}, {"max_session_hours": 0}),
        ({"max_session_hours": 168}, {"max_session_hours": 168}),
        ({"expires_at": None}, {"expires_at": 0}),
        ({"expires_at": 1_900_000_000}, {"expires_at": 1_900_000_000}),
        ({"status": "disabled"}, {"status": "disabled"}),
        ({"status": "active"}, {"status": "active"}),
    ],
)
def test_the_patches_the_contract_allows(body, expected):
    assert portal.validate_patch(body) == expected


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"ecs_service": "shiny-other"}, "unknown field"),
        ({"host": "elsewhere.tools.stratevi.com"}, "unknown field"),
        ({"idle_mins": 30}, "unknown field"),
        ({}, "no fields to change"),
        ([], "JSON object"),
        ("nope", "JSON object"),
        ({"access_mode": "team"}, "reserved"),
        ({"access_mode": "organizations"}, "reserved"),
        ({"access_mode": "client_magic_link"}, "reserved"),
        ({"access_mode": "everyone_lol"}, "all_users or users"),
        ({"access_mode": 7}, "must be a string"),
        ({"allowed_emails": "jake@stratevi.com"}, "must be a list"),
        ({"allowed_emails": ["not-an-address"]}, "not an email address"),
        ({"allowed_emails": [7]}, "list of strings"),
        ({"idle_minutes": 0}, "between 1 and 1440"),
        ({"idle_minutes": 1441}, "between 1 and 1440"),
        ({"idle_minutes": "30"}, "whole number"),
        ({"idle_minutes": 30.5}, "whole number"),
        ({"idle_minutes": True}, "whole number"),
        ({"max_session_hours": -1}, "between 0 and 168"),
        ({"max_session_hours": 169}, "between 0 and 168"),
        ({"expires_at": -1}, "epoch seconds or null"),
        ({"expires_at": "tomorrow"}, "epoch seconds or null"),
        ({"status": "expired"}, "active or disabled"),
        ({"status": "building"}, "active or disabled"),
        ({"label": 7}, "must be a string"),
        ({"label": "x" * 201}, "at most 200"),
        ({"description": "x" * 2001}, "at most 2000"),
    ],
)
def test_the_patches_the_contract_refuses(body, fragment):
    with pytest.raises(portal.PatchError, match=fragment):
        portal.validate_patch(body)


def test_a_patch_is_ordered_so_the_audit_reads_the_same_way_every_time():
    changes = portal.validate_patch({"status": "active", "label": "A", "idle_minutes": 20})
    assert list(changes) == ["label", "idle_minutes", "status"]


# --- PATCH: the endpoint ---------------------------------------------------


def patch_request(body, *, csrf=True, email=ADMIN, host="model.tools.stratevi.com"):
    headers = {portal.CSRF_HEADER: "1"} if csrf else {}
    return request(
        f"/api/v1/apps/{host}", method="PATCH", email=email, body=body, headers=headers
    )


@pytest.mark.asyncio
async def test_a_good_patch_writes_the_row_and_returns_the_updated_app():
    store = FakeStore([app_row()])
    handler = portal_for(store=store)

    response = await handler.handle(
        patch_request({"idle_minutes": 45, "label": "Renamed"})
    )

    assert response.status == 200
    assert store.patches == [
        ("model.tools.stratevi.com", {"label": "Renamed", "idle_minutes": 45})
    ]
    updated = body_of(response)
    assert updated["idle_minutes"] == 45
    assert updated["label"] == "Renamed"


@pytest.mark.asyncio
async def test_a_successful_patch_audits_the_field_names_and_the_caller():
    recorder = FakeRecorder()
    handler = portal_for(store=FakeStore([app_row()]), recorder=recorder)

    await handler.handle(
        patch_request({"allowed_emails": [USER], "status": "disabled"})
    )

    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.event == audit.EVENT_CONFIG_CHANGE
    assert event.email == ADMIN
    assert event.host == "model.tools.stratevi.com"
    # Names, never values: an audit row must not be somewhere an allowlist
    # can be read out of.
    assert event.path == "allowed_emails,status"
    assert USER not in event.path


@pytest.mark.asyncio
async def test_config_change_is_not_one_of_the_deduplicated_events():
    """Only `allow` is collapsed; two identical edits are two decisions."""
    assert audit.EVENT_CONFIG_CHANGE != audit.EVENT_ALLOW

    recorder = audit.Recorder(clock=lambda: NOW)
    for _ in range(3):
        recorder.record(
            audit.Event(
                host="model.tools.stratevi.com",
                event=audit.EVENT_CONFIG_CHANGE,
                email=ADMIN,
                path="idle_minutes",
            )
        )
    assert recorder._queue.qsize() == 3


@pytest.mark.asyncio
async def test_a_patch_without_the_csrf_header_is_refused_before_anything_is_read():
    store = FakeStore([app_row()])
    response = await portal_for(store=store).handle(
        patch_request({"idle_minutes": 45}, csrf=False)
    )
    assert response.status == 403
    assert portal.CSRF_HEADER.lower() in body_of(response)["error"].lower()
    assert store.patches == []


@pytest.mark.asyncio
async def test_a_patch_with_an_invalid_body_writes_nothing():
    store = FakeStore([app_row()])
    handler = portal_for(store=store)

    for body in (b"{not json", {"idle_minutes": 0}, {"nope": 1}, {}):
        response = await handler.handle(patch_request(body))
        assert response.status == 400, body
        assert "error" in body_of(response)
    assert store.patches == []


@pytest.mark.asyncio
async def test_patching_an_unknown_host_is_404_not_a_new_row():
    store = FakeStore([app_row()])
    response = await portal_for(store=store).handle(
        patch_request({"idle_minutes": 45}, host="nope.tools.stratevi.com")
    )
    assert response.status == 404
    assert store.patches == []


@pytest.mark.asyncio
async def test_a_failed_write_is_reported_and_not_audited_as_a_change():
    store = FakeStore([app_row()])
    store.fail_patch = True
    recorder = FakeRecorder()

    response = await portal_for(store=store, recorder=recorder).handle(
        patch_request({"idle_minutes": 45})
    )

    assert response.status == 503
    assert recorder.events == []


@pytest.mark.asyncio
async def test_clearing_the_allowlist_and_the_expiry_round_trips_to_absence():
    store = FakeStore([app_row(expires_at=int(NOW + 100), allowed_emails=[ADMIN])])
    response = await portal_for(store=store).handle(
        patch_request({"allowed_emails": [], "expires_at": None})
    )
    assert response.status == 200
    assert body_of(response)["allowed_emails"] == []
    assert body_of(response)["expires_at"] is None


# --- /api/v1/apps/{host}/audit ---------------------------------------------


@pytest.mark.asyncio
async def test_the_audit_endpoint_returns_events_newest_first_with_a_cursor():
    key = {"host": {"S": "model.tools.stratevi.com"}, "ts": {"S": "0000000000123#ab"}}
    reader = FakeAuditReader(
        [([{"event": "allow", "email": ADMIN, "path": "/", "ts": 1789}], key)]
    )
    response = await portal_for(audit_reader=reader).handle(
        request("/api/v1/apps/model.tools.stratevi.com/audit")
    )

    payload = body_of(response)
    assert payload["events"][0]["event"] == "allow"
    assert payload["cursor"]
    assert audit.decode_cursor(payload["cursor"]) == key


@pytest.mark.asyncio
async def test_the_cursor_round_trips_into_the_next_page_request():
    key = {"host": {"S": "model.tools.stratevi.com"}, "ts": {"S": "0000000000123#ab"}}
    reader = FakeAuditReader([([{"event": "wake"}], key), ([{"event": "sleep"}], None)])
    handler = portal_for(audit_reader=reader)

    first = body_of(
        await handler.handle(request("/api/v1/apps/model.tools.stratevi.com/audit"))
    )
    second = body_of(
        await handler.handle(
            request(
                "/api/v1/apps/model.tools.stratevi.com/audit"
                f"?cursor={first['cursor']}"
            )
        )
    )

    assert reader.calls[1][2] == key  # decoded straight back into ExclusiveStartKey
    assert second["cursor"] is None


@pytest.mark.asyncio
async def test_the_limit_is_passed_through_and_defaulted():
    reader = FakeAuditReader()
    handler = portal_for(audit_reader=reader)

    await handler.handle(request("/api/v1/apps/model.tools.stratevi.com/audit"))
    await handler.handle(request("/api/v1/apps/model.tools.stratevi.com/audit?limit=7"))

    assert reader.calls[0][1] == audit.DEFAULT_AUDIT_LIMIT
    assert reader.calls[1][1] == 7


@pytest.mark.parametrize("query", ["?limit=nope", "?limit=0", "?cursor=not-base64!!"])
@pytest.mark.asyncio
async def test_a_malformed_audit_query_is_400(query):
    reader = FakeAuditReader()
    response = await portal_for(audit_reader=reader).handle(
        request(f"/api/v1/apps/model.tools.stratevi.com/audit{query}")
    )
    assert response.status == 400
    assert reader.calls == []


@pytest.mark.asyncio
async def test_an_unreadable_audit_table_is_503_not_an_empty_trail():
    reader = FakeAuditReader()
    reader.fail = True
    response = await portal_for(audit_reader=reader).handle(
        request("/api/v1/apps/model.tools.stratevi.com/audit")
    )
    assert response.status == 503


# --- the React bundle ------------------------------------------------------


@pytest.fixture
def workspace():
    """A scratch directory.

    ``tempfile`` rather than pytest's ``tmp_path``: this repo's Windows
    machine has an unreadable ``pytest-of-<user>`` left in %TEMP%, and the
    tmp_path fixture cannot get past it. Nothing here needs pytest's numbered
    directory retention.
    """
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


@pytest.fixture
def bundle(workspace):
    dist = workspace / "portal-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    return dist


@pytest.mark.asyncio
async def test_the_bundle_is_served_and_deep_links_fall_back_to_index(bundle):
    handler = portal_for(dist=bundle)

    for path in ("/", "/admin", "/apps/model.tools.stratevi.com", "/anything/deep"):
        response = await handler.handle(request(path))
        assert isinstance(response, web.FileResponse)
        assert response._path.name == "index.html"
        # A cached index.html keeps loading asset URLs that a deploy deleted.
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_a_hashed_asset_is_served_and_cached_hard(bundle):
    response = await portal_for(dist=bundle).handle(
        request("/assets/index-abc123.js")
    )
    assert isinstance(response, web.FileResponse)
    assert "immutable" in response.headers["Cache-Control"]


@pytest.mark.asyncio
async def test_a_missing_asset_is_404_rather_than_the_index_document(bundle):
    """An SPA fallback on /assets/ turns a broken build into a blank page."""
    response = await portal_for(dist=bundle).handle(
        request("/assets/index-deadbeef.js")
    )
    assert response.status == 404
    assert response.content_type == "application/json"


@pytest.mark.parametrize(
    "path", ["/../secret.txt", "/assets/../../secret.txt", "/a/../../secret.txt"]
)
@pytest.mark.asyncio
async def test_a_path_escaping_the_bundle_directory_is_never_served(path, bundle):
    """It resolves to the SPA document or a 404 -- never to a file outside."""
    (bundle.parent / "secret.txt").write_text("not yours", encoding="utf-8")
    response = await portal_for(dist=bundle).handle(request(path))

    if isinstance(response, web.FileResponse):
        assert response._path == (bundle / "index.html").resolve()
    else:
        assert response.status == 404


@pytest.mark.parametrize(
    "path",
    ["/../secret.txt", "/assets/../../secret.txt", "/a/b/../../../secret.txt", "/"],
)
def test_containment_is_checked_on_the_resolved_path(path, bundle):
    assert portal_for(dist=bundle)._within_dist(path) is None


def test_a_path_inside_the_bundle_resolves(bundle):
    resolved = portal_for(dist=bundle)._within_dist("/assets/index-abc123.js")
    assert resolved == (bundle / "assets" / "index-abc123.js").resolve()


@pytest.mark.asyncio
async def test_with_no_bundle_the_placeholder_is_served_and_the_api_still_works(
    workspace,
):
    """The backend has to be deployable before portal-ui/ exists."""
    handler = portal_for(dist=workspace / "not-built-yet")

    page = await handler.handle(request("/"))
    assert page.status == 200
    assert page.content_type == "text/html"
    assert "not built yet" in page.text

    api = await handler.handle(request("/api/v1/me"))
    assert api.status == 200
    assert body_of(api)["is_admin"] is True


@pytest.mark.asyncio
async def test_with_no_bundle_configured_at_all_the_placeholder_is_still_served():
    page = await portal_for(dist=None).handle(request("/"))
    assert page.status == 200
    assert "not built yet" in page.text


@pytest.mark.asyncio
async def test_an_unauthenticated_browser_gets_the_plain_page_not_the_bundle(bundle):
    """portal.md's hard rule: the operational pages must not need the JS."""
    response = await portal_for(dist=bundle).handle(request("/", email=None))
    assert response.status == 401
    assert response.content_type == "text/html"
    assert "not signed in" in response.text.lower()


@pytest.mark.asyncio
async def test_the_bundle_is_read_only(bundle):
    response = await portal_for(dist=bundle).handle(request("/", method="POST"))
    assert response.status == 405
