"""GET /api/v1/costs and GET /api/v1/apps/{host}/costs.

The contract these assertions encode is the "Costs" section of
``docs/design/portal-api.md``; if one of them has to change, that file
changes first. Driven exactly like test_portal.py -- aiohttp's
``make_mocked_request`` plus fakes behind the module's protocols, no AWS.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

from proxy_app import audit, portal, registry, usage
from proxy_app.ecsctl import ServiceState
from proxy_app.registry import App

HOUR = 3600.0
DAY = 86400.0

SEPT = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
AUG = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
#: 2026-09-16 12:00 UTC -- mid-month, so month_to_date is genuinely partial.
NOW = SEPT + 15 * DAY + 12 * HOUR

ADMIN = "jake@stratevi.com"
USER = "nick@stratevi.com"
PORTAL_HOST = "shinyplatform.tools.stratevi.com"

DASHBOARD = "dashboard.tools.stratevi.com"
MODEL = "model.tools.stratevi.com"


# --- fakes -----------------------------------------------------------------


class FakeStore:
    """Returns a `__`-row too, so the portal's own filtering is exercised."""

    def __init__(self, rows: list[App]) -> None:
        self.rows = {row.host: row for row in rows}
        self.fail_list = False

    async def app(self, host):
        return self.rows.get(registry.normalize_host(host))

    async def apps(self):
        if self.fail_list:
            raise RuntimeError("dynamodb is unhappy")
        return list(self.rows.values())

    async def patch(self, host, changes):  # pragma: no cover - unused here
        raise AssertionError("costs never writes an app row")


class FakeTasks:
    async def states(self, services) -> dict[str, ServiceState]:
        return {}


class FakeRecorder:
    def __init__(self) -> None:
        self.events: list[audit.Event] = []

    def record(self, event: audit.Event) -> None:
        self.events.append(event)


class FakeLedger:
    """A :class:`portal.UsageSource` over a dict of pre-computed rows."""

    def __init__(self, rows: dict[tuple[int, int], dict] | None = None) -> None:
        self.rows = rows or {}
        self.calls: list[tuple[tuple[str, ...], int, int]] = []
        self.fail = False

    async def month(self, apps, year, month):
        self.calls.append((tuple(a.host for a in apps), year, month))
        if self.fail:
            raise RuntimeError("dynamodb is unhappy")
        return self.rows.get((year, month), {})


class Clock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# --- request plumbing (identical to test_portal.py) -------------------------


def oidc_headers(email: str | None) -> dict[str, str]:
    if email is None:
        return {"Host": PORTAL_HOST}
    claims = {"sub": "cognito-sub-1", "email": email}
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return {"Host": PORTAL_HOST, "x-amzn-oidc-data": f"header.{payload}.signature"}


def request(path="/", *, method="GET", email=ADMIN):
    return make_mocked_request(method, path, headers=CIMultiDict(oidc_headers(email)))


def body_of(response: web.Response):
    return json.loads(response.text)


def dashboard_row(**overrides) -> App:
    defaults = dict(
        host=DASHBOARD,
        app_key="dashboard",
        label="Treatment Pathway Dashboard",
        ecs_service="shiny-dashboard",
        cpu=512,
        memory=2048,
    )
    defaults.update(overrides)
    return App.create(**defaults)


def model_row(**overrides) -> App:
    defaults = dict(
        host=MODEL,
        app_key="model",
        label="Microsimulation Model",
        ecs_service="shiny-model",
        cpu=4096,
        memory=16384,
    )
    defaults.update(overrides)
    return App.create(**defaults)


def day(day_key: str, host: str, hours: float, **kw) -> usage.DayUsage:
    return usage.DayUsage(
        day=day_key, host=host, awake_seconds=hours * HOUR, **kw
    )


def rows_for(*days: usage.DayUsage) -> dict:
    return {usage.usage_sort_key(d.day, d.host): d for d in days}


def portal_for(*, store=None, ledger=None, admins=(ADMIN,), clock=None):
    async def reader():
        return admins

    return portal.Portal(
        apps=store if store is not None else FakeStore([dashboard_row()]),
        tasks=FakeTasks(),
        admins=portal.AdminList(reader, clock=Clock(0.0)),
        recorder=FakeRecorder(),
        usage=ledger,
        clock=clock or Clock(),
    )


# --- GET /api/v1/costs -----------------------------------------------------


