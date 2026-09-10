"""The sleeper/reaper pass: idle scale-to-zero and expiry enforcement."""

from __future__ import annotations

import pytest

from proxy_app import audit, registry, sleeper
from proxy_app.ecsctl import ServiceState
from proxy_app.registry import App

NOW = 1_700_000_000.0


class FakeStore:
    def __init__(self, rows: list[App]) -> None:
        self.rows = rows
        self.statuses: list[tuple[str, str]] = []
        self.awake_since_writes: list[tuple[str, int]] = []
        self.fail_status = False
        self.fail_awake_since = False

    async def app(self, host: str) -> App | None:
        return next((r for r in self.rows if r.host == host), None)

    async def apps(self) -> list[App]:
        return list(self.rows)

    async def set_last_active(self, host: str, ts: int) -> None:
        pass

    async def set_awake_since(self, host: str, ts: int) -> None:
        if self.fail_awake_since:
            raise RuntimeError("dynamodb is unhappy")
        self.awake_since_writes.append((host, ts))
        # Mirror the write into the row, the way a real table would answer the
        # next Scan -- several tests below tick() more than once.
        for i, r in enumerate(self.rows):
            if r.host == host:
                self.rows[i] = App.create(**{**r.__dict__, "awake_since": ts})

    async def set_status(self, host: str, status: str) -> None:
        if self.fail_status:
            raise RuntimeError("dynamodb is unhappy")
        self.statuses.append((host, status))

    async def ping(self) -> None:
        pass


class FakeScaler:
    def __init__(self, states: dict[str, ServiceState] | None = None) -> None:
        self.states = states or {}
        self.slept: list[str] = []

    async def state(self, service: str) -> ServiceState:
        return self.states.get(service, ServiceState(exists=True, desired=1, running=1))

    async def sleep(self, service: str) -> None:
        self.slept.append(service)


class FakeActivity:
    def __init__(self, *, boot: float = 0.0, last: dict[str, float] | None = None,
                 sockets: dict[str, int] | None = None) -> None:
        self._boot = boot
        self._last = last or {}
        self._sockets = sockets or {}

    @property
    def boot(self) -> float:
        return self._boot

    def last_active(self, host: str) -> float:
        return self._last.get(host, 0.0)

    def sockets(self, host: str) -> int:
        return self._sockets.get(host, 0)


class FakeRecorder:
    def __init__(self) -> None:
        self.events: list[audit.Event] = []

    def record(self, event: audit.Event) -> None:
        self.events.append(event)


def loop_for(store, scaler, activity, recorder=None) -> tuple[sleeper.Loop, FakeRecorder]:
    recorder = recorder or FakeRecorder()
    return (
        sleeper.Loop(store, scaler, activity, recorder, clock=lambda: NOW),
        recorder,
    )


def row(**overrides) -> App:
    defaults = dict(host="model.tools.stratevi.com", app_key="model", ecs_service="shiny-model")
    defaults.update(overrides)
    return App.create(**defaults)


# --- sleeping --------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_idle_app_is_scaled_to_zero_and_audited():
    store = FakeStore([row(idle_minutes=15)])
    scaler = FakeScaler()
    loop, recorder = loop_for(store, scaler, FakeActivity(last={"model.tools.stratevi.com": NOW - 3600}))

    await loop.tick()

    assert scaler.slept == ["shiny-model"]
    assert [e.event for e in recorder.events] == [audit.EVENT_SLEEP]


@pytest.mark.asyncio
async def test_an_app_used_inside_the_idle_window_is_left_alone():
    store = FakeStore([row(idle_minutes=15)])
    scaler = FakeScaler()
    loop, recorder = loop_for(store, scaler, FakeActivity(last={"model.tools.stratevi.com": NOW - 60}))

    await loop.tick()

    assert scaler.slept == []
    assert recorder.events == []


@pytest.mark.asyncio
async def test_an_open_websocket_keeps_an_app_awake_however_long_it_has_been_quiet():
    """The whole point of retiring the ADR-0006 heartbeat."""
    store = FakeStore([row(idle_minutes=15)])
    scaler = FakeScaler()
    activity = FakeActivity(
        last={"model.tools.stratevi.com": NOW - 86400},
        sockets={"model.tools.stratevi.com": 1},
    )
    loop, _ = loop_for(store, scaler, activity)

    await loop.tick()

    assert scaler.slept == []


@pytest.mark.asyncio
async def test_boot_time_counts_as_activity_so_a_restart_does_not_insta_sleep():
    store = FakeStore([row(idle_minutes=15)])
    scaler = FakeScaler()
    # Nothing seen in memory, nothing persisted, but the proxy booted a
    # minute ago -- a busy app gets a full idle window to prove itself.
    loop, _ = loop_for(store, scaler, FakeActivity(boot=NOW - 60))

    await loop.tick()

    assert scaler.slept == []


