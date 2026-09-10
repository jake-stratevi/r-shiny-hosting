"""Task discovery caching, the negative cache, and idempotent wake."""

from __future__ import annotations

import pytest

from proxy_app import ecsctl
from proxy_app.ecsctl import ServiceState


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeBackend:
    def __init__(self, ip: str = "", state: ServiceState | None = None) -> None:
        self.ip = ip
        self.state = state or ServiceState(exists=True, desired=0)
        self.lookups = 0
        self.desired: list[int] = []

    async def describe_service(self, service: str) -> ServiceState:
        return self.state

    async def task_ip(self, service: str) -> str:
        self.lookups += 1
        return self.ip

    async def set_desired_count(self, service: str, count: int) -> None:
        self.desired.append(count)
        self.state = ServiceState(exists=True, desired=count)


@pytest.fixture
def controller():
    backend = FakeBackend()
    clock = Clock()
    return backend, clock, ecsctl.Controller(backend, ttl=10.0, miss_ttl=2.0, clock=clock)


@pytest.mark.asyncio
async def test_a_discovered_address_is_cached_for_the_ttl(controller):
    backend, clock, tasks = controller
    backend.ip = "10.0.1.5"

    for _ in range(40):  # a page load's worth of assets
        assert await tasks.task_ip("shiny-model") == "10.0.1.5"
    assert backend.lookups == 1

    clock.t += 11
    await tasks.task_ip("shiny-model")
    assert backend.lookups == 2


@pytest.mark.asyncio
async def test_a_missing_task_is_cached_far_more_briefly(controller):
    """During a cold start the point is to notice the moment an IP appears."""
    backend, clock, tasks = controller

    assert await tasks.task_ip("shiny-model") == ""
    clock.t += 1.5
    assert await tasks.task_ip("shiny-model") == ""
    assert backend.lookups == 1

    clock.t += 1
    backend.ip = "10.0.1.5"
    assert await tasks.task_ip("shiny-model") == "10.0.1.5"
    assert backend.lookups == 2


@pytest.mark.asyncio
async def test_forget_drops_the_cached_address(controller):
    backend, clock, tasks = controller
    backend.ip = "10.0.1.5"
    await tasks.task_ip("shiny-model")

    tasks.forget("shiny-model")
    backend.ip = "10.0.1.9"
    assert await tasks.task_ip("shiny-model") == "10.0.1.9"


@pytest.mark.asyncio
async def test_wake_scales_a_sleeping_service_and_reports_that_it_did(controller):
    backend, clock, tasks = controller
    assert await tasks.wake("shiny-model") is True
    assert backend.desired == [1]


@pytest.mark.asyncio
async def test_wake_is_idempotent_so_only_the_first_request_is_audited(controller):
    """Every request during a 30-60s cold start lands here."""
    backend, clock, tasks = controller
    assert await tasks.wake("shiny-model") is True
    for _ in range(20):
        assert await tasks.wake("shiny-model") is False
    assert backend.desired == [1]


@pytest.mark.asyncio
async def test_a_deleted_service_is_never_woken(controller):
    backend, clock, tasks = controller
    backend.state = ServiceState(exists=False)
    assert await tasks.wake("shiny-model") is False
    assert backend.desired == []


@pytest.mark.asyncio
async def test_sleep_scales_to_zero_and_drops_the_cache(controller):
    backend, clock, tasks = controller
    backend.ip = "10.0.1.5"
    await tasks.task_ip("shiny-model")

    await tasks.sleep("shiny-model")
    assert backend.desired == [0]

    backend.ip = ""
    assert await tasks.task_ip("shiny-model") == ""


# --- the boto3 shapes ------------------------------------------------------


class FakeEcsClient:
    def __init__(self, tasks=None, services=None) -> None:
        self._tasks = tasks or {}
        self._services = services or {}
        self.calls: list[str] = []

    def list_tasks(self, **kwargs):
        self.calls.append("list_tasks")
        return {"taskArns": list(self._tasks)}

    def describe_tasks(self, **kwargs):
        self.calls.append("describe_tasks")
        return {"tasks": [self._tasks[arn] for arn in kwargs["tasks"]]}

    def describe_services(self, **kwargs):
        self.calls.append("describe_services")
        return {"services": self._services.get("services", [])}

    def update_service(self, **kwargs):
        self.calls.append("update_service")
        return {}


