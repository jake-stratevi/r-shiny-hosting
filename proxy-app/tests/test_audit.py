"""The audit recorder: dedupe, the sort key, and never blocking a request."""

from __future__ import annotations

import asyncio

import pytest

from proxy_app import audit
from proxy_app.audit import Event


class Clock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeSink:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.fail = False

    async def put(self, event: Event) -> None:
        if self.fail:
            raise RuntimeError("dynamodb is unhappy")
        self.events.append(event)


def queued(recorder: audit.Recorder) -> list[Event]:
    """Everything currently sitting on the recorder's queue."""
    drained = []
    while not recorder._queue.empty():  # noqa: SLF001 - the queue is the assertion
        drained.append(recorder._queue.get_nowait())
    return drained


# --- the sort key ----------------------------------------------------------


def test_sort_key_is_thirteen_digits_then_hash_then_eight_hex():
    key = audit.sort_key(1_700_000_000.123)
    stamp, _, suffix = key.partition("#")
    assert len(stamp) == 13 and stamp.isdigit()
    assert stamp == "1700000000123"
    assert len(suffix) == 8
    int(suffix, 16)  # raises if it is not hex


def test_sort_keys_sort_chronologically_as_strings():
    early = audit.sort_key(1_700_000_000.0, suffix="ffffffff")
    late = audit.sort_key(1_700_000_001.0, suffix="00000000")
    assert early < late


def test_two_events_in_the_same_millisecond_get_different_keys():
    keys = {audit.sort_key(1_700_000_000.0) for _ in range(200)}
    assert len(keys) > 1


def test_dates_up_to_the_year_2286_stay_thirteen_digits():
    # Fixed width is what makes the lexicographic sort correct; a 14th digit
    # would break it, and 9999999999999 ms is 2286-11-20.
    assert len(audit.sort_key(9_999_999_999.0).split("#")[0]) == 13


# --- allow dedupe ----------------------------------------------------------


@pytest.mark.asyncio
async def test_allow_events_are_collapsed_per_host_and_email_for_ten_minutes():
    clock = Clock()
    recorder = audit.Recorder(None, clock=clock)

    for _ in range(40):  # one Shiny page load
        recorder.record(Event(host="a.b", event=audit.EVENT_ALLOW, email="jake@stratevi.com"))
    assert len(queued(recorder)) == 1

    clock.t += audit.ALLOW_WINDOW - 1
    recorder.record(Event(host="a.b", event=audit.EVENT_ALLOW, email="jake@stratevi.com"))
    assert queued(recorder) == []

    clock.t += 2
    recorder.record(Event(host="a.b", event=audit.EVENT_ALLOW, email="jake@stratevi.com"))
    assert len(queued(recorder)) == 1


@pytest.mark.asyncio
async def test_dedupe_is_per_host_and_per_email():
    recorder = audit.Recorder(None, clock=Clock())
    recorder.record(Event(host="a.b", event=audit.EVENT_ALLOW, email="jake@stratevi.com"))
    recorder.record(Event(host="a.b", event=audit.EVENT_ALLOW, email="nick@stratevi.com"))
    recorder.record(Event(host="c.d", event=audit.EVENT_ALLOW, email="jake@stratevi.com"))
    assert len(queued(recorder)) == 3


@pytest.mark.asyncio
async def test_no_email_principals_dedupe_as_one_bucket_per_host():
    recorder = audit.Recorder(None, clock=Clock())
    recorder.record(Event(host="a.b", event=audit.EVENT_ALLOW))
    recorder.record(Event(host="a.b", event=audit.EVENT_ALLOW))
    assert len(queued(recorder)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [audit.EVENT_DENY, audit.EVENT_WAKE, audit.EVENT_SLEEP, audit.EVENT_EXPIRED],
)
async def test_denials_wakes_sleeps_and_expiries_are_never_collapsed(kind):
    recorder = audit.Recorder(None, clock=Clock())
    for _ in range(5):
        recorder.record(Event(host="a.b", event=kind, email="jake@stratevi.com"))
    assert len(queued(recorder)) == 5


@pytest.mark.asyncio
async def test_the_dedupe_table_does_not_grow_without_bound():
    clock = Clock()
    recorder = audit.Recorder(None, clock=clock)
    for n in range(5000):
        recorder.record(Event(host=f"h{n}.b", event=audit.EVENT_ALLOW, email="a@b.com"))
        clock.t += 1  # every entry ages past the window as we go
    assert len(recorder._seen) <= 4097  # noqa: SLF001


# --- never blocking, never raising -----------------------------------------


@pytest.mark.asyncio
async def test_a_full_queue_drops_rather_than_blocks():
    recorder = audit.Recorder(None, queue_depth=4, clock=Clock())
    for n in range(50):
        recorder.record(Event(host=f"h{n}.b", event=audit.EVENT_DENY))
    assert recorder._queue.qsize() == 4  # noqa: SLF001


@pytest.mark.asyncio
async def test_events_reach_the_sink_and_a_failing_sink_is_swallowed():
    sink = FakeSink()
    recorder = audit.Recorder(sink, clock=Clock())
    recorder.start()

    recorder.record(Event(host="a.b", event=audit.EVENT_WAKE))
    await asyncio.wait_for(recorder._queue.join(), 2)  # noqa: SLF001
    assert [e.event for e in sink.events] == [audit.EVENT_WAKE]

    sink.fail = True
    recorder.record(Event(host="a.b", event=audit.EVENT_SLEEP))
    await asyncio.wait_for(recorder._queue.join(), 2)  # noqa: SLF001
    assert len(sink.events) == 1  # the failure did not raise, and did not retry

    await recorder.stop()


@pytest.mark.asyncio
async def test_an_event_with_no_timestamp_is_stamped_on_the_way_in():
    clock = Clock()
    recorder = audit.Recorder(None, clock=clock)
    recorder.record(Event(host="a.b", event=audit.EVENT_WAKE))
    assert queued(recorder)[0].at == clock.t


# --- the DynamoDB item shape -----------------------------------------------


class FakeDynamo:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def put_item(self, *, TableName: str, Item: dict) -> dict:  # noqa: N803 - boto3 casing
        self.items.append(Item)
        return {}


@pytest.mark.asyncio
async def test_the_audit_item_matches_the_terraform_table():
    client = FakeDynamo()
    await audit.DynamoAuditSink(client, "shiny-proxy-audit").put(
        Event(
            host="model.tools.stratevi.com",
            event=audit.EVENT_DENY,
            email="stranger@example.com",
            path="/",
            outcome="not_entitled",
            at=1_700_000_000.0,
        )
    )
    item = client.items[0]
    assert item["host"] == {"S": "model.tools.stratevi.com"}
    assert item["ts"]["S"].startswith("1700000000000#")
    assert item["event"] == {"S": audit.EVENT_DENY}
    assert item["ts_epoch"] == {"N": "1700000000"}
    assert item["ttl"] == {"N": str(1_700_000_000 + audit.RETENTION_SECONDS)}
    assert item["email"] == {"S": "stranger@example.com"}
    assert item["outcome"] == {"S": "not_entitled"}


@pytest.mark.asyncio
async def test_optional_attributes_are_omitted_when_empty():
    client = FakeDynamo()
    await audit.DynamoAuditSink(client, "t").put(
        Event(host="a.b", event=audit.EVENT_SLEEP, at=1.0)
    )
    assert set(client.items[0]) == {"host", "ts", "event", "ts_epoch", "ttl"}