@pytest.mark.asyncio
async def test_costs_is_admin_only():
    page = portal_for(ledger=FakeLedger())
    response = await page.handle(request("/api/v1/costs", email=USER))
    assert response.status == 403


@pytest.mark.asyncio
async def test_costs_refuses_a_write():
    page = portal_for(ledger=FakeLedger())
    response = await page.handle(request("/api/v1/costs", method="POST"))
    assert response.status == 405


@pytest.mark.asyncio
async def test_costs_is_503_when_the_ledger_is_not_wired():
    page = portal_for(ledger=None)
    response = await page.handle(request("/api/v1/costs"))
    assert response.status == 503
    assert "error" in body_of(response)


@pytest.mark.asyncio
async def test_costs_returns_both_periods_with_rates_and_a_disclaimer():
    ledger = FakeLedger(
        {
            (2026, 9): rows_for(day("2026-09-02", DASHBOARD, 10)),
            (2026, 8): rows_for(day("2026-08-02", DASHBOARD, 20)),
        }
    )
    page = portal_for(ledger=ledger)
    response = await page.handle(request("/api/v1/costs"))
    assert response.status == 200

    payload = body_of(response)
    assert payload["currency"] == "USD"
    assert payload["basis"] == "awake_time"
    assert payload["stale"] is False
    assert payload["rates"]["vcpu_hour"] == usage.FARGATE_VCPU_HOUR
    assert "Cost Explorer" in payload["disclaimer"]

    mtd = payload["month_to_date"]
    assert mtd["month"] == "2026-09"
    assert mtd["complete"] is False
    assert mtd["apps"][0]["awake_hours"] == 10.0
    assert mtd["apps"][0]["estimated_cost"] == 0.29

    previous = payload["previous_month"]
    assert previous["month"] == "2026-08"
    assert previous["complete"] is True
    assert previous["apps"][0]["awake_hours"] == 20.0


@pytest.mark.asyncio
async def test_costs_reports_overhead_as_its_own_shared_line():
    page = portal_for(ledger=FakeLedger())
    payload = body_of(await page.handle(request("/api/v1/costs")))
    overhead = payload["month_to_date"]["overhead"]

    assert overhead["shared"] is True
    assert len(overhead["lines"]) == 5
    # And no app line carries a share of it.
    for line in payload["month_to_date"]["apps"]:
        assert "overhead" not in line
        assert "overhead_share" not in line


@pytest.mark.asyncio
async def test_costs_skips_config_rows():
    store = FakeStore(
        [dashboard_row(), App.create(host="__config__"), App.create(host="__usage__")]
    )
    ledger = FakeLedger()
    page = portal_for(store=store, ledger=ledger)
    payload = body_of(await page.handle(request("/api/v1/costs")))

    hosts = [line["host"] for line in payload["month_to_date"]["apps"]]
    assert hosts == [DASHBOARD]
    # The ledger is never even asked about them.
    assert all("__config__" not in asked for asked, _, _ in ledger.calls)
    assert all("__usage__" not in asked for asked, _, _ in ledger.calls)


@pytest.mark.asyncio
async def test_costs_with_no_apps_at_all_is_the_overhead_line_alone():
    page = portal_for(store=FakeStore([]), ledger=FakeLedger())
    payload = body_of(await page.handle(request("/api/v1/costs")))
    mtd = payload["month_to_date"]
    assert mtd["apps"] == []
    assert mtd["apps_total"] == 0.0
    assert mtd["total"] == mtd["overhead"]["to_date_total"]


@pytest.mark.asyncio
async def test_costs_with_no_usage_rows_reads_as_zero_not_as_an_error():
    page = portal_for(store=FakeStore([dashboard_row(), model_row()]), ledger=FakeLedger())
    payload = body_of(await page.handle(request("/api/v1/costs")))
    lines = payload["month_to_date"]["apps"]
    assert len(lines) == 2
    assert all(line["awake_hours"] == 0.0 for line in lines)
    assert all(line["last_run"] is None for line in lines)


@pytest.mark.asyncio
async def test_a_ledger_failure_degrades_rather_than_503s():
    """The overhead line is the bigger number and does not need the ledger."""
    ledger = FakeLedger()
    ledger.fail = True
    page = portal_for(ledger=ledger)
    response = await page.handle(request("/api/v1/costs"))

    assert response.status == 200
    payload = body_of(response)
    assert payload["stale"] is True
    assert payload["month_to_date"]["overhead"]["monthly_total"] > 0


