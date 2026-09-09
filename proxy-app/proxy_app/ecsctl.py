"""Finds and scales the app tasks.

The proxy talks to a task's awsvpc ENI address directly rather than through a
per-app target group -- that is what removes the ~50-target-group ceiling
(ADR-0014). The cost is that the proxy has to do the discovery itself, and
discovery is two API calls, so the answer is cached.

``EcsBackend`` keeps boto3 out of the caching/wake/sleep logic; the AWS
implementation is :class:`Boto3EcsBackend` at the bottom of the module.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

#: How long a discovered task IP is trusted. Long enough that a page load
#: costs one DescribeTasks, short enough that a replaced task is picked up
#: before a user notices.
DEFAULT_TTL = 10.0

#: A "no task yet" answer is cached far more briefly: during a cold start the
#: whole point is to notice the moment an IP appears.
MISS_TTL = 2.0

#: The key ECS uses for an awsvpc task's ENI address. This attribute is the
#: whole reason the proxy can skip per-app target groups.
PRIVATE_IP_DETAIL = "privateIPv4Address"


@dataclass(frozen=True)
class ServiceState:
    """The part of an ECS service the proxy cares about."""

    exists: bool = False
    desired: int = 0
    running: int = 0
    pending: int = 0


class EcsBackend(Protocol):
    """The ECS API surface used here, already bound to a cluster."""

    async def describe_service(self, service: str) -> ServiceState: ...

    async def task_ip(self, service: str) -> str: ...

    async def set_desired_count(self, service: str, count: int) -> None: ...


class Controller:
    """Caches task discovery and drives desired counts."""

    def __init__(
        self,
        backend: EcsBackend,
        ttl: float = DEFAULT_TTL,
        *,
        miss_ttl: float = MISS_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._ttl = ttl if ttl > 0 else DEFAULT_TTL
        self._miss_ttl = miss_ttl if miss_ttl > 0 else MISS_TTL
        self._clock = clock
        self._cache: dict[str, tuple[str, float]] = {}

    async def task_ip(self, service: str) -> str:
        """The private address of a running task, or "" when there is none.

        "" is not an error: it is the normal answer for a sleeping app, and
        the caller's cue to wake it.
        """
        cached = self._cache.get(service)
        if cached is not None:
            ttl = self._ttl if cached[0] else self._miss_ttl
            if self._clock() - cached[1] < ttl:
                return cached[0]

        ip = await self._backend.task_ip(service)
        self._cache[service] = (ip, self._clock())
        return ip

    def forget(self, service: str) -> None:
        """Drop a cached address, so the next request rediscovers.

        Called when a connection to that address fails -- the task has
        probably been replaced, and retrying a dead address for another ten
        seconds helps nobody.
        """
        self._cache.pop(service, None)

    async def wake(self, service: str) -> bool:
        """Set desired count to 1 if it is 0; report whether that changed anything.

        Only a real wake is audited. Calling this on an already-awake service
        is a no-op, which matters because every request during a 30-60s cold
        start comes through here.
        """
        state = await self._backend.describe_service(service)
        if not state.exists or state.desired > 0:
            return False
        await self._backend.set_desired_count(service, 1)
        return True

    async def sleep(self, service: str) -> None:
        """Scale a service to zero."""
        await self._backend.set_desired_count(service, 0)
        self.forget(service)

    async def state(self, service: str) -> ServiceState:
        """An uncached read, used by the sleeper loop."""
        return await self._backend.describe_service(service)


class Boto3EcsBackend:
    """:class:`EcsBackend` on a plain boto3 ECS client, bound to one cluster.

    Synchronous boto3 calls are pushed onto threads; see the note in
    ``registry.DynamoAppStore`` for why that is preferred over a second SDK.
    """

    def __init__(self, client: Any, cluster: str) -> None:
        self._client = client
        self._cluster = cluster

    async def describe_service(self, service: str) -> ServiceState:
        response = await asyncio.to_thread(
            self._client.describe_services, cluster=self._cluster, services=[service]
        )
        for described in response.get("services", ()):
            # A deleted service lingers as INACTIVE. Treat it as gone, or the
            # proxy will happily try to wake a corpse on every request.
            if str(described.get("status", "")).upper() == "INACTIVE":
                continue
            return ServiceState(
                exists=True,
                desired=int(described.get("desiredCount", 0)),
                running=int(described.get("runningCount", 0)),
                pending=int(described.get("pendingCount", 0)),
            )
        return ServiceState()

    async def task_ip(self, service: str) -> str:
        listed = await asyncio.to_thread(
            self._client.list_tasks,
            cluster=self._cluster,
            serviceName=service,
            desiredStatus="RUNNING",
        )
        arns = listed.get("taskArns") or []
        if not arns:
            return ""  # asleep; the caller's cue to wake it

        described = await asyncio.to_thread(
            self._client.describe_tasks, cluster=self._cluster, tasks=arns
        )
        for task in described.get("tasks", ()):
            if str(task.get("lastStatus", "")).upper() != "RUNNING":
                continue
            for attachment in task.get("attachments", ()):
                if str(attachment.get("type", "")).lower() != "elasticnetworkinterface":
                    continue
                for detail in attachment.get("details", ()):
                    if detail.get("name") == PRIVATE_IP_DETAIL and detail.get("value"):
                        return str(detail["value"])

        # Tasks exist but none is RUNNING with an address yet: still starting.
        return ""

    async def set_desired_count(self, service: str, count: int) -> None:
        await asyncio.to_thread(
            self._client.update_service,
            cluster=self._cluster,
            service=service,
            desiredCount=int(count),
        )