def running_task(ip: str, status: str = "RUNNING") -> dict:
    return {
        "lastStatus": status,
        "attachments": [
            {
                "type": "ElasticNetworkInterface",
                "details": [
                    {"name": "networkInterfaceId", "value": "eni-123"},
                    {"name": ecsctl.PRIVATE_IP_DETAIL, "value": ip},
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_task_ip_reads_the_awsvpc_eni_address():
    client = FakeEcsClient({"arn:1": running_task("10.0.1.5")})
    backend = ecsctl.Boto3EcsBackend(client, "shiny-cluster")
    assert await backend.task_ip("shiny-model") == "10.0.1.5"
    # ListTasks is what produces the ARNs DescribeTasks needs -- that pair is
    # why the task policy carries ecs:ListTasks.
    assert client.calls == ["list_tasks", "describe_tasks"]


@pytest.mark.asyncio
async def test_no_tasks_means_asleep_not_an_error():
    backend = ecsctl.Boto3EcsBackend(FakeEcsClient({}), "shiny-cluster")
    assert await backend.task_ip("shiny-model") == ""


@pytest.mark.asyncio
async def test_a_task_that_is_not_running_yet_has_no_address():
    client = FakeEcsClient({"arn:1": running_task("10.0.1.5", status="PROVISIONING")})
    backend = ecsctl.Boto3EcsBackend(client, "shiny-cluster")
    assert await backend.task_ip("shiny-model") == ""


@pytest.mark.asyncio
async def test_an_inactive_service_is_treated_as_gone():
    """A deleted service lingers as INACTIVE; waking a corpse helps nobody."""
    client = FakeEcsClient(
        services={"services": [{"status": "INACTIVE", "desiredCount": 0}]}
    )
    backend = ecsctl.Boto3EcsBackend(client, "shiny-cluster")
    assert await backend.describe_service("shiny-model") == ServiceState(exists=False)


@pytest.mark.asyncio
async def test_an_active_service_reports_its_counts():
    client = FakeEcsClient(
        services={
            "services": [
                {"status": "ACTIVE", "desiredCount": 1, "runningCount": 1, "pendingCount": 0}
            ]
        }
    )
    backend = ecsctl.Boto3EcsBackend(client, "shiny-cluster")
    assert await backend.describe_service("shiny-model") == ServiceState(
        exists=True, desired=1, running=1, pending=0
    )


# --- cached service state (the portal's status badges) ---------------------


class CountingBackend(FakeBackend):
    def __init__(self, states=None) -> None:
        super().__init__()
        self.states = states or {}
        self.describes: list[str] = []
        self.broken: set[str] = set()

    async def describe_service(self, service: str) -> ServiceState:
        self.describes.append(service)
        if service in self.broken:
            raise RuntimeError("ecs is unhappy")
        return self.states.get(service, ServiceState(exists=True, desired=0))


@pytest.mark.asyncio
async def test_a_described_state_is_cached_for_the_state_ttl():
    backend = CountingBackend()
    clock = Clock()
    tasks = ecsctl.Controller(backend, state_ttl=5.0, clock=clock)

    for _ in range(10):
        await tasks.cached_state("shiny-model")
    assert backend.describes == ["shiny-model"]

    clock.t += 6
    await tasks.cached_state("shiny-model")
    assert len(backend.describes) == 2


@pytest.mark.asyncio
async def test_the_sleeper_s_state_is_deliberately_not_the_cached_one():
    """It is about to scale a service to zero on the strength of the answer."""
    backend = CountingBackend()
    tasks = ecsctl.Controller(backend, clock=Clock())
    await tasks.state("shiny-model")
    await tasks.state("shiny-model")
    assert len(backend.describes) == 2


@pytest.mark.asyncio
async def test_states_batches_a_page_and_deduplicates_it():
    backend = CountingBackend(
        {
            "shiny-model": ServiceState(exists=True, desired=1, running=1),
            "shiny-dashboard": ServiceState(exists=True, desired=1, running=0),
        }
    )
    tasks = ecsctl.Controller(backend, clock=Clock())

    found = await tasks.states(
        ["shiny-model", "shiny-dashboard", "shiny-model", ""]
    )

    assert set(found) == {"shiny-model", "shiny-dashboard"}
    assert found["shiny-model"].running == 1
    assert sorted(backend.describes) == ["shiny-dashboard", "shiny-model"]


@pytest.mark.asyncio
async def test_a_service_that_cannot_be_described_degrades_to_an_empty_state():
    """A portal page that 500s because one ECS call timed out is worse than
    a badge that reads "asleep"."""
    backend = CountingBackend()
    backend.broken = {"shiny-broken"}
    tasks = ecsctl.Controller(backend, clock=Clock())

    found = await tasks.states(["shiny-broken", "shiny-model"])

    assert found["shiny-broken"] == ServiceState()
    assert found["shiny-model"].exists is True


@pytest.mark.asyncio
async def test_waking_and_forgetting_both_drop_the_cached_state():
    """A just-woken app showing "asleep" for five seconds is a bug report."""
    backend = CountingBackend()
    tasks = ecsctl.Controller(backend, clock=Clock())

    await tasks.cached_state("shiny-model")
    assert await tasks.wake("shiny-model") is True
    await tasks.cached_state("shiny-model")
    assert backend.describes.count("shiny-model") == 3  # cached, wake, re-read

    tasks.forget("shiny-model")
    await tasks.cached_state("shiny-model")
    assert backend.describes.count("shiny-model") == 4
