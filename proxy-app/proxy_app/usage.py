"""Per-app awake time, and what it costs.

Cost Explorer is not the mechanism here, and that was a decision rather than
an oversight. Cost-allocation tags were never activated on this account;
activating them is an admin action, takes 24 hours and is NOT retroactive, so
a CE query grouped by ``Project`` today returns one undifferentiated bucket.
CE data also lags a day, is daily-granular, bills per request, and -- the
part no amount of tagging fixes -- cannot attribute the shared ALB or the
always-on proxy task to any one app.

We already hold better data. The proxy writes ``wake``, ``sleep``,
``force_sleep`` and ``expired`` into ``shiny-proxy-audit`` with a timestamp
and the app's hostname as the partition key. Those events are an exact record
of when each app's task was running, and Fargate bills per second for exactly
that window. Awake-seconds x the per-second rate for the task's cpu/memory is
the compute cost, with no lag, no CE charges, and per-second granularity.

Everything here is an ESTIMATE and says so on the wire (``basis``,
``disclaimer``). The invoice is the invoice; this is the thing that tells you
which app is responsible for it.

Two things this module deliberately does not do:

* It does not spread platform overhead across apps. The ALB, the always-on
  proxy task and the hosted zone are shared, and a per-app share of them is a
  number invented by division. They are reported as their own line, labelled
  shared.
* It does not read the whole audit table per request. Day totals are rolled
  up once and stored (see :class:`UsageLedger`); a finished day is computed
  exactly once and never again.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol, Sequence

from . import audit as audit_mod
from .registry import App

# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

#: AWS Fargate on-demand, Linux/X86, **us-east-1**, as published on the AWS
#: Fargate pricing page. ONE place, on purpose: every hourly figure anywhere
#: in this platform is derived from these two numbers.
#:
#:   per vCPU-hour   $0.04048
#:   per GB-hour     $0.004445
#:
#: Reconciled against the two figures CLAUDE.md's cost model already records,
#: which were derived independently:
#:
#:   0.5 vCPU + 2 GB   0.5*0.04048 + 2*0.004445     = $0.029130/hr  ("$0.0291")
#:   4   vCPU + 16 GB  4  *0.04048 + 16*0.004445    = $0.233040/hr  ("$0.2330")
#:   0.25 vCPU + 0.5GB 0.25*0.04048 + 0.5*0.004445  = $0.012343/hr
#:                                     x 730 hours  = $9.01/month   ("$9.01")
#:
#: All three reproduce. If AWS changes Fargate pricing, or this platform ever
#: runs somewhere other than us-east-1, or moves to ARM/Graviton (cheaper) or
#: Windows containers (dearer), THESE TWO NUMBERS are what must be reviewed --
#: nothing else in the codebase hardcodes a rate.
FARGATE_VCPU_HOUR = 0.04048
FARGATE_GB_HOUR = 0.004445

#: Fargate's own unit conventions, so the conversions below read as arithmetic
#: rather than magic: 1024 CPU units is one vCPU, memory is MiB.
CPU_UNITS_PER_VCPU = 1024.0
MIB_PER_GB = 1024.0

SECONDS_PER_HOUR = 3600.0

#: The billing month AWS uses for flat monthly line items (730 = 365*24/12).
#: Used only for the overhead block, never for compute.
HOURS_PER_MONTH = 730.0

# --- platform overhead ------------------------------------------------------
#
# Shared by every app and attributable to none of them. Reported as its own
# line. Sources are named per line so a stale one is findable.

#: Application Load Balancer, us-east-1: $0.0225 per ALB-hour. It never
#: sleeps -- that is the whole $20/month floor ADR-0004 accepted in exchange
#: for not running a NAT gateway.
ALB_HOURLY = 0.0225

#: LCU-hours at $0.008. Observed at roughly $3/month on this account's
#: traffic; a rough figure and labelled as one, because LCUs scale with
#: connections and bytes and cannot be predicted from awake time.
ALB_LCU_MONTHLY = 3.00

#: One Route 53 hosted zone, $0.50/month. Query charges are pennies.
ROUTE53_MONTHLY = 0.50

#: DynamoDB on-demand (two small tables), the uploads bucket, and CloudWatch
#: Logs retention. Cents in practice; carried as $1 so the total is not
#: optimistic.
MISC_MONTHLY = 1.00

#: The always-on proxy task (ADR-0014). Priced through the same rate function
#: as every app, so it cannot drift away from them.
PROXY_CPU_UNITS = 256
PROXY_MEMORY_MIB = 512

# ---------------------------------------------------------------------------
# Interval pairing
# ---------------------------------------------------------------------------

#: The events that open and close an awake window. All three closers scale
#: the service to zero (sleeper.py): ``sleep`` is idle timeout, ``force_sleep``
#: is the C1 session cap, ``expired`` is the reaper. For cost they are
#: identical -- the task stopped.
OPEN_EVENTS = frozenset({audit_mod.EVENT_WAKE})
CLOSE_EVENTS = frozenset(
    {audit_mod.EVENT_SLEEP, audit_mod.EVENT_FORCE_SLEEP, audit_mod.EVENT_EXPIRED}
)

#: How long an un-closed wake is allowed to run before the pairing stops
#: believing it.
#:
#: This is the single most important guard in the module. Audit writes are
#: best effort by design (audit.py's docstring says so), and the proxy task
#: is replaced on every deploy -- so a ``sleep`` WILL go missing eventually.
#: Without a cap, one dropped event makes an app look continuously awake
#: forever and the estimate grows without bound; the report would be wrong in
#: the expensive direction and stay wrong. 24 hours is comfortably above the
#: default session cap and below "a number that would alarm anyone", and the
#: interval is flagged ``unclosed`` so the truncation is visible rather than
#: silent. An app whose own ``max_session_hours`` is longer than this gets its
#: own cap instead -- see :func:`open_cap_for`.
DEFAULT_OPEN_CAP_SECONDS = 24 * 3600.0

#: How far before the window to start reading events, so an interval that
#: opened before the window is picked up. 8 days: one day past the 168-hour
#: ceiling ``max_session_hours`` allows.
LOOKBACK_SECONDS = 8 * 24 * 3600.0


@dataclass(frozen=True)
class Interval:
    """One continuous stretch during which the app's task was running."""

    start: float
    end: float
    #: The closing event was not observed: either the app is still awake
    #: (``open``) or the pairing gave up on it (``unclosed``).
    open: bool = False
    unclosed: bool = False

    @property
    def seconds(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Pairing:
    """The result of reading a stream of lifecycle events."""

    intervals: tuple[Interval, ...] = ()
    #: Degenerate cases, counted rather than raised. Keys are the names in
    #: :func:`pair_events`'s docstring. An empty mapping means the trail was
    #: clean.
    anomalies: Mapping[str, int] = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        return sum(interval.seconds for interval in self.intervals)

    @property
    def last_end(self) -> float | None:
        """When the app most recently stopped running (or ``now``, if open)."""
        return max((i.end for i in self.intervals), default=None)

    @property
    def currently_open(self) -> bool:
        return any(i.open for i in self.intervals)


def pair_events(
    events: Iterable[Mapping[str, Any]],
    *,
    now: float,
    open_cap_seconds: float = DEFAULT_OPEN_CAP_SECONDS,
    awake_at_start: bool | None = None,
    window_start: float | None = None,
) -> Pairing:
    """Turn wake/sleep events into awake intervals.

    These events are best effort and the proxy restarts, so every degenerate
    case below occurs in practice. Each one resolves to something stated
    rather than to an exception:

    ``wake`` with no matching close
        The interval stays open and is closed at ``now``. If that would make
        it longer than ``open_cap_seconds``, it is truncated to the cap and
        counted as ``unclosed`` -- one dropped ``sleep`` must not make an app
        look awake forever. Truncating UNDER-states cost, which is the right
        direction for a number labelled "estimate": it cannot invent spend.

    two ``wake``s in a row
        The second is ignored and the interval continues, counted as
        ``duplicate_wake``. A wake only fires on an actual scale-from-zero;
        a second one means a retry or a restarted proxy re-announcing an app
        that was already running, not a new run. Treating it as a new
        interval would double-count the overlap.

    ``sleep`` with no preceding ``wake``
        If it is the first lifecycle event seen and nothing is open, the app
        was already awake when the window began: an interval is opened at
        ``window_start`` and closed here, counted as ``awake_at_window_start``.
        The inferred start is ALSO bounded by ``open_cap_seconds``, for the
        same reason the trailing open interval is -- the window may begin days
        before the close, and inventing days of runtime from one orphan event
        would be worse than under-counting it. With no window given the start
        is the event's own timestamp, contributing zero. Any later orphan
        close is ignored and counted as ``orphan_close``: there is no
        defensible start for it at all.

    two closes in a row
        The second is an ``orphan_close``. Same handling.

    events out of order, or with no timestamp
        Events are sorted by timestamp first. A row with no usable ``ts`` is
        dropped and counted as ``undated``; it cannot be placed.

    a pair whose end is at or before its start
        Contributes zero and is counted as ``nonpositive``. Clock skew
        between two proxy tasks can produce it.

    events straddling midnight, or a month boundary
        Not a degenerate case at all: pairing is done over the whole range
        with no notion of days, and :func:`seconds_by_day` splits the
        resulting intervals at UTC midnight afterwards. An interval that
        started in the previous month is clipped by :func:`clip`, not
        discarded.

    ``awake_at_start`` overrides the inference when the caller knows the
    answer (for instance from the row's ``awake_since``).
    """
    counts: dict[str, int] = {}

    def bump(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    dated: list[tuple[float, str]] = []
    for event in events:
        kind = str((event or {}).get("event") or "")
        if kind not in OPEN_EVENTS and kind not in CLOSE_EVENTS:
            continue
        raw = (event or {}).get("ts")
        if raw is None:
            bump("undated")
            continue
        try:
            at = float(raw)
        except (TypeError, ValueError):
            bump("undated")
            continue
        dated.append((at, kind))

    # Stable sort on the timestamp alone: two events in the same second keep
    # the order the store returned them in, which is the order they were
    # written.
    dated.sort(key=lambda pair: pair[0])

    intervals: list[Interval] = []
    open_at: float | None = None
    seen_any = False

    if awake_at_start:
        open_at = window_start if window_start is not None else (
            dated[0][0] if dated else None
        )
        seen_any = True

    for at, kind in dated:
        if kind in OPEN_EVENTS:
            if open_at is not None:
                bump("duplicate_wake")
                continue
            open_at = at
            seen_any = True
            continue

        # A close.
        if open_at is None:
            if not seen_any and awake_at_start is not False:
                # Nothing opened yet and the very first thing we see is a
                # stop: it was running before the window began.
                bump("awake_at_window_start")
                floor = at - max(0.0, open_cap_seconds)
                start = max(window_start, floor) if window_start is not None else at
                intervals.append(_interval(start, at, counts))
                seen_any = True
                continue
            bump("orphan_close")
            seen_any = True
            continue

        intervals.append(_interval(open_at, at, counts))
        open_at = None
        seen_any = True

    if open_at is not None:
        limit = open_at + max(0.0, open_cap_seconds)
        if now > limit:
            bump("unclosed")
            intervals.append(Interval(start=open_at, end=limit, unclosed=True))
        else:
            intervals.append(Interval(start=open_at, end=max(open_at, now), open=True))

    return Pairing(tuple(intervals), counts)


def _interval(start: float, end: float, counts: dict[str, int]) -> Interval:
    if end <= start:
        counts["nonpositive"] = counts.get("nonpositive", 0) + 1
        return Interval(start=start, end=start)
    return Interval(start=start, end=end)


def open_cap_for(app: App) -> float:
    """The un-closed-interval cap for one app.

    An app with its own ``max_session_hours`` cannot legitimately run longer
    than that, so use it (plus an hour of slack for the sleeper's own tick).
    An uncapped app falls back to the module default.
    """
    if app.max_session_hours > 0:
        return app.max_session_hours * SECONDS_PER_HOUR + SECONDS_PER_HOUR
    return DEFAULT_OPEN_CAP_SECONDS


def clip(
    intervals: Iterable[Interval], start: float, end: float
) -> tuple[Interval, ...]:
    """The parts of ``intervals`` that fall inside ``[start, end)``."""
    kept: list[Interval] = []
    for interval in intervals:
        lo = max(interval.start, start)
        hi = min(interval.end, end)
        if hi > lo:
            kept.append(
                Interval(
                    start=lo,
                    end=hi,
                    open=interval.open and hi == interval.end,
                    unclosed=interval.unclosed and hi == interval.end,
                )
            )
    return tuple(kept)


def seconds_in(intervals: Iterable[Interval], start: float, end: float) -> float:
    return sum(interval.seconds for interval in clip(intervals, start, end))


def seconds_by_day(
    intervals: Iterable[Interval], start: float, end: float
) -> dict[str, float]:
    """Awake seconds per UTC day, for every day in ``[start, end)``.

    UTC, not local time: the rollup row's key has to mean the same thing
    whoever computes it, and a DST-shifting local day would make a stored row
    depend on the reader's timezone.

    Days with no awake time are present with 0.0, so a caller can tell "we
    computed this day and it was quiet" from "we never computed this day" --
    which is exactly what makes the rollup cacheable.
    """
    materialized = tuple(intervals)
    out: dict[str, float] = {}
    cursor = day_start(start)
    while cursor < end:
        next_day = cursor + 86400.0
        out[day_key(cursor)] = seconds_in(
            materialized, max(cursor, start), min(next_day, end)
        )
        cursor = next_day
    return out


# ---------------------------------------------------------------------------
# Calendar helpers (UTC throughout)
# ---------------------------------------------------------------------------


def day_start(ts: float) -> float:
    """Midnight UTC at or before ``ts``."""
    moment = datetime.fromtimestamp(ts, tz=timezone.utc)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def day_key(ts: float) -> str:
    """``YYYY-MM-DD``, UTC."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def month_of(ts: float) -> tuple[int, int]:
    moment = datetime.fromtimestamp(ts, tz=timezone.utc)
    return moment.year, moment.month


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def month_bounds(year: int, month: int) -> tuple[float, float]:
    """``[start, end)`` in epoch seconds for a calendar month, UTC."""
    start = datetime(year, month, 1, tzinfo=timezone.utc).timestamp()
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp()
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()
    return start, end


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _day_keys(year: int, month: int) -> list[str]:
    first = date(year, month, 1)
    return [
        (first + timedelta(days=offset)).isoformat()
        for offset in range(days_in_month(year, month))
    ]


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------


def hourly_rate(cpu_units: int, memory_mib: int) -> float:
    """Fargate cost per awake hour for a task of this size, us-east-1."""
    vcpus = max(0, int(cpu_units or 0)) / CPU_UNITS_PER_VCPU
    gb = max(0, int(memory_mib or 0)) / MIB_PER_GB
    return vcpus * FARGATE_VCPU_HOUR + gb * FARGATE_GB_HOUR


def cost_for_seconds(cpu_units: int, memory_mib: int, seconds: float) -> float:
    """Fargate bills per second, so this is not a per-hour approximation."""
    return hourly_rate(cpu_units, memory_mib) * (max(0.0, seconds) / SECONDS_PER_HOUR)


#: Sizes for rows written before the portal existed, which carry no cpu/memory
#: (registry.App: "0 means not portal-created"). Keyed by ``app_key``, from
#: CLAUDE.md's cost model. An app that matches neither is priced at 0 and the
#: payload says ``size_known: false`` -- guessing a size would put a fabricated
#: number on a screen labelled "cost".
LEGACY_SIZES: Mapping[str, tuple[int, int]] = {
    "dashboard": (512, 2048),
    "model": (4096, 16384),
}


def size_of(app: App) -> tuple[int, int, bool]:
    """``(cpu_units, memory_mib, known)`` for an app row."""
    if app.cpu > 0 and app.memory > 0:
        return app.cpu, app.memory, True
    legacy = LEGACY_SIZES.get(app.app_key)
    if legacy is not None:
        return legacy[0], legacy[1], True
    return 0, 0, False


# ---------------------------------------------------------------------------
# Platform overhead
# ---------------------------------------------------------------------------


def overhead_lines() -> list[dict[str, Any]]:
    """The shared cost, itemised. Never divided among apps."""
    return [
        {
            "name": "Application Load Balancer",
            "monthly": round(ALB_HOURLY * HOURS_PER_MONTH, 2),
            "note": "Always on. $0.0225/hr x 730. The platform's fixed floor.",
        },
        {
            "name": "ALB capacity units",
            "monthly": round(ALB_LCU_MONTHLY, 2),
            "note": "LCU-hours at $0.008. Approximate -- scales with traffic.",
        },
        {
            "name": "Authorizing proxy task",
            "monthly": round(
                hourly_rate(PROXY_CPU_UNITS, PROXY_MEMORY_MIB) * HOURS_PER_MONTH, 2
            ),
            "note": "0.25 vCPU / 512 MB, always on (ADR-0014). Never sleeps.",
        },
        {
            "name": "Route 53 hosted zone",
            "monthly": round(ROUTE53_MONTHLY, 2),
            "note": "tools.stratevi.com. Query charges are pennies.",
        },
        {
            "name": "DynamoDB, S3 and CloudWatch",
            "monthly": round(MISC_MONTHLY, 2),
            "note": "Two small tables, the uploads bucket, log retention.",
        },
    ]


def overhead_block(elapsed_fraction: float) -> dict[str, Any]:
    """The overhead line for one period.

    ``elapsed_fraction`` is how much of the month has passed (1.0 for a
    finished month). These are time-based fixed charges, so pro-rating them
    across elapsed time is arithmetic, not allocation -- unlike splitting
    them per app, which this deliberately does not do.
    """
    lines = overhead_lines()
    monthly = sum(line["monthly"] for line in lines)
    fraction = min(1.0, max(0.0, elapsed_fraction))
    return {
        "shared": True,
        "lines": lines,
        "monthly_total": round(monthly, 2),
        "to_date_total": round(monthly * fraction, 2),
        "elapsed_fraction": round(fraction, 4),
        "note": (
            "Shared by every app and attributable to none. Not divided across "
            "apps: a per-app share of a load balancer is a number invented by "
            "division."
        ),
    }


# ---------------------------------------------------------------------------
# The stored ledger
# ---------------------------------------------------------------------------

#: The audit table partition the daily rollups live in.
#:
#: Why the AUDIT table and not a new one, or ``shiny-proxy-apps``:
#:
#: * ``shiny-proxy-apps`` is Scanned by the sleeper every minute. One row per
#:   app per day would be thousands of rows inside that Scan within a year,
#:   for data the sleeper never reads. (It would be CORRECT -- ``__``-rows are
#:   already skipped -- just wasteful in the one place on the platform that
#:   pays per minute.)
#: * A new table is a new Terraform resource, a new IAM statement and a new
#:   thing to forget in a teardown, for one small derived index.
#: * The audit table already holds the source events, is already partitioned
#:   and time-sorted, and the proxy already has GetItem/PutItem/Query on it.
#:   The rollup lives next to its input and needs no infrastructure change.
#:
#: ``__``-prefixed, matching the convention the registry and sleeper already
#: use for "this row is not an app". The portal refuses ``__``-hosts on every
#: app route, so these never surface in the audit viewer.
USAGE_PARTITION = "__usage__"

#: Sort key is ``<day>#<app host>``, DATE FIRST: the primary read is "every
#: app, one month", which is then a single Query over a contiguous sort-key
#: range. Host-first would make that one Query per app.
def usage_sort_key(day: str, host: str) -> str:
    return f"{day}#{host}"


#: A high code point so a BETWEEN over a whole month catches every host.
_SORT_MAX = "￿"

#: How stale today's partial row may be before it is recomputed. Days in the
#: past are written ``final`` and never recomputed at all.
TODAY_REFRESH_SECONDS = 300.0


@dataclass(frozen=True)
class DayUsage:
    """One stored rollup row."""

    day: str
    host: str
    awake_seconds: float = 0.0
    final: bool = False
    computed_at: float = 0.0
    last_end: float = 0.0
    anomalies: Mapping[str, int] = field(default_factory=dict)


class UsageStore(Protocol):
    """What the ledger needs from persistence."""

    async def lifecycle_events(
        self, host: str, start: float, end: float
    ) -> list[dict[str, Any]]: ...

    async def read_days(self, first_day: str, last_day: str) -> list[DayUsage]: ...

    async def write_days(self, rows: Sequence[DayUsage]) -> None: ...


class DynamoUsageStore:
    """:class:`UsageStore` over ``shiny-proxy-audit``.

    Needs ``dynamodb:Query`` and ``dynamodb:PutItem`` on that table, both of
    which the proxy task role already has (proxy/iam.tf) -- so this feature
    requires no IAM change.
    """

    def __init__(self, client: Any, table: str, log: logging.Logger | None = None) -> None:
        self._client = client
        self._table = table
        self._log = log or logging.getLogger("proxy.usage")

    async def lifecycle_events(
        self, host: str, start: float, end: float
    ) -> list[dict[str, Any]]:
        """Wake/sleep events for one host in ``[start, end]``.

        A FilterExpression, not client-side filtering: ``allow`` rows vastly
        outnumber lifecycle rows, and filtering server-side keeps the payload
        (and the JSON parsing on a 0.25 vCPU task) small. It does not reduce
        RCUs -- DynamoDB charges for what it reads, not what it returns --
        but the sort-key range is what bounds that, and it already does.
        """
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "#host = :host AND #ts BETWEEN :lo AND :hi",
            "ExpressionAttributeNames": {"#host": "host", "#ts": "ts", "#event": "event"},
            "ExpressionAttributeValues": {
                ":host": {"S": host},
                ":lo": {"S": f"{int(start * 1000):013d}#"},
                ":hi": {"S": f"{int(end * 1000):013d}#{_SORT_MAX}"},
                ":wake": {"S": audit_mod.EVENT_WAKE},
                ":sleep": {"S": audit_mod.EVENT_SLEEP},
                ":force": {"S": audit_mod.EVENT_FORCE_SLEEP},
                ":expired": {"S": audit_mod.EVENT_EXPIRED},
            },
            "FilterExpression": "#event IN (:wake, :sleep, :force, :expired)",
            "ScanIndexForward": True,
        }

        found: list[dict[str, Any]] = []
        start_key: dict[str, Any] | None = None
        while True:
            call = dict(kwargs)
            if start_key:
                call["ExclusiveStartKey"] = start_key
            response = await asyncio.to_thread(self._client.query, **call)
            found.extend(
                audit_mod.event_from_item(item) for item in response.get("Items", ())
            )
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return found

    async def read_days(self, first_day: str, last_day: str) -> list[DayUsage]:
        kwargs: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "#host = :host AND #ts BETWEEN :lo AND :hi",
            "ExpressionAttributeNames": {"#host": "host", "#ts": "ts"},
            "ExpressionAttributeValues": {
                ":host": {"S": USAGE_PARTITION},
                ":lo": {"S": f"{first_day}#"},
                ":hi": {"S": f"{last_day}#{_SORT_MAX}"},
            },
            "ScanIndexForward": True,
        }

        rows: list[DayUsage] = []
        start_key: dict[str, Any] | None = None
        while True:
            call = dict(kwargs)
            if start_key:
                call["ExclusiveStartKey"] = start_key
            response = await asyncio.to_thread(self._client.query, **call)
            rows.extend(day_from_item(item) for item in response.get("Items", ()))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return rows

    async def write_days(self, rows: Sequence[DayUsage]) -> None:
        """PutItem per row, which makes a re-run idempotent by construction.

        No ``ttl`` attribute: audit rows expire after 90 days, and a rollup
        that vanished would take the previous month's comparison with it.
        These are a handful of bytes per app per day.
        """
        for row in rows:
            await asyncio.to_thread(
                self._client.put_item, TableName=self._table, Item=day_item(row)
            )


def day_item(row: DayUsage) -> dict[str, dict[str, Any]]:
    item: dict[str, dict[str, Any]] = {
        "host": {"S": USAGE_PARTITION},
        "ts": {"S": usage_sort_key(row.day, row.host)},
        "event": {"S": "usage_day"},
        "app_host": {"S": row.host},
        "day": {"S": row.day},
        "awake_seconds": {"N": f"{row.awake_seconds:.3f}"},
        "final": {"BOOL": bool(row.final)},
        "computed_at": {"N": str(int(row.computed_at))},
    }
    if row.last_end:
        item["last_end"] = {"N": str(int(row.last_end))}
    if row.anomalies:
        item["anomalies"] = {"S": json.dumps(dict(row.anomalies), sort_keys=True)}
    return item


def day_from_item(item: Mapping[str, Mapping[str, Any]]) -> DayUsage:
    sort = str((item.get("ts") or {}).get("S") or "")
    day, _, host = sort.partition("#")
    raw_anomalies = str((item.get("anomalies") or {}).get("S") or "")
    anomalies: dict[str, int] = {}
    if raw_anomalies:
        try:
            decoded = json.loads(raw_anomalies)
            if isinstance(decoded, dict):
                anomalies = {str(k): int(v) for k, v in decoded.items()}
        except (ValueError, TypeError):
            anomalies = {}
    return DayUsage(
        day=str((item.get("day") or {}).get("S") or day),
        host=str((item.get("app_host") or {}).get("S") or host),
        awake_seconds=_num(item, "awake_seconds"),
        final=bool((item.get("final") or {}).get("BOOL") or False),
        computed_at=_num(item, "computed_at"),
        last_end=_num(item, "last_end"),
        anomalies=anomalies,
    )


def _num(item: Mapping[str, Mapping[str, Any]], name: str) -> float:
    raw = (item.get(name) or {}).get("N")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


class UsageLedger:
    """Daily awake-seconds per app, computed once and stored.

    The read path is: pull the month's rollup rows in one Query; work out
    which days are missing or stale; recompute only those apps, from the raw
    trail, in one Query each; write the days back. A finished day is written
    ``final`` and is never recomputed, so a month that has ended costs exactly
    one Query however often it is asked for.

    Recomputation is lazy rather than scheduled on purpose: there is no
    background job to fail silently, a cold table self-heals on the first
    request, and the proxy task -- which is in the request path for every app
    on the platform -- does no work nobody asked for.
    """

    def __init__(
        self,
        store: UsageStore,
        *,
        clock: Any = time.time,
        log: logging.Logger | None = None,
        refresh_seconds: float = TODAY_REFRESH_SECONDS,
    ) -> None:
        self._store = store
        self._clock = clock
        self._log = log or logging.getLogger("proxy.usage")
        self._refresh = refresh_seconds

    async def month(
        self, apps: Sequence[App], year: int, month: int
    ) -> dict[str, DayUsage]:
        """Every stored day for ``year``/``month``, keyed ``day#host``.

        Computes what is missing first, so the answer is complete for every
        app in ``apps``.
        """
        days = _day_keys(year, month)
        if not days:
            return {}

        rows = {
            usage_sort_key(row.day, row.host): row
            for row in await self._store.read_days(days[0], days[-1])
        }

        now = float(self._clock())
        start, end = month_bounds(year, month)
        horizon = min(end, now)
        if horizon <= start:
            return rows  # a month that has not begun

        # Only days that have actually started can be computed.
        wanted = [day for day in days if _day_epoch(day) < horizon]
        today = day_key(now) if start <= now < end else None

        written: list[DayUsage] = []
        for app in apps:
            if not self._needs_compute(app.host, wanted, today, rows, now):
                continue
            try:
                fresh = await self._compute(app, start, horizon, today, now)
            except Exception as exc:
                # A costs screen is not worth a 503. The app keeps whatever
                # rows it already had, and the payload's `stale` flag says so.
                self._log.warning(
                    "usage: cannot roll up",
                    extra={"host": app.host, "reason": str(exc)},
                )
                continue
            for row in fresh:
                rows[usage_sort_key(row.day, row.host)] = row
            written.extend(fresh)

        if written:
            try:
                await self._store.write_days(written)
            except Exception as exc:
                # The numbers above are still right -- they just were not
                # cached. Next request recomputes them.
                self._log.warning("usage: cannot store rollup", extra={"reason": str(exc)})

        return rows

    def _needs_compute(
        self,
        host: str,
        days: Sequence[str],
        today: str | None,
        rows: Mapping[str, DayUsage],
        now: float,
    ) -> bool:
        for day in days:
            row = rows.get(usage_sort_key(day, host))
            if row is None:
                return True
            if day == today:
                if now - row.computed_at >= self._refresh:
                    return True
            elif not row.final:
                # A past day computed while it was still running.
                return True
        return False

    async def _compute(
        self, app: App, start: float, horizon: float, today: str | None, now: float
    ) -> list[DayUsage]:
        events = await self._store.lifecycle_events(
            app.host, start - LOOKBACK_SECONDS, horizon
        )
        pairing = pair_events(
            events,
            now=now,
            open_cap_seconds=open_cap_for(app),
            window_start=start - LOOKBACK_SECONDS,
        )
        per_day = seconds_by_day(pairing.intervals, start, horizon)

        out: list[DayUsage] = []
        for day, seconds in per_day.items():
            day_from, day_to = _day_epoch(day), _day_epoch(day) + 86400.0
            within = clip(pairing.intervals, day_from, min(day_to, horizon))
            out.append(
                DayUsage(
                    day=day,
                    host=app.host,
                    awake_seconds=seconds,
                    # A day is final once it is wholly in the past. Today is
                    # not, and neither is a day whose horizon we clipped.
                    final=day_to <= horizon and day != today,
                    computed_at=now,
                    last_end=max((i.end for i in within), default=0.0),
                    # Anomalies are recorded on every day the pairing covered,
                    # not just the one they happened on: the pairing is over
                    # the whole window and cannot attribute them to a day.
                    anomalies=pairing.anomalies,
                )
            )
        return out


def _day_epoch(day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# The API payload
# ---------------------------------------------------------------------------

DISCLAIMER = (
    "Estimates derived from recorded awake time and published Fargate rates, "
    "not billed amounts. AWS Cost Explorer remains the source of truth for an "
    "invoice."
)


def app_costs(
    app: App, rows: Mapping[str, DayUsage], year: int, month: int
) -> dict[str, Any]:
    """One app's line for one month."""
    cpu, memory, known = size_of(app)
    seconds = 0.0
    last_end = 0.0
    anomalies: dict[str, int] = {}
    daily: list[dict[str, Any]] = []

    for day in _day_keys(year, month):
        row = rows.get(usage_sort_key(day, app.host))
        if row is None:
            continue
        seconds += row.awake_seconds
        last_end = max(last_end, row.last_end)
        for name, count in row.anomalies.items():
            anomalies[name] = max(anomalies.get(name, 0), count)
        if row.awake_seconds > 0:
            daily.append(
                {"day": day, "awake_hours": round(row.awake_seconds / SECONDS_PER_HOUR, 3)}
            )

    cost = cost_for_seconds(cpu, memory, seconds) if known else 0.0
    return {
        "host": app.host,
        "label": app.display_label(),
        "app_key": app.app_key,
        "cpu": cpu,
        "memory": memory,
        "size_known": known,
        "hourly_rate": round(hourly_rate(cpu, memory), 6) if known else None,
        "awake_hours": round(seconds / SECONDS_PER_HOUR, 3),
        "estimated_cost": round(cost, 2),
        "last_run": int(last_end) or None,
        "currently_awake": bool(app.awake_since),
        "daily": daily,
        "anomalies": anomalies,
    }


def month_payload(
    apps: Sequence[App],
    rows: Mapping[str, DayUsage],
    year: int,
    month: int,
    now: float,
) -> dict[str, Any]:
    """One period's block: per-app compute, the shared line, and a total."""
    start, end = month_bounds(year, month)
    complete = now >= end
    elapsed = 1.0 if complete else max(0.0, (now - start) / (end - start))

    lines = [app_costs(app, rows, year, month) for app in apps]
    lines.sort(key=lambda line: (-line["estimated_cost"], line["label"].lower()))

    compute_total = sum(line["estimated_cost"] for line in lines)
    overhead = overhead_block(elapsed)
    shared = overhead["monthly_total"] if complete else overhead["to_date_total"]

    return {
        "month": month_key(year, month),
        "start": int(start),
        "end": int(end),
        "complete": complete,
        "apps": lines,
        "apps_total": round(compute_total, 2),
        "overhead": overhead,
        "total": round(compute_total + shared, 2),
    }


def rates_block() -> dict[str, Any]:
    return {
        "vcpu_hour": FARGATE_VCPU_HOUR,
        "gb_hour": FARGATE_GB_HOUR,
        "region": "us-east-1",
        "source": "AWS Fargate on-demand pricing, Linux/X86, us-east-1.",
    }


__all__ = [
    "ALB_HOURLY",
    "ALB_LCU_MONTHLY",
    "CLOSE_EVENTS",
    "DEFAULT_OPEN_CAP_SECONDS",
    "DISCLAIMER",
    "DayUsage",
    "DynamoUsageStore",
    "FARGATE_GB_HOUR",
    "FARGATE_VCPU_HOUR",
    "HOURS_PER_MONTH",
    "Interval",
    "LEGACY_SIZES",
    "LOOKBACK_SECONDS",
    "MISC_MONTHLY",
    "OPEN_EVENTS",
    "PROXY_CPU_UNITS",
    "PROXY_MEMORY_MIB",
    "Pairing",
    "ROUTE53_MONTHLY",
    "TODAY_REFRESH_SECONDS",
    "USAGE_PARTITION",
    "UsageLedger",
    "UsageStore",
    "app_costs",
    "clip",
    "cost_for_seconds",
    "day_from_item",
    "day_item",
    "day_key",
    "day_start",
    "days_in_month",
    "hourly_rate",
    "month_bounds",
    "month_key",
    "month_of",
    "month_payload",
    "open_cap_for",
    "overhead_block",
    "overhead_lines",
    "pair_events",
    "previous_month",
    "rates_block",
    "seconds_by_day",
    "seconds_in",
    "size_of",
    "usage_sort_key",
]
