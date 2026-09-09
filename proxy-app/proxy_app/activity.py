"""The server-side replacement for the ADR-0006 client heartbeat.

The heartbeat existed because idle detection read ``RequestCountPerTarget``
and a Shiny websocket generates zero HTTP requests, so the sleeper would scale
a task to zero underneath a working user. The proxy sees the websocket itself,
so "active" here means: a proxied HTTP request, or at least one open
websocket. Migrated apps can drop the snippet from their UI.

``last_active`` is mirrored to DynamoDB at most once a minute so that a proxy
restart does not make every app look idle since the epoch. On restart the boot
time also counts as activity, which gives a busy app a full idle window to
prove itself rather than being slept out from under its users.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol

#: How often an active host's last_active is written back to the table. Not
#: per request: a Shiny page load is dozens of requests, and this is a write.
PERSIST_EVERY = 60.0


class LastActiveWriter(Protocol):
    """The registry's persistence, narrowed to what is used here."""

    async def set_last_active(self, host: str, ts: int) -> None: ...


@dataclass
class _HostState:
    last: float = 0.0
    persisted: float = 0.0
    sockets: int = 0


class Tracker:
    """Per-host activity, held in memory and mirrored to the table.

    Wall-clock (``time.time``) is used rather than a monotonic clock because
    the value is persisted as an epoch and compared against rows written by
    the other proxy task.
    """

    def __init__(
        self,
        writer: LastActiveWriter | None = None,
        log: logging.Logger | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._writer = writer
        self._log = log or logging.getLogger("proxy.activity")
        self._clock = clock
        self._boot = clock()
        self._hosts: dict[str, _HostState] = {}
        # Strong refs to in-flight persists: asyncio only holds weak ones, and
        # a garbage-collected task is a silently dropped write.
        self._pending: set[asyncio.Task[None]] = set()

    @property
    def boot(self) -> float:
        """When this proxy process started."""
        return self._boot

    def touch(self, host: str) -> None:
        """Record a request against a host.

        At most once a minute this also schedules a write-back, on its own
        task so the DynamoDB round trip never sits in the request path.
        """
        now = self._clock()
        state = self._hosts.get(host)
        if state is None:
            state = _HostState()
            self._hosts[host] = state
        state.last = now

        if now - state.persisted < PERSIST_EVERY:
            return
        state.persisted = now
        if self._writer is None:
            return

        try:
            task = asyncio.get_running_loop().create_task(self._persist(host, now))
        except RuntimeError:
            return  # no loop (a unit test calling touch synchronously)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _persist(self, host: str, at: float) -> None:
        try:
            await self._writer.set_last_active(host, int(at))  # type: ignore[union-attr]
        except Exception as exc:  # never allowed to escape into the loop
            self._log.warning(
                "last_active write failed", extra={"host": host, "reason": str(exc)}
            )

    @contextlib.contextmanager
    def open_socket(self, host: str) -> Iterator[None]:
        """Register an open websocket for the duration of the ``with`` block.

        An app with an open socket is never slept, however long it has been
        since the last HTTP request -- that is the whole point.
        """
        self.touch(host)
        state = self._hosts.setdefault(host, _HostState(last=self._clock()))
        state.sockets += 1
        try:
            yield
        finally:
            if state.sockets > 0:
                state.sockets -= 1
            state.last = self._clock()

    def sockets(self, host: str) -> int:
        """Websockets currently open through THIS proxy task for a host.

        With two proxy tasks each sees only its own; the sleeper is therefore
        conservative per task, and the persisted ``last_active`` is what keeps
        the two honest about HTTP activity.
        """
        state = self._hosts.get(host)
        return state.sockets if state else 0

    def last_active(self, host: str) -> float:
        """In-memory last activity, 0.0 if this process has never seen it."""
        state = self._hosts.get(host)
        return state.last if state else 0.0
