"""The awake-hours ledger and the cost arithmetic on top of it.

Two things these tests exist to hold still:

* **The rates reproduce CLAUDE.md's cost model.** Those two $/hr figures were
  derived independently of this module; if a future edit to the per-unit
  rates stops reproducing them, one of the two is wrong and this says so.
* **Every degenerate event case has a stated answer.** The audit trail is
  best effort -- dropped sleeps, duplicate wakes and orphan closes all happen
  -- and "what does the ledger do" must be a test, not a guess.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from proxy_app import audit, usage
from proxy_app.registry import App

HOUR = 3600.0
DAY = 86400.0

# 2026-09-01 00:00:00 UTC
SEPT = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
AUG = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
OCT = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()


def ev(kind: str, at: float) -> dict:
    return {"event": kind, "email": "", "path": "", "outcome": "", "ts": at}


def wake(at: float) -> dict:
    return ev(audit.EVENT_WAKE, at)


def sleep(at: float) -> dict:
    return ev(audit.EVENT_SLEEP, at)


def dashboard(**overrides) -> App:
    base = dict(
        host="dashboard.tools.stratevi.com",
        app_key="dashboard",
        label="Treatment Pathway Dashboard",
        ecs_service="shiny-dashboard",
        cpu=512,
        memory=2048,
    )
    base.update(overrides)
    return App.create(**base)


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------


class TestRates:
    def test_reproduces_the_dashboard_hourly_figure(self):
        """CLAUDE.md: dashboard $0.0291/hr at 0.5 vCPU / 2 GB."""
        assert round(usage.hourly_rate(512, 2048), 4) == 0.0291

    def test_reproduces_the_model_hourly_figure(self):
        """CLAUDE.md: model $0.2330/hr at 4 vCPU / 16 GB."""
        assert round(usage.hourly_rate(4096, 16384), 4) == 0.2330

    def test_reproduces_the_proxy_monthly_figure(self):
        """CLAUDE.md: the always-on proxy task is about $9.01/month."""
        monthly = (
            usage.hourly_rate(usage.PROXY_CPU_UNITS, usage.PROXY_MEMORY_MIB)
            * usage.HOURS_PER_MONTH
        )
        assert round(monthly, 2) == 9.01

    def test_the_two_derived_figures_come_from_the_same_two_constants(self):
        # Not a tautology: it is the check that someone who edits one rate
        # has to look at both known figures.
        assert usage.FARGATE_VCPU_HOUR == 0.04048
        assert usage.FARGATE_GB_HOUR == 0.004445

    def test_per_second_not_per_hour(self):
        # Fargate bills per second; half an hour costs half the hourly rate.
        assert usage.cost_for_seconds(512, 2048, 1800) == pytest.approx(
            usage.hourly_rate(512, 2048) / 2
        )

    def test_zero_size_is_free_rather_than_an_error(self):
        assert usage.hourly_rate(0, 0) == 0.0
        assert usage.cost_for_seconds(0, 0, 10 * HOUR) == 0.0

    def test_negative_seconds_never_produce_a_credit(self):
        assert usage.cost_for_seconds(512, 2048, -100) == 0.0


class TestSizeOf:
    def test_portal_created_row_uses_its_own_size(self):
        assert usage.size_of(dashboard(cpu=1024, memory=4096)) == (1024, 4096, True)

    def test_legacy_row_falls_back_to_the_known_app_key(self):
        assert usage.size_of(dashboard(cpu=0, memory=0)) == (512, 2048, True)
        assert usage.size_of(
            App.create(host="model.tools.stratevi.com", app_key="model")
        ) == (4096, 16384, True)

    def test_unknown_legacy_row_is_reported_unknown_not_guessed(self):
        cpu, memory, known = usage.size_of(
            App.create(host="mystery.tools.stratevi.com", app_key="mystery")
        )
        assert (cpu, memory, known) == (0, 0, False)


# ---------------------------------------------------------------------------
# Interval pairing -- the degenerate cases
# ---------------------------------------------------------------------------


class TestPairing:
    def test_the_ordinary_case(self):
        result = usage.pair_events(
            [wake(SEPT), sleep(SEPT + 2 * HOUR)], now=SEPT + 3 * HOUR
        )
        assert result.seconds == 2 * HOUR
        assert result.anomalies == {}
        assert result.last_end == SEPT + 2 * HOUR

    def test_force_sleep_and_expired_close_an_interval_too(self):
        """All three closers scale the task to zero, so all three end a bill."""
        for kind in (audit.EVENT_FORCE_SLEEP, audit.EVENT_EXPIRED):
            result = usage.pair_events(
                [wake(SEPT), ev(kind, SEPT + HOUR)], now=SEPT + 2 * HOUR
            )
            assert result.seconds == HOUR, kind

    def test_unrelated_events_are_ignored(self):
        result = usage.pair_events(
            [
                ev(audit.EVENT_ALLOW, SEPT),
                wake(SEPT + HOUR),
                ev(audit.EVENT_DENY, SEPT + 90 * 60),
                ev(audit.EVENT_CONFIG_CHANGE, SEPT + 100 * 60),
                sleep(SEPT + 2 * HOUR),
            ],
            now=SEPT + 3 * HOUR,
        )
        assert result.seconds == HOUR
        assert result.anomalies == {}

    def test_still_awake_is_an_open_interval_to_now(self):
        result = usage.pair_events([wake(SEPT)], now=SEPT + 90 * 60)
        assert result.seconds == 90 * 60
        assert result.currently_open
        assert result.anomalies == {}

    def test_wake_with_no_sleep_is_capped_not_unbounded(self):
        """One dropped `sleep` must not make an app look awake forever."""
        result = usage.pair_events(
            [wake(SEPT)], now=SEPT + 40 * DAY, open_cap_seconds=24 * HOUR
        )
        assert result.seconds == 24 * HOUR
        assert result.anomalies == {"unclosed": 1}
        assert result.intervals[0].unclosed is True
        assert result.intervals[0].open is False

    def test_two_wakes_in_a_row_do_not_double_count(self):
        result = usage.pair_events(
            [wake(SEPT), wake(SEPT + HOUR), sleep(SEPT + 3 * HOUR)],
            now=SEPT + 4 * HOUR,
        )
        assert result.seconds == 3 * HOUR
        assert result.anomalies == {"duplicate_wake": 1}
        assert len(result.intervals) == 1

    def test_leading_orphan_close_infers_it_was_awake_at_the_window_start(self):
        result = usage.pair_events(
            [sleep(SEPT + HOUR)], now=SEPT + 2 * HOUR, window_start=SEPT
        )
        assert result.seconds == HOUR
        assert result.anomalies == {"awake_at_window_start": 1}

    def test_the_inferred_start_is_bounded_by_the_open_cap(self):
        """A window that begins days before the close must not invent days."""
        result = usage.pair_events(
            [sleep(SEPT + HOUR)],
            now=SEPT + 2 * HOUR,
            window_start=SEPT - 8 * DAY,
            open_cap_seconds=6 * HOUR,
        )
        assert result.seconds == 6 * HOUR
        assert result.anomalies == {"awake_at_window_start": 1}

    def test_leading_orphan_close_with_no_window_contributes_nothing(self):
        result = usage.pair_events([sleep(SEPT + HOUR)], now=SEPT + 2 * HOUR)
        assert result.seconds == 0.0
        assert result.anomalies["awake_at_window_start"] == 1

    def test_a_later_orphan_close_is_ignored(self):
        result = usage.pair_events(
            [wake(SEPT), sleep(SEPT + HOUR), sleep(SEPT + 2 * HOUR)],
            now=SEPT + 3 * HOUR,
        )
        assert result.seconds == HOUR
        assert result.anomalies == {"orphan_close": 1}

    def test_two_closes_in_a_row_after_a_pair(self):
        result = usage.pair_events(
            [
                wake(SEPT),
                sleep(SEPT + HOUR),
                ev(audit.EVENT_FORCE_SLEEP, SEPT + 2 * HOUR),
                ev(audit.EVENT_EXPIRED, SEPT + 3 * HOUR),
            ],
            now=SEPT + 4 * HOUR,
        )
        assert result.seconds == HOUR
        assert result.anomalies == {"orphan_close": 2}

    def test_awake_at_start_false_refuses_the_inference(self):
        result = usage.pair_events(
            [sleep(SEPT + HOUR)],
            now=SEPT + 2 * HOUR,
            window_start=SEPT,
            awake_at_start=False,
        )
        assert result.seconds == 0.0
        assert result.anomalies == {"orphan_close": 1}

    def test_awake_at_start_true_opens_at_the_window_start(self):
        result = usage.pair_events(
            [sleep(SEPT + HOUR)],
            now=SEPT + 2 * HOUR,
            window_start=SEPT,
            awake_at_start=True,
        )
        assert result.seconds == HOUR
        assert "awake_at_window_start" not in result.anomalies

    def test_out_of_order_events_are_sorted_first(self):
        result = usage.pair_events(
            [sleep(SEPT + 2 * HOUR), wake(SEPT)], now=SEPT + 3 * HOUR
        )
        assert result.seconds == 2 * HOUR
        assert result.anomalies == {}

    def test_undated_events_are_dropped_and_counted(self):
        result = usage.pair_events(
            [
                {"event": audit.EVENT_WAKE, "ts": None},
                {"event": audit.EVENT_SLEEP, "ts": "not a number"},
                wake(SEPT),
                sleep(SEPT + HOUR),
            ],
            now=SEPT + 2 * HOUR,
        )
        assert result.seconds == HOUR
        assert result.anomalies == {"undated": 2}

    def test_a_backwards_pair_contributes_zero(self):
        """Clock skew between two proxy tasks, not a negative bill."""
        result = usage.pair_events(
            [wake(SEPT + HOUR), sleep(SEPT + HOUR)], now=SEPT + 2 * HOUR
        )
        assert result.seconds == 0.0
        assert result.anomalies == {"nonpositive": 1}

    def test_no_events_at_all(self):
        result = usage.pair_events([], now=SEPT)
        assert result.seconds == 0.0
        assert result.intervals == ()
        assert result.last_end is None
        assert result.currently_open is False

    def test_open_cap_for_uses_the_apps_own_session_cap(self):
        assert usage.open_cap_for(dashboard(max_session_hours=12)) == 13 * HOUR
        assert usage.open_cap_for(dashboard(max_session_hours=0)) == (
            usage.DEFAULT_OPEN_CAP_SECONDS
        )


# ---------------------------------------------------------------------------
# Clipping, days and month boundaries
# ---------------------------------------------------------------------------


class TestWindows:
    def test_clip_trims_to_the_window(self):
        intervals = (usage.Interval(SEPT - HOUR, SEPT + HOUR),)
        assert usage.seconds_in(intervals, SEPT, SEPT + DAY) == HOUR

    def test_an_interval_wholly_outside_the_window_contributes_nothing(self):
        intervals = (usage.Interval(AUG, AUG + HOUR),)
        assert usage.seconds_in(intervals, SEPT, OCT) == 0.0

    def test_an_interval_straddling_midnight_splits_across_two_days(self):
        # 23:00 to 01:00 on 2026-09-04/05.
        start = SEPT + 3 * DAY + 23 * HOUR
        per_day = usage.seconds_by_day(
            (usage.Interval(start, start + 2 * HOUR),), SEPT, SEPT + 6 * DAY
        )
        assert per_day["2026-09-04"] == HOUR
        assert per_day["2026-09-05"] == HOUR

    def test_an_interval_straddling_a_month_boundary_is_clipped_to_each(self):
        # 2026-08-31 22:00 -> 2026-09-01 02:00.
        interval = usage.Interval(SEPT - 2 * HOUR, SEPT + 2 * HOUR)
        assert usage.seconds_in((interval,), AUG, SEPT) == 2 * HOUR
        assert usage.seconds_in((interval,), SEPT, OCT) == 2 * HOUR

    def test_a_multi_day_interval_lands_on_every_day_it_covers(self):
        per_day = usage.seconds_by_day(
            (usage.Interval(SEPT + 12 * HOUR, SEPT + 3 * DAY + 6 * HOUR),),
            SEPT,
            SEPT + 5 * DAY,
        )
        assert per_day["2026-09-01"] == 12 * HOUR
        assert per_day["2026-09-02"] == DAY
        assert per_day["2026-09-03"] == DAY
        assert per_day["2026-09-04"] == 6 * HOUR
        assert per_day["2026-09-05"] == 0.0

    def test_quiet_days_are_present_as_zero(self):
        per_day = usage.seconds_by_day((), SEPT, SEPT + 3 * DAY)
        assert per_day == {
            "2026-09-01": 0.0,
            "2026-09-02": 0.0,
            "2026-09-03": 0.0,
        }

    def test_month_bounds_and_rollover(self):
        assert usage.month_bounds(2026, 9) == (SEPT, OCT)
        start, end = usage.month_bounds(2026, 12)
        assert end == datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp()

    def test_previous_month_crosses_the_year(self):
        assert usage.previous_month(2026, 9) == (2026, 8)
        assert usage.previous_month(2026, 1) == (2025, 12)

    def test_days_in_month_knows_about_february(self):
        assert usage.days_in_month(2026, 2) == 28
        assert usage.days_in_month(2028, 2) == 29
        assert usage.days_in_month(2026, 9) == 30

    def test_day_key_is_utc(self):
        assert usage.day_key(SEPT) == "2026-09-01"
        assert usage.day_key(SEPT + 23 * HOUR + 3599) == "2026-09-01"
        assert usage.day_key(SEPT + DAY) == "2026-09-02"


# ---------------------------------------------------------------------------
# Overhead
# ---------------------------------------------------------------------------


class TestOverhead:
    def test_it_is_labelled_shared_and_is_never_divided(self):
        block = usage.overhead_block(1.0)
        assert block["shared"] is True
        assert "invented by division" in block["note"]
        assert all("per_app" not in line for line in block["lines"])

    def test_the_alb_line_matches_the_documented_floor(self):
        alb = next(
            line
            for line in usage.overhead_lines()
            if line["name"] == "Application Load Balancer"
        )
        assert alb["monthly"] == 16.43

    def test_the_proxy_line_is_priced_through_the_same_rate_function(self):
        proxy = next(
            line
            for line in usage.overhead_lines()
            if line["name"] == "Authorizing proxy task"
        )
        assert proxy["monthly"] == 9.01

    def test_the_total_is_the_platforms_documented_fixed_cost(self):
        # CLAUDE.md: "Fixed cost ~$29/month".
        assert 28.0 <= usage.overhead_block(1.0)["monthly_total"] <= 31.0

    def test_month_to_date_is_prorated_by_elapsed_time_not_by_app(self):
        block = usage.overhead_block(0.5)
        assert block["to_date_total"] == round(block["monthly_total"] * 0.5, 2)

    def test_the_fraction_is_clamped(self):
        assert usage.overhead_block(-1.0)["elapsed_fraction"] == 0.0
        assert usage.overhead_block(9.0)["elapsed_fraction"] == 1.0


# ---------------------------------------------------------------------------
# The stored ledger
# ---------------------------------------------------------------------------


class FakeUsageStore:
    """In-memory :class:`usage.UsageStore`, counting every call."""

    def __init__(self, events: dict[str, list[dict]] | None = None) -> None:
        self.events = events or {}
        self.rows: dict[str, usage.DayUsage] = {}
        self.event_queries: list[str] = []
        self.day_reads = 0
        self.writes: list[list[usage.DayUsage]] = []
        self.fail_events = False
        self.fail_writes = False

    async def lifecycle_events(self, host, start, end):
        self.event_queries.append(host)
        if self.fail_events:
            raise RuntimeError("dynamodb is unhappy")
        return [
            event
            for event in self.events.get(host, [])
            if start <= float(event["ts"]) <= end
        ]

    async def read_days(self, first_day, last_day):
        self.day_reads += 1
        return [
            row
            for key, row in self.rows.items()
            if first_day <= key.split("#", 1)[0] <= last_day
        ]

    async def write_days(self, rows):
        if self.fail_writes:
            raise RuntimeError("dynamodb is unhappy")
        self.writes.append(list(rows))
        for row in rows:
            self.rows[usage.usage_sort_key(row.day, row.host)] = row


HOST = "dashboard.tools.stratevi.com"


def ledger(store, now):
    return usage.UsageLedger(store, clock=lambda: now)


class TestLedger:
    @pytest.mark.asyncio
    async def test_a_finished_month_is_computed_and_stored(self):
        store = FakeUsageStore(
            {HOST: [wake(SEPT + 9 * HOUR), sleep(SEPT + 11 * HOUR)]}
        )
        rows = await ledger(store, OCT + DAY).month([dashboard()], 2026, 9)

        first = rows[usage.usage_sort_key("2026-09-01", HOST)]
        assert first.awake_seconds == 2 * HOUR
        assert first.final is True
        # Every day of September has a row, including the quiet ones.
        assert len([k for k in rows if k.endswith(HOST)]) == 30

    @pytest.mark.asyncio
    async def test_running_it_twice_is_idempotent(self):
        store = FakeUsageStore(
            {HOST: [wake(SEPT + 9 * HOUR), sleep(SEPT + 11 * HOUR)]}
        )
        book = ledger(store, OCT + DAY)
        first = await book.month([dashboard()], 2026, 9)
        queries_after_first = len(store.event_queries)
        second = await book.month([dashboard()], 2026, 9)

        assert {k: v.awake_seconds for k, v in first.items()} == {
            k: v.awake_seconds for k, v in second.items()
        }
        # And the second pass did no raw-trail work at all: a finished day is
        # computed exactly once.
        assert len(store.event_queries) == queries_after_first

    @pytest.mark.asyncio
    async def test_todays_partial_row_is_refreshed_but_yesterdays_is_not(self):
        now = SEPT + 2 * DAY + 10 * HOUR
        store = FakeUsageStore({HOST: [wake(SEPT + DAY), sleep(SEPT + DAY + HOUR)]})
        book = usage.UsageLedger(store, clock=lambda: now, refresh_seconds=0.0)

        await book.month([dashboard()], 2026, 9)
        assert len(store.event_queries) == 1
        await book.month([dashboard()], 2026, 9)
        # Today is stale immediately (refresh_seconds=0), so it recomputes --
        # but yesterday's row came back `final` and was not what triggered it.
        assert len(store.event_queries) == 2
        assert store.rows[usage.usage_sort_key("2026-09-02", HOST)].final is True
        assert store.rows[usage.usage_sort_key("2026-09-03", HOST)].final is False

    @pytest.mark.asyncio
    async def test_a_fresh_today_row_is_not_recomputed(self):
        now = SEPT + 2 * DAY + 10 * HOUR
        store = FakeUsageStore({HOST: []})
        book = usage.UsageLedger(store, clock=lambda: now, refresh_seconds=3600.0)
        await book.month([dashboard()], 2026, 9)
        await book.month([dashboard()], 2026, 9)
        assert len(store.event_queries) == 1

    @pytest.mark.asyncio
    async def test_a_month_that_has_not_started_reads_nothing(self):
        store = FakeUsageStore()
        rows = await ledger(store, SEPT).month([dashboard()], 2026, 10)
        assert rows == {}
        assert store.event_queries == []

    @pytest.mark.asyncio
    async def test_a_currently_awake_app_accrues_to_now(self):
        now = SEPT + 5 * HOUR
        store = FakeUsageStore({HOST: [wake(SEPT + 3 * HOUR)]})
        rows = await ledger(store, now).month([dashboard()], 2026, 9)
        assert rows[usage.usage_sort_key("2026-09-01", HOST)].awake_seconds == 2 * HOUR

    @pytest.mark.asyncio
    async def test_an_interval_from_the_previous_month_is_clipped_not_dropped(self):
        # Awake 2026-08-31 22:00 -> 2026-09-01 02:00.
        store = FakeUsageStore({HOST: [wake(SEPT - 2 * HOUR), sleep(SEPT + 2 * HOUR)]})
        rows = await ledger(store, OCT + DAY).month([dashboard()], 2026, 9)
        assert rows[usage.usage_sort_key("2026-09-01", HOST)].awake_seconds == 2 * HOUR

    @pytest.mark.asyncio
    async def test_a_trail_read_failure_does_not_take_the_report_down(self):
        store = FakeUsageStore()
        store.fail_events = True
        rows = await ledger(store, OCT + DAY).month([dashboard()], 2026, 9)
        assert rows == {}

    @pytest.mark.asyncio
    async def test_a_write_failure_still_returns_the_numbers(self):
        store = FakeUsageStore(
            {HOST: [wake(SEPT + 9 * HOUR), sleep(SEPT + 11 * HOUR)]}
        )
        store.fail_writes = True
        rows = await ledger(store, OCT + DAY).month([dashboard()], 2026, 9)
        assert rows[usage.usage_sort_key("2026-09-01", HOST)].awake_seconds == 2 * HOUR

    @pytest.mark.asyncio
    async def test_one_day_read_serves_every_app(self):
        store = FakeUsageStore({HOST: [], "b.tools.stratevi.com": []})
        await ledger(store, OCT + DAY).month(
            [dashboard(), dashboard(host="b.tools.stratevi.com")], 2026, 9
        )
        assert store.day_reads == 1


class TestRowEncoding:
    def test_round_trips_through_the_dynamo_item_shape(self):
        row = usage.DayUsage(
            day="2026-09-04",
            host=HOST,
            awake_seconds=7200.5,
            final=True,
            computed_at=1_800_000_000.0,
            last_end=1_790_000_000.0,
            anomalies={"unclosed": 2},
        )
        back = usage.day_from_item(usage.day_item(row))
        assert back.day == row.day
        assert back.host == row.host
        assert back.awake_seconds == pytest.approx(row.awake_seconds, abs=0.001)
        assert back.final is True
        assert back.anomalies == {"unclosed": 2}

    def test_the_partition_key_is_a_config_prefix_not_an_app(self):
        """`__`-rows are configuration; the registry and sleeper already skip
        them, and the portal 404s them on every app route."""
        from proxy_app import registry

        assert usage.USAGE_PARTITION.startswith(registry.CONFIG_PREFIX)
        assert registry.is_config_host(usage.USAGE_PARTITION)

    def test_the_sort_key_is_date_first_so_a_month_is_one_query(self):
        keys = sorted(
            [
                usage.usage_sort_key("2026-09-02", "a.example.com"),
                usage.usage_sort_key("2026-09-01", "z.example.com"),
            ]
        )
        assert keys[0].startswith("2026-09-01")

    def test_a_row_carries_no_ttl(self):
        """Audit rows expire after 90 days; a rollup that vanished would take
        the previous month's comparison with it."""
        assert "ttl" not in usage.day_item(usage.DayUsage(day="2026-09-01", host=HOST))

    def test_a_malformed_anomalies_blob_reads_as_empty(self):
        item = usage.day_item(usage.DayUsage(day="2026-09-01", host=HOST))
        item["anomalies"] = {"S": "not json"}
        assert usage.day_from_item(item).anomalies == {}

    def test_anomalies_are_stored_as_json(self):
        item = usage.day_item(
            usage.DayUsage(day="2026-09-01", host=HOST, anomalies={"unclosed": 1})
        )
        assert json.loads(item["anomalies"]["S"]) == {"unclosed": 1}


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------


