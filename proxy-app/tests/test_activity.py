"""Activity tracking: requests, open sockets, and the once-a-minute write."""

from __future__ import annotations

import asyncio

import pytest

from proxy_app import activity


class Clock:
    def __init__(self, t: float = 1_700_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeWriter:
    def __init__(self) -> None:
        self.writes: list[tuple[str, int]] = []
        self.fail = False

    async def set_last_active(self, host: str, ts: int) -> None:
        if self.fail:
            raise RuntimeError("dynamodb is unhappy")
        self.writes.append((host, ts))


async def settle() -> None:
    """Let the tracker's fire-and-forget persist tasks run."""
    for _ in range(3):
        await asyncio.sleep(0)


def test_touch_records_activity_without_a_running_loop():
    clock = Clock()
    tracker = activity.Tracker(None, clock=clock)
    tracker.touch("a.b")
    assert tracker.last_active("a.b") == clock.t
    assert tracker.last_active("never.seen") == 0.0


def test_boot_is_captured_at_construction():
    clock = Clock()
    tracker = activity.Tracker(None, clock=clock)
    clock.t += 5000
    assert tracker.boot == 1_700_000_000.0


@pytest.mark.asyncio
async def test_last_active_is_persisted_at_most_once_a_minute():
    clock = Clock()
    writer = FakeWriter()
    tracker = activity.Tracker(writer, clock=clock)

    for _ in range(50):  # a page load's worth of assets in the same second
        tracker.touch("a.b")
    await settle()
    assert writer.writes == [("a.b", int(clock.t))]

    clock.t += activity.PERSIST_EVERY - 1
    tracker.touch("a.b")
    await settle()
    assert len(writer.writes) == 1

    clock.t += 2
    tracker.touch("a.b")
    await settle()
    assert len(writer.writes) == 2


@pytest.mark.asyncio
async def test_a_failing_write_does_not_escape_into_the_request_path():
    writer = FakeWriter()
    writer.fail = True
    tracker = activity.Tracker(writer, clock=Clock())
    tracker.touch("a.b")  # must not raise
    await settle()
    assert writer.writes == []


@pytest.mark.asyncio
async def test_open_sockets_are_counted_for_the_life_of_the_block():
    clock = Clock()
    tracker = activity.Tracker(None, clock=clock)

    assert tracker.sockets("a.b") == 0
    with tracker.open_socket("a.b"):
        assert tracker.sockets("a.b") == 1
        with tracker.open_socket("a.b"):
            assert tracker.sockets("a.b") == 2
        assert tracker.sockets("a.b") == 1
    assert tracker.sockets("a.b") == 0


@pytest.mark.asyncio
async def test_closing_a_socket_counts_as_activity():
    clock = Clock()
    tracker = activity.Tracker(None, clock=clock)
    with tracker.open_socket("a.b"):
        clock.t += 3600  # an hour-long Shiny session
    assert tracker.last_active("a.b") == clock.t


@pytest.mark.asyncio
async def test_a_socket_that_raises_is_still_released():
    tracker = activity.Tracker(None, clock=Clock())
    with pytest.raises(ValueError):
        with tracker.open_socket("a.b"):
            raise ValueError("the browser vanished")
    assert tracker.sockets("a.b") == 0
