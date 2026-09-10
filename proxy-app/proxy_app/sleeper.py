"""The background loop that scales idle apps to zero and expires apps whose
time is up.

It replaces the per-app sleeper Lambda for migrated apps (ADR-0002's sleeper
half) and is phase 1 of the ADR-0014 reaper. It is deliberately dull: read the
table, ask ECS, act, audit. It holds no state of its own, so a second proxy
task running the same loop reaches the same conclusion and its UpdateService
is a harmless no-op.

--- force-sleep cap (C1) -----------------------------------------------------

The sleeper treats an open websocket as activity (activity.py), so a browser
tab left open keeps an expensive app -- the model at $0.233/hr -- awake
indefinitely. ``max_session_hours`` is the hard ceiling: once a service has
been continuously awake longer than the cap, this loop force-sleeps it even
with open sockets or recent requests. Users lose their session, which is
acceptable (the wake page is one refresh away); money stops burning.

``awake_since`` is what "continuously awake" is measured from. The proxy sets
it when it wakes an app (server.Proxy._wake); this loop fills in the two cases
the wake path cannot see -- a service running with no awake_since recorded
(woken by something else, or a proxy that restarted mid-session) sets it to
now rather than guessing at history, and a service observed at 0 clears it.
Conservative both ways: never undercounts the REMAINING time before a cap
trips, never force-sleeps early off history nobody actually observed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Protocol

from . import audit as audit_mod
from . import registry
from .audit import Event
from .ecsctl import ServiceState
from .registry import App

#: Interval between passes. A code constant, not config: idle_minutes is
#: measured in tens of minutes, and expiry to the minute is closer than any
#: reminder email will ever be.
INTERVAL = 60.0


class Scaler(Protocol):
    """The ECS surface used here."""

    async def state(self, service: str) -> ServiceState: ...

    async def sleep(self, service: str) -> None: ...


class ActivitySource(Protocol):
    """The activity-tracker surface used here."""

    def sockets(self, host: str) -> int: ...

    def last_active(self, host: str) -> float: ...

    @property
    def boot(self) -> float: ...


class Recorder(Protocol):
    def record(self, event: Event) -> None: ...


class BuildSweeper(Protocol):
    """``provision.BuildWatcher``, narrowed to the one call this loop makes."""

    async def sweep(self, app: App, now: float) -> None: ...


class Loop:
    """The sleeper/reaper."""

    def __init__(
        self,
        store: registry.AppStore,
        scaler: Scaler,
        activity: ActivitySource,
        recorder: Recorder,
        log: logging.Logger | None = None,
        *,
        clock: Callable[[], float] = time.time,
        interval: float = INTERVAL,
        builds: BuildSweeper | None = None,
    ) -> None:
        self._store = store
        self._scaler = scaler
        self._activity = activity
        self._audit = recorder
        self._log = log or logging.getLogger("proxy.sleeper")
        self._clock = clock
        self._interval = interval
        # None until the P2a creation pipeline is configured. Rows can only
        # be in `building` if something created them, so with no sweeper
        # there is nothing to sweep.
        self._builds = builds

    async def run(self) -> None:
        """Tick until cancelled. One bad pass must not end the loop."""
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - belt and braces
                self._log.error("sleeper pass failed", extra={"reason": str(exc)})

    async def tick(self) -> None:
        """One pass. Public so a test can drive it directly."""
        now = self._clock()

        try:
            apps = await self._store.apps()
        except Exception as exc:
            self._log.error("sleeper: cannot list apps", extra={"reason": str(exc)})
            return

        for app in apps:
            # Belt and braces: the store already filters `__`-prefixed
            # configuration rows out of the enumeration, but this loop calls
            # UpdateService, and a bug that let the portal's `__config__` row
            # through would have it describing a service called "" once a
            # minute forever.
            if registry.is_config_host(app.host):
                continue
            try:
                # Builds first. A row in `building` has no ECS service yet --
                # describing one would be an error a minute, forever -- and
                # it is the ONE state that can get stuck with nothing else
                # watching it. portal-p2a.md: never leave an app "creating"
                # forever. Success is detected here too, by polling, rather
                # than by a webhook that would need a public endpoint and an
                # auth story of its own.
                if app.status == registry.STATUS_BUILDING:
                    await self._sweep_build(app, now)
                    continue
                if await self._expire(app, now):
                    continue
                if app.status != registry.STATUS_ACTIVE:
                    continue
                await self._track_and_sleep(app, now)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.error(
                    "sleeper: pass failed for app",
                    extra={"host": app.host, "reason": str(exc)},
                )

    async def _sweep_build(self, app: App, now: float) -> None:
        """Hand a ``building`` row to the build watcher.

        A configured-away sweeper is logged rather than ignored: a row in
        `building` with nothing to advance it is exactly the stuck state the
        45-minute reaper exists to prevent, and if the creation env has been
        removed from under a live row somebody needs to know.
        """
        if self._builds is None:
            self._log.warning(
                "an app is building but no build watcher is configured",
                extra={"host": app.host, "build_id": app.build_id},
            )
            return
        await self._builds.sweep(app, now)

    async def _expire(self, app: App, now: float) -> bool:
        """Flip a lapsed app to expired and scale it to zero.

        Returns True when the app is expired, whether or not this pass is the
        one that changed it.
        """
        if app.status == registry.STATUS_EXPIRED:
            return True
        if not app.is_expired(now):
            return False

        try:
            await self._store.set_status(app.host, registry.STATUS_EXPIRED)
        except Exception as exc:
            self._log.error(
                "reaper: cannot mark expired",
                extra={"host": app.host, "reason": str(exc)},
            )
            # Keep going anyway: scaling to zero is the part that costs money,
            # and the access decision already refuses on the clock alone.

        await self._scale_to_zero(app, "reaper")
        self._audit.record(Event(host=app.host, event=audit_mod.EVENT_EXPIRED, at=now))
        self._log.info(
            "app expired",
            extra={"host": app.host, "app_key": app.app_key, "expires_at": app.expires_at},
        )
        return True

    async def _track_and_sleep(self, app: App, now: float) -> None:
        """Keep ``awake_since`` in step with reality, then decide whether to
        force-sleep (cap) or idle-sleep (no recent activity).

        One ``describe_service`` call covers both decisions below, rather than
        the bookkeeping and the sleeping each fetching their own state.
        """
        try:
            state = await self._scaler.state(app.ecs_service)
        except Exception as exc:
            self._log.error(
                "sleeper: cannot describe service",
                extra={"host": app.host, "service": app.ecs_service, "reason": str(exc)},
            )
            return

        running = state.exists and state.desired > 0
        if not running:
            # Observed at 0: nothing to force-sleep or idle-sleep, and any
            # awake_since left over from before is now meaningless.
            if app.awake_since:
                await self._set_awake_since(app, 0)
            return

        awake_since = app.awake_since
        if not awake_since:
            # Running, but this row has no memory of when that started -- woken
            # by something other than the proxy's own wake path, or a proxy
            # that restarted mid-session. Start counting from now rather than
            # guessing at unknown history: this can never undercount the
            # REMAINING time before a cap trips, and never force-sleeps early
            # off a history nobody actually observed.
            awake_since = int(now)
            await self._set_awake_since(app, awake_since)

        # The cap check runs before the idle check and ignores activity and
        # open sockets entirely -- that is the whole point of C1.
        if app.has_session_cap() and now - awake_since > app.max_session_seconds():
            await self._force_sleep(app, state, now)
            return

        await self._idle_sleep(app, state, now)

    async def _force_sleep(self, app: App, state: ServiceState, now: float) -> None:
        if not await self._scale(app, state, "sleeper"):
            return
        await self._set_awake_since(app, 0)
        self._audit.record(Event(host=app.host, event=audit_mod.EVENT_FORCE_SLEEP, at=now))
        self._log.info(
            "app force-slept: session cap exceeded",
            extra={
                "host": app.host,
                "app_key": app.app_key,
                "max_session_hours": app.max_session_hours,
            },
        )

    async def _idle_sleep(self, app: App, state: ServiceState, now: float) -> None:
        if self._activity.sockets(app.host) > 0:
            return
        if now - self._last_active(app) < app.idle_after_seconds():
            return

        if not await self._scale(app, state, "sleeper"):
            return

        await self._set_awake_since(app, 0)
        self._audit.record(Event(host=app.host, event=audit_mod.EVENT_SLEEP, at=now))
        self._log.info(
            "app asleep",
            extra={"host": app.host, "app_key": app.app_key, "idle_minutes": app.idle_minutes},
        )

    async def _set_awake_since(self, app: App, ts: int) -> None:
        """Best-effort, like every other write this loop makes: a failure here
        must not stop the pass, and never blocks scaling."""
        try:
            await self._store.set_awake_since(app.host, ts)
        except Exception as exc:
            self._log.error(
                "sleeper: cannot persist awake_since",
                extra={"host": app.host, "reason": str(exc)},
            )

    async def _scale_to_zero(self, app: App, who: str) -> bool:
        """Fetch state and scale to zero if not already there.

        Used by the reaper, which (unlike ``_track_and_sleep``) has no other
        reason to describe the service first.
        """
        try:
            state = await self._scaler.state(app.ecs_service)
        except Exception as exc:
            self._log.error(
                f"{who}: cannot describe service",
                extra={"host": app.host, "service": app.ecs_service, "reason": str(exc)},
            )
            return False
        return await self._scale(app, state, who)

    async def _scale(self, app: App, state: ServiceState, who: str) -> bool:
        if not state.exists or state.desired == 0:
            return False
        try:
            await self._scaler.sleep(app.ecs_service)
        except Exception as exc:
            self._log.error(
                f"{who}: cannot scale to zero",
                extra={"host": app.host, "service": app.ecs_service, "reason": str(exc)},
            )
            return False
        return True

    def _last_active(self, app: App) -> float:
        """The most generous of: this process's boot time, what this process
        has seen, and what any proxy task persisted.

        Boot counts because a restarted proxy has no memory of the users
        currently on an app, and slamming a busy app to zero on deploy is far
        worse than one wasted idle window.
        """
        return max(
            self._activity.boot,
            self._activity.last_active(app.host),
            float(app.last_active),
        )