@pytest.mark.asyncio
async def test_last_active_persisted_by_the_other_proxy_task_is_respected():
    store = FakeStore([row(idle_minutes=15, last_active=int(NOW - 60))])
    scaler = FakeScaler()
    loop, _ = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert scaler.slept == []


@pytest.mark.asyncio
async def test_an_already_sleeping_service_is_not_slept_again():
    store = FakeStore([row()])
    scaler = FakeScaler({"shiny-model": ServiceState(exists=True, desired=0)})
    loop, recorder = loop_for(store, scaler, FakeActivity(last={"model.tools.stratevi.com": 0.0}))

    await loop.tick()

    assert scaler.slept == []
    assert recorder.events == []


@pytest.mark.asyncio
async def test_a_missing_service_is_not_scaled():
    store = FakeStore([row()])
    scaler = FakeScaler({"shiny-model": ServiceState(exists=False)})
    loop, _ = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert scaler.slept == []


# --- force-sleep cap (C1) ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_running_app_past_its_session_cap_is_force_slept_even_with_open_sockets():
    """The whole point of C1: the cap ignores activity and open sockets."""
    store = FakeStore([row(max_session_hours=1, awake_since=int(NOW - 3700))])
    scaler = FakeScaler()
    activity = FakeActivity(
        last={"model.tools.stratevi.com": NOW - 5},  # just used
        sockets={"model.tools.stratevi.com": 1},  # and a socket is open
    )
    loop, recorder = loop_for(store, scaler, activity)

    await loop.tick()

    assert scaler.slept == ["shiny-model"]
    assert [e.event for e in recorder.events] == [audit.EVENT_FORCE_SLEEP]
    assert store.awake_since_writes[-1] == ("model.tools.stratevi.com", 0)


@pytest.mark.asyncio
async def test_an_app_within_its_session_cap_is_left_alone():
    store = FakeStore([row(max_session_hours=2, awake_since=int(NOW - 3600))])
    scaler = FakeScaler()
    loop, recorder = loop_for(
        store, scaler, FakeActivity(last={"model.tools.stratevi.com": NOW - 5})
    )

    await loop.tick()

    assert scaler.slept == []
    assert recorder.events == []


@pytest.mark.asyncio
async def test_an_app_with_no_cap_is_never_force_slept_however_long_it_has_been_awake():
    store = FakeStore([row(max_session_hours=0, awake_since=int(NOW - 10 * 86400))])
    scaler = FakeScaler()
    activity = FakeActivity(
        last={"model.tools.stratevi.com": NOW - 5},
        sockets={"model.tools.stratevi.com": 1},
    )
    loop, recorder = loop_for(store, scaler, activity)

    await loop.tick()

    assert scaler.slept == []
    assert recorder.events == []


@pytest.mark.asyncio
async def test_a_restarted_or_externally_woken_service_gets_awake_since_set_not_slept():
    """No awake_since on the row, but the service is running: conservative --
    start counting from now instead of guessing at unknown history, and do
    not force-sleep on the very tick that starts tracking."""
    store = FakeStore([row(max_session_hours=1)])  # awake_since defaults to 0
    scaler = FakeScaler()
    loop, recorder = loop_for(
        store, scaler, FakeActivity(last={"model.tools.stratevi.com": NOW - 5})
    )

    await loop.tick()

    assert scaler.slept == []
    assert recorder.events == []
    assert store.awake_since_writes == [("model.tools.stratevi.com", int(NOW))]


@pytest.mark.asyncio
async def test_awake_since_is_cleared_once_the_service_is_observed_asleep():
    store = FakeStore([row(awake_since=int(NOW - 100))])
    scaler = FakeScaler({"shiny-model": ServiceState(exists=True, desired=0)})
    loop, _ = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert store.awake_since_writes == [("model.tools.stratevi.com", 0)]
    assert scaler.slept == []


@pytest.mark.asyncio
async def test_awake_since_is_also_cleared_for_a_service_that_no_longer_exists():
    """A deleted service is "observed at 0" too -- nothing running means
    nothing to force-sleep or idle-sleep, and a stale awake_since is cleared."""
    store = FakeStore([row(awake_since=int(NOW - 100))])
    scaler = FakeScaler({"shiny-model": ServiceState(exists=False)})
    loop, _ = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert store.awake_since_writes == [("model.tools.stratevi.com", 0)]


