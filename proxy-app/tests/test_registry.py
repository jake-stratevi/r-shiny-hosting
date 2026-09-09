"""Host normalization, row defaults, item encoding, and the read cache."""

from __future__ import annotations

import pytest

from proxy_app import registry
from proxy_app.registry import App

# --- host normalization ----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("model.tools.stratevi.com", "model.tools.stratevi.com"),
        ("MODEL.Tools.Stratevi.COM", "model.tools.stratevi.com"),
        ("model.tools.stratevi.com:443", "model.tools.stratevi.com"),
        ("model.tools.stratevi.com.", "model.tools.stratevi.com"),
        ("  model.tools.stratevi.com  ", "model.tools.stratevi.com"),
        ("MODEL.tools.stratevi.com.:8080", "model.tools.stratevi.com"),
        ("localhost:8080", "localhost"),
        ("[::1]:8080", "::1"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_host(raw, expected):
    assert registry.normalize_host(raw) == expected


def test_all_the_ways_one_app_arrives_are_one_partition_key():
    variants = [
        "model.tools.stratevi.com",
        "Model.Tools.Stratevi.com",
        "model.tools.stratevi.com:443",
        "model.tools.stratevi.com.",
    ]
    assert len({registry.normalize_host(v) for v in variants}) == 1


# --- row defaults ----------------------------------------------------------


def test_defaults_are_applied_and_comparisons_normalized():
    row = App.create(
        host="MODEL.tools.stratevi.com:443",
        app_key="model",
        ecs_service="shiny-model",
        allowed_emails=[" JAKE@Stratevi.com ", "", "nick@stratevi.com"],
    )
    assert row.host == "model.tools.stratevi.com"
    assert row.container_port == registry.DEFAULT_CONTAINER_PORT
    assert row.idle_minutes == registry.DEFAULT_IDLE_MINUTES
    assert row.status == registry.STATUS_ACTIVE
    assert row.access_mode == registry.MODE_USERS
    assert row.allowed_emails == ("jake@stratevi.com", "nick@stratevi.com")


def test_status_and_mode_are_lowercased():
    row = App.create(host="a.b", status="EXPIRED", access_mode="All_Users")
    assert row.status == registry.STATUS_EXPIRED
    assert row.access_mode == registry.MODE_ALL_USERS


def test_is_expired_uses_the_clock_not_the_status():
    row = App.create(host="a.b", expires_at=100)
    assert not row.is_expired(99)
    assert row.is_expired(100)  # inclusive: at the second, it is over
    assert row.is_expired(101)
    assert not App.create(host="a.b", expires_at=0).is_expired(1e12)


def test_idle_after_seconds():
    assert App.create(host="a.b").idle_after_seconds() == 15 * 60
    assert App.create(host="a.b", idle_minutes=45).idle_after_seconds() == 45 * 60


# --- the C1 session cap ------------------------------------------------


def test_max_session_hours_absent_or_zero_means_uncapped():
    assert App.create(host="a.b").max_session_hours == registry.DEFAULT_MAX_SESSION_HOURS
    assert App.create(host="a.b").has_session_cap() is False
    assert App.create(host="a.b", max_session_hours=0).has_session_cap() is False


def test_max_session_hours_set_gives_a_cap_in_seconds():
    row = App.create(host="a.b", max_session_hours=4)
    assert row.has_session_cap() is True
    assert row.max_session_seconds() == 4 * 3600


def test_awake_since_defaults_to_absent():
    assert App.create(host="a.b").awake_since == 0


# --- item encoding ---------------------------------------------------------


def test_item_round_trip():
    row = App.create(
        host="model.tools.stratevi.com",
        app_key="model",
        ecs_service="shiny-model",
        container_port=3838,
        status=registry.STATUS_ACTIVE,
        access_mode=registry.MODE_USERS,
        allowed_emails=["jake@stratevi.com"],
        idle_minutes=15,
        expires_at=1_800_000_000,
        last_active=1_700_000_000,
        max_session_hours=4,
        awake_since=1_700_000_500,
    )
    assert registry.app_from_item(registry.app_item(row)) == row


def test_an_empty_string_set_is_omitted_rather_than_written_empty():
    item = registry.app_item(App.create(host="a.b", access_mode=registry.MODE_ALL_USERS))
    assert "allowed_emails" not in item
    assert "expires_at" not in item
    assert "last_active" not in item
    assert "max_session_hours" not in item
    assert "awake_since" not in item


def test_allowed_emails_written_by_hand_as_a_list_are_read():
    row = registry.app_from_item(
        {
            "host": {"S": "a.b"},
            "allowed_emails": {"L": [{"S": "JAKE@stratevi.com"}, {"S": "nick@stratevi.com"}]},
        }
    )
    assert row.allowed_emails == ("jake@stratevi.com", "nick@stratevi.com")


def test_a_sparse_item_decodes_to_the_defaults():
    row = registry.app_from_item({"host": {"S": "a.b"}})
    assert row.container_port == registry.DEFAULT_CONTAINER_PORT
    assert row.idle_minutes == registry.DEFAULT_IDLE_MINUTES
    assert row.status == registry.STATUS_ACTIVE
    assert row.access_mode == registry.MODE_USERS
    assert row.max_session_hours == registry.DEFAULT_MAX_SESSION_HOURS
    assert row.awake_since == 0


# --- the read-through cache ------------------------------------------------


class FakeStore:
    """An in-memory AppStore that counts reads."""

    def __init__(self, rows: dict[str, App] | None = None) -> None:
        self.rows = rows or {}
        self.reads = 0
        self.fail = False
        self.statuses: list[tuple[str, str]] = []
        self.awake_since_writes: list[tuple[str, int]] = []

    async def app(self, host: str) -> App | None:
        self.reads += 1
        if self.fail:
            raise RuntimeError("dynamodb is unhappy")
        return self.rows.get(host)

    async def apps(self) -> list[App]:
        return list(self.rows.values())

    async def set_last_active(self, host: str, ts: int) -> None:
        pass

    async def set_awake_since(self, host: str, ts: int) -> None:
        self.awake_since_writes.append((host, ts))

    async def set_status(self, host: str, status: str) -> None:
        self.statuses.append((host, status))

    async def ping(self) -> None:
        pass


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def cached():
    store = FakeStore({"a.b": App.create(host="a.b", app_key="a")})
    clock = Clock()
    return store, clock, registry.CachedRegistry(store, ttl=10.0, clock=clock)


@pytest.mark.asyncio
async def test_hits_within_the_ttl_do_not_reach_the_store(cached):
    store, clock, cache = cached
    for _ in range(40):  # a Shiny page load's worth of assets
        assert (await cache.app("a.b")).app_key == "a"
    assert store.reads == 1


@pytest.mark.asyncio
async def test_the_entry_expires(cached):
    store, clock, cache = cached
    await cache.app("a.b")
    clock.t += 11
    await cache.app("a.b")
    assert store.reads == 2


@pytest.mark.asyncio
async def test_misses_are_cached_too_so_an_unknown_host_cannot_hammer_the_table(cached):
    store, clock, cache = cached
    assert await cache.app("nope.tools.stratevi.com") is None
    assert await cache.app("nope.tools.stratevi.com") is None
    assert store.reads == 1


@pytest.mark.asyncio
async def test_host_variants_share_one_cache_entry(cached):
    store, clock, cache = cached
    await cache.app("a.b")
    await cache.app("A.B:443")
    assert store.reads == 1


@pytest.mark.asyncio
async def test_a_store_error_serves_the_stale_entry(cached):
    store, clock, cache = cached
    await cache.app("a.b")
    clock.t += 11
    store.fail = True
    assert (await cache.app("a.b")).app_key == "a"


@pytest.mark.asyncio
async def test_a_store_error_with_nothing_cached_propagates_and_fails_closed(cached):
    store, clock, cache = cached
    store.fail = True
    with pytest.raises(RuntimeError):
        await cache.app("cold.tools.stratevi.com")


@pytest.mark.asyncio
async def test_invalidate_and_set_status_force_a_fresh_read(cached):
    store, clock, cache = cached
    await cache.app("a.b")
    cache.invalidate("A.B:443")
    await cache.app("a.b")
    assert store.reads == 2

    await cache.set_status("a.b", registry.STATUS_EXPIRED)
    assert store.statuses == [("a.b", registry.STATUS_EXPIRED)]
    await cache.app("a.b")
    assert store.reads == 3


@pytest.mark.asyncio
async def test_set_awake_since_passes_through_without_invalidating_the_cache(cached):
    """Unlike set_status: awake_since does not affect the access decision, so
    a cached row need not be dropped for it to take effect."""
    store, clock, cache = cached
    await cache.app("a.b")
    assert store.reads == 1

    await cache.set_awake_since("A.B:443", 1234)
    assert store.awake_since_writes == [("A.B:443", 1234)]

    await cache.app("a.b")
    assert store.reads == 1