@pytest.mark.asyncio
async def test_a_store_failure_is_a_503():
    store = FakeStore([dashboard_row()])
    store.fail_list = True
    page = portal_for(store=store, ledger=FakeLedger())
    response = await page.handle(request("/api/v1/costs"))
    assert response.status == 503


@pytest.mark.asyncio
async def test_the_total_is_compute_plus_the_shared_line():
    ledger = FakeLedger({(2026, 9): rows_for(day("2026-09-02", DASHBOARD, 100))})
    page = portal_for(ledger=ledger)
    mtd = body_of(await page.handle(request("/api/v1/costs")))["month_to_date"]
    assert mtd["total"] == round(
        mtd["apps_total"] + mtd["overhead"]["to_date_total"], 2
    )


@pytest.mark.asyncio
async def test_january_asks_for_the_previous_december():
    page = portal_for(
        ledger=(ledger := FakeLedger()),
        clock=Clock(datetime(2027, 1, 10, tzinfo=timezone.utc).timestamp()),
    )
    await page.handle(request("/api/v1/costs"))
    assert [(year, month) for _, year, month in ledger.calls] == [
        (2027, 1),
        (2026, 12),
    ]


# --- GET /api/v1/apps/{host}/costs -----------------------------------------


@pytest.mark.asyncio
async def test_per_app_costs_is_admin_only():
    page = portal_for(ledger=FakeLedger())
    response = await page.handle(
        request(f"/api/v1/apps/{DASHBOARD}/costs", email=USER)
    )
    assert response.status == 403


@pytest.mark.asyncio
async def test_per_app_costs_returns_both_periods_and_a_daily_breakdown():
    ledger = FakeLedger(
        {
            (2026, 9): rows_for(
                day("2026-09-02", DASHBOARD, 4), day("2026-09-03", DASHBOARD, 6)
            ),
            (2026, 8): rows_for(day("2026-08-30", DASHBOARD, 1)),
        }
    )
    page = portal_for(ledger=ledger)
    response = await page.handle(request(f"/api/v1/apps/{DASHBOARD}/costs"))
    assert response.status == 200

    payload = body_of(response)
    assert payload["month_to_date"]["awake_hours"] == 10.0
    assert [d["day"] for d in payload["month_to_date"]["daily"]] == [
        "2026-09-02",
        "2026-09-03",
    ]
    assert payload["previous_month"]["awake_hours"] == 1.0
    assert "Cost Explorer" in payload["disclaimer"]


@pytest.mark.asyncio
async def test_per_app_costs_404s_an_unknown_host():
    page = portal_for(ledger=FakeLedger())
    response = await page.handle(request("/api/v1/apps/nope.tools.stratevi.com/costs"))
    assert response.status == 404


@pytest.mark.asyncio
async def test_per_app_costs_404s_a_config_row():
    page = portal_for(ledger=FakeLedger())
    response = await page.handle(request("/api/v1/apps/__usage__/costs"))
    assert response.status == 404


@pytest.mark.asyncio
async def test_per_app_costs_refuses_a_write():
    page = portal_for(ledger=FakeLedger())
    response = await page.handle(
        request(f"/api/v1/apps/{DASHBOARD}/costs", method="PATCH")
    )
    assert response.status == 405


@pytest.mark.asyncio
async def test_per_app_costs_is_503_when_the_ledger_is_not_wired():
    page = portal_for(ledger=None)
    response = await page.handle(request(f"/api/v1/apps/{DASHBOARD}/costs"))
    assert response.status == 503


@pytest.mark.asyncio
async def test_per_app_costs_503s_when_the_ledger_fails():
    ledger = FakeLedger()
    ledger.fail = True
    page = portal_for(ledger=ledger)
    response = await page.handle(request(f"/api/v1/apps/{DASHBOARD}/costs"))
    assert response.status == 503


@pytest.mark.asyncio
async def test_an_unknown_subresource_is_still_a_404():
    """The costs route must not have widened /apps/{host}/{anything}."""
    page = portal_for(ledger=FakeLedger())
    response = await page.handle(request(f"/api/v1/apps/{DASHBOARD}/invoices"))
    assert response.status == 404


@pytest.mark.asyncio
async def test_the_audit_route_still_works_next_to_it():
    page = portal_for(ledger=FakeLedger())
    response = await page.handle(request(f"/api/v1/apps/{DASHBOARD}/audit"))
    # No audit reader wired in this fixture -> the contract's 503, not a 404.
    assert response.status == 503