@pytest.mark.asyncio
async def test_awake_since_is_cleared_when_the_idle_sleeper_scales_a_service_to_zero():
    store = FakeStore([row(idle_minutes=15, awake_since=int(NOW - 3600))])
    scaler = FakeScaler()
    loop, recorder = loop_for(
        store, scaler, FakeActivity(last={"model.tools.stratevi.com": NOW - 3600})
    )

    await loop.tick()

    assert scaler.slept == ["shiny-model"]
    assert [e.event for e in recorder.events] == [audit.EVENT_SLEEP]
    assert store.awake_since_writes == [("model.tools.stratevi.com", 0)]


@pytest.mark.asyncio
async def test_a_failing_awake_since_write_does_not_stop_the_pass():
    store = FakeStore([row(idle_minutes=15)])
    store.fail_awake_since = True
    scaler = FakeScaler()
    loop, recorder = loop_for(
        store, scaler, FakeActivity(last={"model.tools.stratevi.com": NOW - 3600})
    )

    await loop.tick()  # must not raise

    assert scaler.slept == ["shiny-model"]
    assert [e.event for e in recorder.events] == [audit.EVENT_SLEEP]


# --- expiry ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lapsed_app_is_marked_expired_scaled_to_zero_and_audited():
    store = FakeStore([row(expires_at=int(NOW - 1))])
    scaler = FakeScaler()
    loop, recorder = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert store.statuses == [("model.tools.stratevi.com", registry.STATUS_EXPIRED)]
    assert scaler.slept == ["shiny-model"]
    assert [e.event for e in recorder.events] == [audit.EVENT_EXPIRED]


@pytest.mark.asyncio
async def test_expiry_still_scales_to_zero_when_the_status_write_fails():
    """Scaling is the part that costs money; the gate already refuses on the clock."""
    store = FakeStore([row(expires_at=int(NOW - 1))])
    store.fail_status = True
    scaler = FakeScaler()
    loop, recorder = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert scaler.slept == ["shiny-model"]
    assert [e.event for e in recorder.events] == [audit.EVENT_EXPIRED]


@pytest.mark.asyncio
async def test_an_already_expired_app_is_not_re_expired_or_slept():
    store = FakeStore([row(status=registry.STATUS_EXPIRED, expires_at=int(NOW - 1))])
    scaler = FakeScaler()
    loop, recorder = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert store.statuses == []
    assert scaler.slept == []
    assert recorder.events == []


@pytest.mark.asyncio
async def test_a_disabled_app_is_left_to_the_portal_not_slept_by_the_reaper():
    store = FakeStore([row(status=registry.STATUS_DISABLED)])
    scaler = FakeScaler()
    loop, recorder = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert scaler.slept == []
    assert recorder.events == []


# --- resilience ------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_bad_row_does_not_stop_the_pass():
    class Explodes(FakeScaler):
        async def state(self, service: str) -> ServiceState:
            if service == "shiny-broken":
                raise RuntimeError("ecs is unhappy")
            return await super().state(service)

    store = FakeStore(
        [
            row(host="broken.tools.stratevi.com", app_key="broken", ecs_service="shiny-broken"),
            row(),
        ]
    )
    scaler = Explodes()
    loop, _ = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert scaler.slept == ["shiny-model"]


@pytest.mark.asyncio
async def test_an_unreadable_table_ends_the_pass_quietly():
    class Broken(FakeStore):
        async def apps(self):
            raise RuntimeError("dynamodb is unhappy")

    scaler = FakeScaler()
    loop, _ = loop_for(Broken([]), scaler, FakeActivity())

    await loop.tick()  # must not raise

    assert scaler.slept == []


@pytest.mark.asyncio
async def test_a_config_row_is_never_treated_as_an_app():
    """`__config__` holds the portal's admin list, not an app. Reaching ECS
    with its (empty) ecs_service once a minute forever is the bug this
    prevents -- see registry.CONFIG_PREFIX."""
    store = FakeStore([App.create(host=registry.CONFIG_HOST), row(idle_minutes=15)])
    scaler = FakeScaler()
    loop, recorder = loop_for(
        store, scaler, FakeActivity(last={"model.tools.stratevi.com": NOW - 3600})
    )

    await loop.tick()

    assert scaler.slept == ["shiny-model"]
    assert all(host != registry.CONFIG_HOST for host, _ in store.awake_since_writes)
    assert all(event.host != registry.CONFIG_HOST for event in recorder.events)


@pytest.mark.asyncio
async def test_a_config_row_with_an_expiry_is_not_expired_either():
    """Belt and braces: the skip happens before the reaper, not after it."""
    store = FakeStore([App.create(host=registry.CONFIG_HOST, expires_at=int(NOW - 1))])
    scaler = FakeScaler()
    loop, recorder = loop_for(store, scaler, FakeActivity())

    await loop.tick()

    assert store.statuses == []
    assert scaler.slept == []
    assert recorder.events == []