def row(day: str, host: str, hours: float, **kw) -> usage.DayUsage:
    return usage.DayUsage(day=day, host=host, awake_seconds=hours * HOUR, **kw)


class TestPayload:
    def test_an_apps_line_sums_the_month_and_prices_it(self):
        rows = {
            usage.usage_sort_key("2026-09-01", HOST): row("2026-09-01", HOST, 2),
            usage.usage_sort_key("2026-09-02", HOST): row("2026-09-02", HOST, 3),
        }
        line = usage.app_costs(dashboard(), rows, 2026, 9)
        assert line["awake_hours"] == 5.0
        assert line["estimated_cost"] == round(5 * 0.02913, 2)
        assert line["size_known"] is True
        assert [d["day"] for d in line["daily"]] == ["2026-09-01", "2026-09-02"]

    def test_an_app_with_no_rows_reads_as_zero_not_as_missing(self):
        line = usage.app_costs(dashboard(), {}, 2026, 9)
        assert line["awake_hours"] == 0.0
        assert line["estimated_cost"] == 0.0
        assert line["last_run"] is None
        assert line["daily"] == []

    def test_an_unknown_size_costs_nothing_and_says_so(self):
        app = App.create(host="mystery.tools.stratevi.com", app_key="mystery")
        rows = {
            usage.usage_sort_key("2026-09-01", app.host): row(
                "2026-09-01", app.host, 10
            )
        }
        line = usage.app_costs(app, rows, 2026, 9)
        assert line["awake_hours"] == 10.0
        assert line["estimated_cost"] == 0.0
        assert line["size_known"] is False
        assert line["hourly_rate"] is None

    def test_rows_for_another_month_are_not_counted(self):
        rows = {usage.usage_sort_key("2026-08-31", HOST): row("2026-08-31", HOST, 9)}
        assert usage.app_costs(dashboard(), rows, 2026, 9)["awake_hours"] == 0.0

    def test_anomalies_surface_on_the_line(self):
        rows = {
            usage.usage_sort_key("2026-09-01", HOST): row(
                "2026-09-01", HOST, 24, anomalies={"unclosed": 1}
            )
        }
        assert usage.app_costs(dashboard(), rows, 2026, 9)["anomalies"] == {
            "unclosed": 1
        }

    def test_a_month_payload_carries_apps_overhead_and_a_total(self):
        rows = {usage.usage_sort_key("2026-09-01", HOST): row("2026-09-01", HOST, 10)}
        payload = usage.month_payload([dashboard()], rows, 2026, 9, OCT + DAY)

        assert payload["month"] == "2026-09"
        assert payload["complete"] is True
        assert payload["apps_total"] == 0.29
        assert payload["overhead"]["shared"] is True
        assert payload["total"] == round(
            payload["apps_total"] + payload["overhead"]["monthly_total"], 2
        )

    def test_month_to_date_uses_the_prorated_overhead(self):
        payload = usage.month_payload([dashboard()], {}, 2026, 9, SEPT + 15 * DAY)
        assert payload["complete"] is False
        assert payload["total"] == payload["overhead"]["to_date_total"]
        assert payload["overhead"]["to_date_total"] < payload["overhead"][
            "monthly_total"
        ]

    def test_apps_are_ordered_most_expensive_first(self):
        cheap = dashboard(host="cheap.tools.stratevi.com", label="Cheap")
        dear = App.create(
            host="dear.tools.stratevi.com",
            label="Dear",
            app_key="model",
            cpu=4096,
            memory=16384,
        )
        rows = {
            usage.usage_sort_key("2026-09-01", cheap.host): row(
                "2026-09-01", cheap.host, 5
            ),
            usage.usage_sort_key("2026-09-01", dear.host): row(
                "2026-09-01", dear.host, 5
            ),
        }
        payload = usage.month_payload([cheap, dear], rows, 2026, 9, OCT + DAY)
        assert [line["host"] for line in payload["apps"]] == [dear.host, cheap.host]

    def test_no_apps_at_all_is_the_overhead_line_alone(self):
        payload = usage.month_payload([], {}, 2026, 9, OCT + DAY)
        assert payload["apps"] == []
        assert payload["apps_total"] == 0.0
        assert payload["total"] == payload["overhead"]["monthly_total"]

    def test_the_rates_block_names_its_source_and_region(self):
        block = usage.rates_block()
        assert block["region"] == "us-east-1"
        assert block["vcpu_hour"] == usage.FARGATE_VCPU_HOUR
        assert "Fargate" in block["source"]

    def test_the_disclaimer_points_at_cost_explorer(self):
        assert "Cost Explorer" in usage.DISCLAIMER
        assert "not billed amounts" in usage.DISCLAIMER
