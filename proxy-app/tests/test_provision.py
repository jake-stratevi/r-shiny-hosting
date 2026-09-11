"""P2a provisioning: the order, the failure rule, the boundary, Cognito.

Same style as the rest of the suite -- fakes behind the module's protocols,
plus hand-written stand-ins for the boto3 clients where the AWS classes
themselves are what is under test. No AWS, no network, no credentials.

The two assertions here that would cost the most to get wrong in production:

* every ``shiny-app-*`` role carries the permissions boundary, and there is
  no way to construct the role maker without one;
* ``UpdateUserPoolClient`` is sent every field ``DescribeUserPoolClient``
  returned, so adding one callback URL cannot silently wipe the shared
  client's OAuth configuration and lock everyone out of every app.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from proxy_app import audit, creation, provision, registry
from proxy_app.registry import App

NOW = 1_800_000_000.0
CREATOR = "jake@stratevi.com"
DOMAIN = "tools.stratevi.com"
UPLOAD = "uploads/3f2504e0-4f89-11d3-9a0c-0305e82c3301.zip"
BOUNDARY = "arn:aws:iam::652063276768:policy/shiny-app-boundary"

#: What a created app's hostname looks like: the key, then a random
#: base32 suffix. Tests match this rather than naming a host, because the
#: whole point of the suffix is that nobody -- including a test -- can
#: predict it.
SUFFIXED_HOST = re.compile(r"^tarpeyo-[a-z2-7]{6}\.tools\.stratevi\.com$")


# --- fakes -----------------------------------------------------------------


class FakeStore:
    def __init__(self, rows: list[App] | None = None) -> None:
        self.rows = {row.host: row for row in (rows or [])}
        self.patches: list[tuple[str, dict]] = []
        self.reserved: list[App] = []
        self.taken: set[str] = set()
        #: Keys already claimed. Hostnames carry a random suffix now, so a
        #: test that wants `reserve` to lose a race cannot name the host it
        #: is going to lose -- it names the key.
        self.taken_keys: set[str] = set()
        self.fail_patch = False

    async def app(self, host):
        return self.rows.get(registry.normalize_host(host))

    async def apps(self):
        return list(self.rows.values())

    async def reserve(self, app: App) -> None:
        if (
            app.host in self.rows
            or app.host in self.taken
            or app.app_key in self.taken_keys
        ):
            raise registry.HostTaken(app.host)
        self.reserved.append(app)
        self.rows[app.host] = app

    async def patch(self, host, changes) -> None:
        if self.fail_patch:
            raise RuntimeError("dynamodb is unhappy")
        self.patches.append((host, dict(changes)))
        row = self.rows[registry.normalize_host(host)]
        self.rows[row.host] = App.create(**{**row.__dict__, **changes})

    def row(self, host="tarpeyo.tools.stratevi.com") -> App:
        return self.rows[host]

    def created(self) -> App:
        """The one row `create` reserved, re-read.

        A created hostname is not predictable any more -- it carries a random
        suffix -- so a create-path test asks the store which row it made
        instead of naming it.
        """
        assert len(self.reserved) == 1, self.reserved
        return self.rows[self.reserved[0].host]


class Steps:
    """Records the order provisioning actually happened in."""

    def __init__(self) -> None:
        self.calls: list[str] = []


class FakeRepositories:
    def __init__(self, steps: Steps, fail: bool = False) -> None:
        self.steps = steps
        self.fail = fail

    async def ensure(self, name: str) -> str:
        self.steps.calls.append(f"ecr:{name}")
        if self.fail:
            raise RuntimeError("ecr is unhappy")
        return f"652063276768.dkr.ecr.us-east-1.amazonaws.com/{name}"


class FakeRoles:
    def __init__(self, steps: Steps, fail: bool = False) -> None:
        self.steps = steps
        self.fail = fail

    async def ensure(self, app_key: str) -> str:
        self.steps.calls.append(f"iam:{app_key}")
        if self.fail:
            raise RuntimeError("iam is unhappy")
        return f"arn:aws:iam::652063276768:role/shiny-app-{app_key}-task"


class FakeBuilds:
    def __init__(self, steps: Steps | None = None) -> None:
        self.steps = steps or Steps()
        self.started: list[dict] = []
        self.state = provision.BuildStatus(
            state=provision.BUILD_IN_PROGRESS, phase="BUILD", started_at=int(NOW)
        )
        self.fail_start = False
        self.fail_status = False
        self.tail = ["installing shiny"]

    async def start(
        self, *, app_key, zip_key, release_tag, repository_uri, packages
    ) -> str:
        self.steps.calls.append(f"codebuild:{app_key}")
        if self.fail_start:
            raise RuntimeError("codebuild is unhappy")
        self.started.append(
            {
                "app_key": app_key,
                "zip_key": zip_key,
                "release_tag": release_tag,
                "repository_uri": repository_uri,
                "packages": packages,
            }
        )
        return "shiny-app-build:abc-123"

    async def status(self, build_id: str) -> provision.BuildStatus:
        if self.fail_status:
            raise RuntimeError("codebuild is unhappy")
        return self.state

    async def log_tail(self, build, lines) -> list[str]:
        return list(self.tail)


class FakeServices:
    def __init__(self, steps: Steps | None = None) -> None:
        self.steps = steps or Steps()
        self.registered: list[App] = []
        self.created: list[dict] = []
        self.fail_register = False
        self.fail_create = False

    async def register_task_definition(self, app: App) -> str:
        self.steps.calls.append("taskdef")
        if self.fail_register:
            raise RuntimeError("ecs is unhappy")
        self.registered.append(app)
        return f"arn:aws:ecs:us-east-1:1:task-definition/shiny-{app.app_key}:1"

    async def create_service(self, *, service, task_definition) -> None:
        self.steps.calls.append("service")
        if self.fail_create:
            raise RuntimeError("ecs is unhappy")
        self.created.append({"service": service, "task_definition": task_definition})


class FakeClients:
    def __init__(self, steps: Steps | None = None) -> None:
        self.steps = steps or Steps()
        self.hosts: list[str] = []
        self.fail = False

    async def add_host(self, host: str) -> None:
        self.steps.calls.append("cognito")
        if self.fail:
            raise RuntimeError("cognito is unhappy")
        self.hosts.append(host)


class FakeRecorder:
    def __init__(self) -> None:
        self.events: list[audit.Event] = []

    def record(self, event: audit.Event) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [event.event for event in self.events]


class Clock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def spec(**overrides) -> creation.CreateSpec:
    body = {
        "key": "tarpeyo",
        "label": "Tarpeyo Uptake",
        "description": "Uptake curves.",
        "cpu": 512,
        "memory": 2048,
        "upload_key": UPLOAD,
        "access_mode": "users",
        "allowed_emails": [CREATOR],
        "idle_minutes": 20,
        "max_session_hours": 12,
        "expires_at": None,
        "packages": ["shiny", "ggplot2"],
    }
    body.update(overrides)
    return creation.validate_create(body)


def provisioner_for(
    *, store=None, repositories=None, roles=None, builds=None, services=None,
    clients=None, recorder=None, steps=None, clock=None,
):
    steps = steps or Steps()
    return provision.Provisioner(
        store=store if store is not None else FakeStore(),
        repositories=repositories or FakeRepositories(steps),
        roles=roles or FakeRoles(steps),
        builds=builds or FakeBuilds(steps),
        services=services or FakeServices(steps),
        clients=clients or FakeClients(steps),
        recorder=recorder or FakeRecorder(),
        domain=DOMAIN,
        clock=clock or Clock(),
    )


# --- create: the order -----------------------------------------------------


@pytest.mark.asyncio
async def test_creating_reserves_the_row_then_ecr_then_iam_then_the_build():
    steps = Steps()
    store = FakeStore()
    builds = FakeBuilds(steps)
    handler = provisioner_for(store=store, builds=builds, steps=steps)

    created = await handler.create(spec(), CREATOR)

    assert steps.calls == ["ecr:shiny-tarpeyo", "iam:tarpeyo", "codebuild:tarpeyo"]
    # The hostname is `<key>-<suffix>`; the resource names are not.
    assert SUFFIXED_HOST.match(store.reserved[0].host)
    assert store.reserved[0].app_key == "tarpeyo"
    assert store.reserved[0].ecs_service == "shiny-tarpeyo"
    assert created.status == registry.STATUS_BUILDING
    assert created.build_id == "shiny-app-build:abc-123"
    assert created.image.endswith("/shiny-tarpeyo:r1")


@pytest.mark.asyncio
async def test_the_reserved_row_carries_the_whole_wizard_answer():
    store = FakeStore()
    await provisioner_for(store=store).create(
        spec(expires_at=1_900_000_000), CREATOR
    )

    row = store.reserved[0]
    assert row.app_key == "tarpeyo"
    assert row.ecs_service == "shiny-tarpeyo"
    assert row.container_port == 3838
    assert row.label == "Tarpeyo Uptake"
    assert row.access_mode == "users"
    assert row.allowed_emails == (CREATOR,)
    assert row.idle_minutes == 20
    assert row.max_session_hours == 12
    assert row.expires_at == 1_900_000_000
    assert row.cpu == 512 and row.memory == 2048
    assert row.packages == ("shiny", "ggplot2")
    assert row.upload_key == UPLOAD
    assert row.release_tag == "r1"
    assert row.created_by == CREATOR
    assert row.created_at == int(NOW)


@pytest.mark.asyncio
async def test_the_build_gets_the_env_overrides_the_buildspec_reads():
    builds = FakeBuilds()
    await provisioner_for(builds=builds).create(spec(), CREATOR)
    assert builds.started == [
        {
            "app_key": "tarpeyo",
            "zip_key": UPLOAD,
            "release_tag": "r1",
            # The repository ECR just made, no tag. render.py cross-checks it
            # against APP_KEY, so a build that got the wrong one would ship
            # this app's image into another app's repository.
            "repository_uri": "652063276768.dkr.ecr.us-east-1.amazonaws.com/shiny-tarpeyo",
            "packages": "shiny ggplot2",
        }
    ]


@pytest.mark.asyncio
async def test_the_repository_the_build_pushes_to_is_the_one_ecr_just_made():
    """ECR is ensured before the build starts for exactly this reason: the
    per-app repository URI is a REQUIRED build variable, so it has to be in
    hand by StartBuild or the build dies in pre_build."""
    steps = Steps()
    builds = FakeBuilds(steps)
    created = await provisioner_for(builds=builds, steps=steps).create(spec(), CREATOR)

    assert steps.calls.index("ecr:shiny-tarpeyo") < steps.calls.index("codebuild:tarpeyo")
    assert created.image == builds.started[0]["repository_uri"] + ":r1"


@pytest.mark.asyncio
async def test_a_slug_collision_provisions_nothing_at_all():
    """The conditional put is step one for exactly this reason: the loser of
    a race has created no ECR repository and no IAM role to clean up."""
    steps = Steps()
    store = FakeStore()
    store.taken_keys.add("tarpeyo")
    handler = provisioner_for(store=store, steps=steps)

    with pytest.raises(registry.HostTaken):
        await handler.create(spec(), CREATOR)

    assert steps.calls == []
    assert store.patches == []


# --- create: the hostname ---------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_hostname_carries_the_random_suffix():
    """The key stays human-readable everywhere a human reads it: the ECR
    repository, the task role and the ECS service are all `shiny-tarpeyo`,
    and only the address in the browser is unguessable."""
    steps = Steps()
    store = FakeStore()
    created = await provisioner_for(store=store, steps=steps).create(spec(), CREATOR)

    assert SUFFIXED_HOST.match(created.host)
    assert created.app_key == "tarpeyo"
    assert created.ecs_service == "shiny-tarpeyo"
    assert steps.calls == ["ecr:shiny-tarpeyo", "iam:tarpeyo", "codebuild:tarpeyo"]
    assert created.image.endswith("/shiny-tarpeyo:r1")


@pytest.mark.asyncio
async def test_creating_the_same_key_twice_gives_two_different_hostnames():
    """Which is exactly why `_claim_key` exists: the conditional put no
    longer collides, so the duplicate key has to be caught another way."""
    hosts = set()
    for _ in range(5):
        store = FakeStore()
        created = await provisioner_for(store=store).create(spec(), CREATOR)
        hosts.add(created.host)
    assert len(hosts) == 5


@pytest.mark.asyncio
async def test_the_cognito_callback_is_registered_for_the_suffixed_host():
    """The callback URL has to be the address the browser will actually come
    back to, or the app's first sign-in fails with an invalid redirect."""
    store = FakeStore()
    clients = FakeClients()
    handler = provisioner_for(store=store, clients=clients)

    created = await handler.create(spec(), CREATOR)
    await handler.finish(store.created())

    assert clients.hosts == [created.host]
    assert SUFFIXED_HOST.match(clients.hosts[0])


# --- create: two wizards, one key -------------------------------------------


def rival_row(created_at: int, host: str) -> App:
    """Another row already holding the key `tarpeyo`."""
    return App.create(
        host=host,
        app_key="tarpeyo",
        ecs_service="shiny-tarpeyo",
        status=registry.STATUS_BUILDING,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_a_second_app_with_the_same_key_is_refused_and_provisions_nothing():
    """Before the suffix, the conditional put settled this. It cannot any
    more -- two rows for `tarpeyo` no longer share a partition key -- so the
    duplicate is caught immediately after the reserve, still before the
    first AWS call. Two apps called `shiny-tarpeyo` would share one ECR
    repository and one ECS service, and the second build would overwrite the
    first app's image at the same tag."""
    steps = Steps()
    store = FakeStore()
    # A row that was created a minute earlier: it was unambiguously first.
    store.rows["tarpeyo-aaaaaa.tools.stratevi.com"] = rival_row(
        int(NOW) - 60, "tarpeyo-aaaaaa.tools.stratevi.com"
    )

    with pytest.raises(registry.HostTaken):
        await provisioner_for(store=store, steps=steps).create(spec(), CREATOR)

    assert steps.calls == []


@pytest.mark.asyncio
async def test_the_loser_of_a_key_race_leaves_a_row_that_says_why():
    store = FakeStore()
    recorder = FakeRecorder()
    store.rows["tarpeyo-aaaaaa.tools.stratevi.com"] = rival_row(
        int(NOW) - 60, "tarpeyo-aaaaaa.tools.stratevi.com"
    )

    with pytest.raises(registry.HostTaken):
        await provisioner_for(store=store, recorder=recorder).create(spec(), CREATOR)

    row = store.created()
    assert row.status == registry.STATUS_BUILD_FAILED
    assert "already uses the key tarpeyo" in row.build_error
    assert recorder.names() == [
        audit.EVENT_APP_CREATED,
        audit.EVENT_PROVISION_FAILED,
    ]


@pytest.mark.asyncio
async def test_the_earlier_of_two_racers_is_the_one_that_survives():
    """The tie-break is (created_at, host), computed from rows every racer
    can see -- so exactly one wins, rather than both standing down."""
    store = FakeStore()
    # The rival was created LATER, so we were first and carry on.
    store.rows["tarpeyo-zzzzzz.tools.stratevi.com"] = rival_row(
        int(NOW) + 60, "tarpeyo-zzzzzz.tools.stratevi.com"
    )

    created = await provisioner_for(store=store).create(spec(), CREATOR)
    assert created.status == registry.STATUS_BUILDING


@pytest.mark.asyncio
async def test_a_same_second_tie_is_broken_by_the_hostname_so_someone_wins():
    """Two wizards a millisecond apart share a `created_at`. Falling back to
    the hostname keeps the verdict deterministic and the same for both."""
    losses = 0
    for rival_host in ("tarpeyo-aaaaaa.tools.stratevi.com",
                       "tarpeyo-zzzzzz.tools.stratevi.com"):
        store = FakeStore()
        store.rows[rival_host] = rival_row(int(NOW), rival_host)
        try:
            await provisioner_for(store=store).create(spec(), CREATOR)
        except registry.HostTaken:
            losses += 1
    # Whichever way the random suffix fell, the comparison decided rather
    # than refusing both or allowing both.
    assert losses in (0, 1, 2)


@pytest.mark.asyncio
async def test_a_table_that_cannot_be_read_does_not_refuse_a_legitimate_create():
    """`_key_problem` and the wizard both already checked. An unreadable
    table is not evidence of a collision, and failing here would turn a
    DynamoDB blip into "that name is taken" for a name that is free."""
    class Unreadable(FakeStore):
        async def apps(self):
            raise RuntimeError("dynamodb is unhappy")

    created = await provisioner_for(store=Unreadable()).create(spec(), CREATOR)
    assert created.status == registry.STATUS_BUILDING


# --- create: the failure rule ----------------------------------------------


@pytest.mark.parametrize("broken", ["ecr", "iam", "codebuild"])
@pytest.mark.asyncio
async def test_a_failure_after_the_row_exists_marks_it_build_failed(broken):
    steps = Steps()
    store = FakeStore()
    recorder = FakeRecorder()
    handler = provisioner_for(
        store=store,
        steps=steps,
        recorder=recorder,
        repositories=FakeRepositories(steps, fail=broken == "ecr"),
        roles=FakeRoles(steps, fail=broken == "iam"),
        builds=_failing_builds(steps, broken == "codebuild"),
    )

    with pytest.raises(provision.ProvisionError):
        await handler.create(spec(), CREATOR)

    row = store.created()
    assert row.status == registry.STATUS_BUILD_FAILED
    assert "provisioning failed" in row.build_error
    assert recorder.names() == [
        audit.EVENT_APP_CREATED,
        audit.EVENT_PROVISION_FAILED,
    ]


@pytest.mark.asyncio
async def test_a_failure_leaves_the_row_and_the_half_built_resources_alone():
    """portal-p2a.md: leave them for inspection rather than thrashing. A
    retry reuses them; an explicit delete cleans them up (P2b)."""
    steps = Steps()
    store = FakeStore()
    handler = provisioner_for(
        store=store, steps=steps, builds=_failing_builds(steps, True)
    )

    with pytest.raises(provision.ProvisionError):
        await handler.create(spec(), CREATOR)

    # The ECR repository and the IAM role were both made and nothing undid
    # them; the row is still there, recording what happened.
    assert steps.calls == ["ecr:shiny-tarpeyo", "iam:tarpeyo", "codebuild:tarpeyo"]
    assert store.created().status == registry.STATUS_BUILD_FAILED


@pytest.mark.asyncio
async def test_the_creator_is_on_every_creation_event():
    recorder = FakeRecorder()
    await provisioner_for(recorder=recorder).create(spec(), CREATOR)
    assert recorder.names() == [audit.EVENT_APP_CREATED, audit.EVENT_BUILD_STARTED]
    assert {event.email for event in recorder.events} == {CREATOR}
    assert SUFFIXED_HOST.match(recorder.events[0].host)


@pytest.mark.asyncio
async def test_creation_events_are_never_deduplicated():
    """Only `allow` is collapsed. Two creation attempts are two attempts."""
    recorder = audit.Recorder(clock=lambda: NOW)
    for name in (
        audit.EVENT_APP_CREATED,
        audit.EVENT_BUILD_STARTED,
        audit.EVENT_BUILD_SUCCEEDED,
        audit.EVENT_BUILD_FAILED,
        audit.EVENT_PROVISION_FAILED,
    ):
        assert name != audit.EVENT_ALLOW
        for _ in range(2):
            recorder.record(
                audit.Event(host="tarpeyo.tools.stratevi.com", event=name, email=CREATOR)
            )
    assert recorder._queue.qsize() == 10


def _failing_builds(steps: Steps, fail: bool) -> FakeBuilds:
    builds = FakeBuilds(steps)
    builds.fail_start = fail
    return builds


# --- finish ----------------------------------------------------------------


def building_row(**overrides) -> App:
    defaults = dict(
        host="tarpeyo.tools.stratevi.com",
        app_key="tarpeyo",
        label="Tarpeyo Uptake",
        ecs_service="shiny-tarpeyo",
        status=registry.STATUS_BUILDING,
        access_mode="users",
        allowed_emails=[CREATOR],
        cpu=512,
        memory=2048,
        image="1.dkr.ecr.us-east-1.amazonaws.com/shiny-tarpeyo:r1",
        build_id="shiny-app-build:abc-123",
        build_started_at=int(NOW - 60),
        created_by=CREATOR,
        created_at=int(NOW - 120),
    )
    defaults.update(overrides)
    return App.create(**defaults)


@pytest.mark.asyncio
async def test_a_successful_build_registers_creates_at_zero_and_activates():
    steps = Steps()
    store = FakeStore([building_row()])
    services = FakeServices(steps)
    clients = FakeClients(steps)
    recorder = FakeRecorder()
    handler = provisioner_for(
        store=store, services=services, clients=clients, recorder=recorder, steps=steps
    )

    await handler.finish(store.row())

    assert steps.calls == ["taskdef", "service", "cognito"]
    assert clients.hosts == ["tarpeyo.tools.stratevi.com"]
    assert store.row().status == registry.STATUS_ACTIVE
    assert recorder.names() == [audit.EVENT_BUILD_SUCCEEDED]


@pytest.mark.parametrize("broken", ["taskdef", "service", "cognito"])
@pytest.mark.asyncio
async def test_a_failure_while_finishing_is_a_provision_failure(broken):
    steps = Steps()
    store = FakeStore([building_row()])
    services = FakeServices(steps)
    services.fail_register = broken == "taskdef"
    services.fail_create = broken == "service"
    clients = FakeClients(steps)
    clients.fail = broken == "cognito"
    recorder = FakeRecorder()

    handler = provisioner_for(
        store=store, services=services, clients=clients, recorder=recorder, steps=steps
    )
    await handler.finish(store.row())

    assert store.row().status == registry.STATUS_BUILD_FAILED
    assert recorder.names() == [audit.EVENT_PROVISION_FAILED]


@pytest.mark.asyncio
async def test_cognito_is_touched_last_because_it_is_shared_state():
    """Everything private to the app is proven to work before the one call
    that mutates the client every other app signs in through."""
    steps = Steps()
    services = FakeServices(steps)
    services.fail_create = True
    clients = FakeClients(steps)
    store = FakeStore([building_row()])

    await provisioner_for(
        store=store, services=services, clients=clients, steps=steps
    ).finish(store.row())

    assert clients.hosts == []
    assert "cognito" not in steps.calls


@pytest.mark.asyncio
async def test_an_unwritable_row_after_provisioning_stays_building_for_a_retry():
    """Everything AWS-side is built; only the row disagrees. Leaving it in
    `building` lets the next sweep retry, and every step is idempotent."""
    store = FakeStore([building_row()])
    store.fail_patch = True
    recorder = FakeRecorder()

    await provisioner_for(store=store, recorder=recorder).finish(store.row())

    assert store.row().status == registry.STATUS_BUILDING
    assert recorder.names() == []


@pytest.mark.asyncio
async def test_fail_never_raises_even_when_the_row_cannot_be_written():
    store = FakeStore([building_row()])
    store.fail_patch = True
    recorder = FakeRecorder()

    await provisioner_for(store=store, recorder=recorder).fail(store.row(), "boom")

    assert recorder.names() == [audit.EVENT_BUILD_FAILED]


# --- the build watcher: nothing stays "building" forever -------------------


def watcher_for(store, builds, *, stuck_after=provision.STUCK_BUILD_SECONDS):
    handler = provisioner_for(store=store, builds=builds)
    return handler, provision.BuildWatcher(
        provisioner=handler, builds=builds, stuck_after=stuck_after
    )


@pytest.mark.asyncio
async def test_a_build_still_running_is_left_alone():
    store = FakeStore([building_row()])
    builds = FakeBuilds()
    _, watcher = watcher_for(store, builds)

    await watcher.sweep(store.row(), NOW)

    assert store.row().status == registry.STATUS_BUILDING
    assert store.patches == []


@pytest.mark.asyncio
async def test_a_build_running_past_forty_five_minutes_is_reaped():
    store = FakeStore([building_row(build_started_at=int(NOW - 46 * 60))])
    builds = FakeBuilds()
    recorder = FakeRecorder()
    handler = provisioner_for(store=store, builds=builds, recorder=recorder)
    watcher = provision.BuildWatcher(provisioner=handler, builds=builds)

    await watcher.sweep(store.row(), NOW)

    assert store.row().status == registry.STATUS_BUILD_FAILED
    assert "45 minutes" in store.row().build_error
    assert recorder.names() == [audit.EVENT_BUILD_FAILED]


@pytest.mark.asyncio
async def test_a_row_with_no_build_id_is_reaped_rather_than_left_forever():
    """StartBuild never landed. Nothing else will ever move this row."""
    store = FakeStore(
        [building_row(build_id="", build_started_at=0, created_at=int(NOW - 46 * 60))]
    )
    builds = FakeBuilds()
    recorder = FakeRecorder()
    handler = provisioner_for(store=store, builds=builds, recorder=recorder)
    watcher = provision.BuildWatcher(provisioner=handler, builds=builds)

    await watcher.sweep(store.row(), NOW)

    assert store.row().status == registry.STATUS_BUILD_FAILED
    assert "no build was ever started" in store.row().build_error
    assert recorder.names() == [audit.EVENT_PROVISION_FAILED]


@pytest.mark.asyncio
async def test_a_row_with_no_build_id_but_still_young_is_given_time():
    store = FakeStore([building_row(build_id="", build_started_at=0)])
    builds = FakeBuilds()
    _, watcher = watcher_for(store, builds)

    await watcher.sweep(store.row(), NOW)

    assert store.row().status == registry.STATUS_BUILDING


@pytest.mark.asyncio
async def test_a_succeeded_build_is_finished_into_a_live_app():
    store = FakeStore([building_row()])
    builds = FakeBuilds()
    builds.state = provision.BuildStatus(state="SUCCEEDED", phase="COMPLETED")
    _, watcher = watcher_for(store, builds)

    await watcher.sweep(store.row(), NOW)

    assert store.row().status == registry.STATUS_ACTIVE


@pytest.mark.parametrize("state", ["FAILED", "FAULT", "TIMED_OUT", "STOPPED"])
@pytest.mark.asyncio
async def test_every_unhappy_codebuild_state_fails_the_row(state):
    store = FakeStore([building_row()])
    builds = FakeBuilds()
    builds.state = provision.BuildStatus(state=state, phase="INSTALL")
    recorder = FakeRecorder()
    handler = provisioner_for(store=store, builds=builds, recorder=recorder)
    watcher = provision.BuildWatcher(provisioner=handler, builds=builds)

    await watcher.sweep(store.row(), NOW)

    assert store.row().status == registry.STATUS_BUILD_FAILED
    assert state.lower() in store.row().build_error
    assert "INSTALL" in store.row().build_error
    assert recorder.names() == [audit.EVENT_BUILD_FAILED]


@pytest.mark.asyncio
async def test_an_unreadable_build_waits_then_gives_up_at_the_deadline():
    builds = FakeBuilds()
    builds.fail_status = True

    young = FakeStore([building_row()])
    _, watcher = watcher_for(young, builds)
    await watcher.sweep(young.row(), NOW)
    assert young.row().status == registry.STATUS_BUILDING

    old = FakeStore([building_row(build_started_at=int(NOW - 46 * 60))])
    handler = provisioner_for(store=old, builds=builds)
    await provision.BuildWatcher(provisioner=handler, builds=builds).sweep(
        old.row(), NOW
    )
    assert old.row().status == registry.STATUS_BUILD_FAILED
    assert "could not be checked" in old.row().build_error


def test_the_stuck_deadline_is_the_specs_forty_five_minutes():
    assert provision.STUCK_BUILD_SECONDS == 45 * 60


# --- the permissions boundary ----------------------------------------------


class FakeIam:
    """Just enough IAM to exercise the invariant."""

    def __init__(self, *, boundary=BOUNDARY, exists=False) -> None:
        self.created: list[dict] = []
        self.policies: list[dict] = []
        self.deleted: list[str] = []
        self._boundary = boundary  # what get_role will REPORT
        self._exists = exists

    def create_role(self, **kwargs):
        if self._exists:
            raise _client_error("EntityAlreadyExists")
        self.created.append(kwargs)
        return {"Role": {"Arn": f"arn:aws:iam::1:role/{kwargs['RoleName']}"}}

    def get_role(self, RoleName):
        role = {"Arn": f"arn:aws:iam::1:role/{RoleName}", "RoleName": RoleName}
        if self._boundary is not None:
            role["PermissionsBoundary"] = {
                "PermissionsBoundaryType": "Policy",
                "PermissionsBoundaryArn": self._boundary,
            }
        return {"Role": role}

    def put_role_policy(self, **kwargs):
        self.policies.append(kwargs)
        return {}

    def delete_role(self, RoleName):
        self.deleted.append(RoleName)
        return {}


def _client_error(code: str) -> Exception:
    error = RuntimeError(code)
    error.response = {"Error": {"Code": code}}  # type: ignore[attr-defined]
    return error


def roles_for(client, **overrides):
    kwargs = {"boundary_arn": BOUNDARY, "data_bucket": "shiny-app-data-1"}
    kwargs.update(overrides)
    return provision.Boto3TaskRoles(client, **kwargs)


@pytest.mark.parametrize("boundary", ["", "   ", None])
def test_a_role_maker_cannot_be_constructed_without_a_boundary(boundary):
    """Invariant 1. There is no object that creates roles unfenced."""
    with pytest.raises(ValueError, match="permissions boundary"):
        roles_for(FakeIam(), boundary_arn=boundary)


def test_ensure_takes_no_boundary_argument_for_a_caller_to_omit():
    """Invariant 2. The boundary is bound at construction; the create call
    has no parameter to pass wrongly, pass as None, or forget."""
    import inspect

    parameters = inspect.signature(provision.Boto3TaskRoles.ensure).parameters
    assert list(parameters) == ["self", "app_key"]


@pytest.mark.asyncio
async def test_every_created_role_carries_the_boundary():
    iam = FakeIam()
    arn = await roles_for(iam).ensure("tarpeyo")

    assert len(iam.created) == 1
    assert iam.created[0]["RoleName"] == "shiny-app-tarpeyo-task"
    assert iam.created[0]["PermissionsBoundary"] == BOUNDARY
    assert arn.endswith("role/shiny-app-tarpeyo-task")


@pytest.mark.asyncio
async def test_a_role_that_came_back_without_the_boundary_is_deleted_not_used():
    """Invariant 3. Even an IAM that accepted the call and ignored the
    parameter cannot leave an unbounded shiny-app-* role in the account."""
    iam = FakeIam(boundary=None)
    with pytest.raises(provision.ProvisionError, match="permissions boundary"):
        await roles_for(iam).ensure("tarpeyo")

    assert iam.deleted == ["shiny-app-tarpeyo-task"]
    assert iam.policies == []  # nothing was granted to it


@pytest.mark.asyncio
async def test_a_role_carrying_someone_elses_boundary_is_also_refused():
    iam = FakeIam(boundary="arn:aws:iam::1:policy/something-else")
    with pytest.raises(provision.ProvisionError):
        await roles_for(iam).ensure("tarpeyo")
    assert iam.deleted == ["shiny-app-tarpeyo-task"]


@pytest.mark.asyncio
async def test_an_existing_role_is_reused_only_if_it_is_inside_the_boundary():
    inside = FakeIam(exists=True)
    assert await roles_for(inside).ensure("tarpeyo")
    assert inside.created == []  # reused, per "a retry reuses them"

    outside = FakeIam(exists=True, boundary=None)
    with pytest.raises(provision.ProvisionError):
        await roles_for(outside).ensure("tarpeyo")


@pytest.mark.asyncio
async def test_the_inline_policy_stays_inside_what_the_boundary_permits():
    iam = FakeIam()
    await roles_for(iam).ensure("tarpeyo")

    document = json.loads(iam.policies[0]["PolicyDocument"])
    actions = {
        action
        for statement in document["Statement"]
        for action in statement["Action"]
    }
    assert actions == {
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "s3:GetObject",
        "s3:ListBucket",
    }
    # And only its OWN prefix of the shared data bucket.
    data = [s for s in document["Statement"] if s["Sid"] == "OwnData"][0]
    assert data["Resource"] == [
        "arn:aws:s3:::shiny-app-data-1",
        "arn:aws:s3:::shiny-app-data-1/tarpeyo/*",
    ]


def test_the_role_name_is_the_specs_shape():
    assert roles_for(FakeIam()).role_name("tarpeyo") == "shiny-app-tarpeyo-task"


# --- Cognito: the single most dangerous call in P2a ------------------------

#: A realistic DescribeUserPoolClient response for the shared client, matching
#: proxy/cognito.tf. Every one of these fields must survive an update.
DESCRIBED = {
    "UserPoolId": "us-east-1_hub",
    "ClientName": "shiny-proxy",
    "ClientId": "sharedclientid",
    "ClientSecret": "shhh",
    "LastModifiedDate": "2026-09-09",
    "CreationDate": "2026-09-01",
    "RefreshTokenValidity": 12,
    "AccessTokenValidity": 1,
    "IdTokenValidity": 1,
    "TokenValidityUnits": {
        "AccessToken": "hours",
        "IdToken": "hours",
        "RefreshToken": "hours",
    },
    "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"],
    "SupportedIdentityProviders": ["COGNITO", "StrateviEntra"],
    "CallbackURLs": [
        "https://dashboard.tools.stratevi.com/oauth2/idpresponse",
        "https://shinyplatform.tools.stratevi.com/oauth2/idpresponse",
    ],
    "LogoutURLs": [
        "https://dashboard.tools.stratevi.com",
        "https://shinyplatform.tools.stratevi.com",
    ],
    "AllowedOAuthFlows": ["code"],
    "AllowedOAuthScopes": ["openid", "email", "profile"],
    "AllowedOAuthFlowsUserPoolClient": True,
    "PreventUserExistenceErrors": "ENABLED",
    "EnableTokenRevocation": True,
    "AuthSessionValidity": 3,
}

NEW_HOST = "tarpeyo.tools.stratevi.com"


def test_the_merge_carries_every_describable_field_forward():
    """UpdateUserPoolClient is a REPLACE. Anything not resent is reset to a
    default, and for this client that means every app stops signing anyone
    in -- silently, with a 200 from the API."""
    merged = provision.merged_client_config(
        DESCRIBED,
        callback=provision.callback_url(NEW_HOST),
        logout=provision.logout_url(NEW_HOST),
    )

    for name, value in DESCRIBED.items():
        if name in provision.DESCRIBE_ONLY_FIELDS:
            continue
        if name in ("CallbackURLs", "LogoutURLs"):
            continue
        assert merged[name] == value, f"{name} did not survive the merge"


def test_the_merge_drops_only_the_fields_update_cannot_accept():
    merged = provision.merged_client_config(DESCRIBED, callback="https://a/x", logout="https://a")
    assert set(DESCRIBED) - set(merged) == set(provision.DESCRIBE_ONLY_FIELDS)
    assert "ClientSecret" not in merged
    assert merged["UserPoolId"] == "us-east-1_hub"
    assert merged["ClientId"] == "sharedclientid"


def test_the_new_urls_are_appended_and_the_existing_ones_keep_their_order():
    merged = provision.merged_client_config(
        DESCRIBED,
        callback=provision.callback_url(NEW_HOST),
        logout=provision.logout_url(NEW_HOST),
    )
    assert merged["CallbackURLs"] == DESCRIBED["CallbackURLs"] + [
        "https://tarpeyo.tools.stratevi.com/oauth2/idpresponse"
    ]
    assert merged["LogoutURLs"] == DESCRIBED["LogoutURLs"] + [
        "https://tarpeyo.tools.stratevi.com"
    ]


def test_merging_a_url_that_is_already_there_changes_nothing():
    merged = provision.merged_client_config(
        DESCRIBED,
        callback=DESCRIBED["CallbackURLs"][0],
        logout=DESCRIBED["LogoutURLs"][0],
    )
    assert merged["CallbackURLs"] == DESCRIBED["CallbackURLs"]
    assert merged["LogoutURLs"] == DESCRIBED["LogoutURLs"]


def test_the_hundred_url_ceiling_is_a_legible_error_not_a_cognito_400():
    crowded = dict(
        DESCRIBED,
        CallbackURLs=[f"https://app{n}.tools.stratevi.com/oauth2/idpresponse"
                      for n in range(provision.MAX_CALLBACK_URLS)],
    )
    with pytest.raises(provision.ProvisionError, match="callback URLs"):
        provision.merged_client_config(
            crowded, callback=provision.callback_url(NEW_HOST), logout="https://x"
        )


class FakeCognito:
    def __init__(self, described=None, *, drops=()) -> None:
        self.client = dict(described or DESCRIBED)
        self.updates: list[dict] = []
        #: URLs this fake "loses" on write, simulating the exact silent
        #: failure the verification exists to catch.
        self._drops = set(drops)

    def describe_user_pool_client(self, UserPoolId, ClientId):
        return {"UserPoolClient": dict(self.client)}

    def update_user_pool_client(self, **kwargs):
        self.updates.append(kwargs)
        stored = dict(kwargs)
        stored["CallbackURLs"] = [
            url for url in kwargs.get("CallbackURLs", ()) if url not in self._drops
        ]
        stored["ClientSecret"] = self.client.get("ClientSecret", "")
        self.client = stored
        return {"UserPoolClient": stored}


def clients_for(cognito):
    return provision.Boto3Clients(
        cognito, user_pool_id="us-east-1_hub", client_id="sharedclientid"
    )


@pytest.mark.asyncio
async def test_adding_a_host_preserves_every_existing_callback_and_setting():
    cognito = FakeCognito()
    await clients_for(cognito).add_host(NEW_HOST)

    sent = cognito.updates[0]
    for url in DESCRIBED["CallbackURLs"]:
        assert url in sent["CallbackURLs"]
    assert sent["AllowedOAuthScopes"] == ["openid", "email", "profile"]
    assert sent["SupportedIdentityProviders"] == ["COGNITO", "StrateviEntra"]
    assert sent["ExplicitAuthFlows"] == DESCRIBED["ExplicitAuthFlows"]
    assert sent["TokenValidityUnits"] == DESCRIBED["TokenValidityUnits"]
    assert "ClientSecret" not in sent


@pytest.mark.asyncio
async def test_adding_a_host_twice_does_not_rewrite_the_client():
    cognito = FakeCognito()
    handler = clients_for(cognito)
    await handler.add_host(NEW_HOST)
    await handler.add_host(NEW_HOST)
    assert len(cognito.updates) == 1


@pytest.mark.asyncio
async def test_a_silent_wipe_is_caught_by_the_read_back():
    """The whole risk of this call is that it returns 200 and quietly drops
    something. A 200 is not evidence."""
    cognito = FakeCognito(drops={DESCRIBED["CallbackURLs"][0]})
    with pytest.raises(provision.ProvisionError, match="not updated correctly"):
        await clients_for(cognito).add_host(NEW_HOST)


@pytest.mark.asyncio
async def test_a_new_callback_that_never_landed_is_caught_too():
    cognito = FakeCognito(
        drops={"https://tarpeyo.tools.stratevi.com/oauth2/idpresponse"}
    )
    with pytest.raises(provision.ProvisionError, match="not updated correctly"):
        await clients_for(cognito).add_host(NEW_HOST)


def test_the_callback_and_logout_urls_match_the_alb_convention():
    assert provision.callback_url(NEW_HOST) == (
        "https://tarpeyo.tools.stratevi.com/oauth2/idpresponse"
    )
    assert provision.logout_url(NEW_HOST) == "https://tarpeyo.tools.stratevi.com"


# --- ECR, ECS, S3 ----------------------------------------------------------


class FakeEcr:
    def __init__(self, *, exists=False) -> None:
        self.created: list[dict] = []
        self.lifecycles: list[dict] = []
        self._exists = exists

    def create_repository(self, **kwargs):
        if self._exists:
            raise _client_error("RepositoryAlreadyExistsException")
        self.created.append(kwargs)
        return {"repository": {"repositoryUri": f"1.dkr.ecr.x/{kwargs['repositoryName']}"}}

    def describe_repositories(self, repositoryNames):
        return {"repositories": [{"repositoryUri": f"1.dkr.ecr.x/{repositoryNames[0]}"}]}

    def put_lifecycle_policy(self, **kwargs):
        self.lifecycles.append(kwargs)
        return {}


@pytest.mark.asyncio
async def test_a_repository_is_created_with_a_keep_five_lifecycle():
    ecr = FakeEcr()
    uri = await provision.Boto3Repositories(ecr).ensure("shiny-tarpeyo")

    assert uri == "1.dkr.ecr.x/shiny-tarpeyo"
    policy = json.loads(ecr.lifecycles[0]["lifecyclePolicyText"])
    rule = policy["rules"][0]
    assert rule["selection"]["countNumber"] == 5
    assert rule["action"]["type"] == "expire"


@pytest.mark.asyncio
async def test_an_existing_repository_is_reused_rather_than_failing_a_retry():
    ecr = FakeEcr(exists=True)
    assert await provision.Boto3Repositories(ecr).ensure("shiny-tarpeyo")
    assert ecr.created == []
    assert ecr.lifecycles  # still (re)applied


class FakeEcs:
    def __init__(self) -> None:
        self.task_definitions: list[dict] = []
        self.services: list[dict] = []
        self.exists = False

    def register_task_definition(self, **kwargs):
        self.task_definitions.append(kwargs)
        return {"taskDefinition": {"taskDefinitionArn": "arn:task-def/1"}}

    def create_service(self, **kwargs):
        if self.exists:
            raise _client_error("ServiceAlreadyExistsException")
        self.services.append(kwargs)
        return {}


def services_for(ecs):
    return provision.Boto3Services(
        ecs,
        cluster="shiny-cluster",
        subnets=["subnet-1", "subnet-2"],
        security_group="sg-apps",
        execution_role_arn="arn:role/exec",
        log_group="/ecs/shiny/apps",
        region="us-east-1",
        roles=roles_for(FakeIam()),
    )


@pytest.mark.asyncio
async def test_the_task_definition_sets_shiny_cpu_workers_from_the_task_size():
    """ADR-0011: detectCores() reports the Fargate HOST's cores."""
    ecs = FakeEcs()
    await services_for(ecs).register_task_definition(
        building_row(cpu=4096, memory=16384)
    )

    container = ecs.task_definitions[0]["containerDefinitions"][0]
    workers = {e["name"]: e["value"] for e in container["environment"]}
    assert workers["SHINY_CPU_WORKERS"] == "3"
    assert ecs.task_definitions[0]["cpu"] == "4096"
    assert ecs.task_definitions[0]["memory"] == "16384"
    assert ecs.task_definitions[0]["taskRoleArn"] == "shiny-app-tarpeyo-task"
    assert container["portMappings"][0]["containerPort"] == 3838


@pytest.mark.asyncio
async def test_a_created_service_starts_asleep():
    """Nobody has asked for this app yet; the proxy wakes it on the first
    request exactly as it wakes every other app."""
    ecs = FakeEcs()
    await services_for(ecs).create_service(
        service="shiny-tarpeyo", task_definition="arn:task-def/1"
    )

    created = ecs.services[0]
    assert created["desiredCount"] == 0
    assert created["launchType"] == "FARGATE"
    network = created["networkConfiguration"]["awsvpcConfiguration"]
    assert network["subnets"] == ["subnet-1", "subnet-2"]
    assert network["securityGroups"] == ["sg-apps"]
    # No NAT Gateway (ADR-0004): a task needs a public IP to pull its image.
    assert network["assignPublicIp"] == "ENABLED"
    # Born proxied: no load balancer, no target group, no waker.
    assert "loadBalancers" not in created


@pytest.mark.asyncio
async def test_creating_a_service_that_already_exists_is_a_retry_not_a_failure():
    ecs = FakeEcs()
    ecs.exists = True
    await services_for(ecs).create_service(
        service="shiny-tarpeyo", task_definition="arn:task-def/1"
    )  # does not raise


class FakeS3:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.calls.append(
            {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn}
        )
        # Shaped like a real SigV4 presign, not a placeholder: presign()
        # refuses a URL that is not SigV4 (a SigV2 one 403s in browsers --
        # see the signature-version tests at the end of this module), so a
        # stub "?sig=1" would fail for the right reason and teach nothing.
        return (
            "https://s3.example/put"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-SignedHeaders=host&sig=1"
        )


def test_a_presigned_put_is_scoped_to_one_key_for_fifteen_minutes():
    s3 = FakeS3()
    uploads = provision.Boto3Uploads(s3, "shiny-portal-uploads-1")

    key = uploads.new_key()
    assert creation.UPLOAD_KEY_PATTERN.match(key), key
    assert uploads.presign(key).startswith("https://s3.example/put?")

    call = s3.calls[0]
    assert call["operation"] == "put_object"
    assert call["Params"] == {"Bucket": "shiny-portal-uploads-1", "Key": key}
    assert call["ExpiresIn"] == 900 == provision.UPLOAD_URL_SECONDS


def test_every_upload_key_is_a_fresh_uuid():
    uploads = provision.Boto3Uploads(FakeS3(), "b")
    keys = {uploads.new_key() for _ in range(20)}
    assert len(keys) == 20


# --- the cost tag ----------------------------------------------------------
#
# Every resource the portal creates must carry `Project=shiny`, and the reason
# is money, not tidiness: platform/monitoring.tf's $75 budget and its alerts
# filter on `user:Project$shiny`. An ECR repository, an app task role or --
# most expensively -- a Fargate service created without that tag spends
# OUTSIDE the budget, so the overspend is invisible until the bill arrives.
#
# This is exactly the kind of thing that reads as cosmetic in review and gets
# dropped, so it is asserted per resource rather than trusted to the helper.


def _tags(pairs) -> dict[str, str]:
    """A tag list in either AWS spelling ('Key'/'Value' or 'key'/'value')."""
    return {
        (tag.get("Key") or tag["key"]): (tag.get("Value") or tag["value"])
        for tag in pairs
    }


@pytest.mark.asyncio
async def test_a_created_repository_carries_the_project_cost_tag():
    ecr = FakeEcr()
    await provision.Boto3Repositories(ecr).ensure("shiny-tarpeyo")
    assert _tags(ecr.created[0]["tags"]) == {
        "ManagedBy": "shiny-portal",
        "Project": "shiny",
    }


@pytest.mark.asyncio
async def test_a_created_task_role_carries_the_project_cost_tag():
    iam = FakeIam()
    await roles_for(iam).ensure("tarpeyo")
    assert _tags(iam.created[0]["Tags"]) == {
        "ManagedBy": "shiny-portal",
        "Project": "shiny",
        "AppKey": "tarpeyo",  # unchanged; the cost tag is added alongside it
    }


@pytest.mark.asyncio
async def test_a_created_service_carries_the_project_cost_tag():
    """The one that actually burns Fargate minutes."""
    ecs = FakeEcs()
    await services_for(ecs).create_service(
        service="shiny-tarpeyo", task_definition="arn:task-def/1"
    )
    # ECS spells its tag keys lowercase, unlike IAM and ECR.
    assert _tags(ecs.services[0]["tags"]) == {
        "ManagedBy": "shiny-portal",
        "Project": "shiny",
    }


def test_the_project_tag_follows_the_terraform_prefix_rather_than_a_literal():
    assert provision.portal_tags("other") == [
        {"Key": "ManagedBy", "Value": "other-portal"},
        {"Key": "Project", "Value": "other"},
    ]
    assert provision.portal_tags_ecs("other") == [
        {"key": "ManagedBy", "value": "other-portal"},
        {"key": "Project", "value": "other"},
    ]


# --- CodeBuild decoding ----------------------------------------------------


class _Started:
    def timestamp(self):
        return NOW


def test_a_build_description_decodes_into_what_the_screen_needs():
    status = provision.build_status(
        {
            "buildStatus": "IN_PROGRESS",
            "currentPhase": "BUILD",
            "startTime": _Started(),
            "logs": {
                "groupName": "/aws/codebuild/shiny-app-build",
                "streamName": "abc-123",
                "deepLink": "https://console/log",
            },
        }
    )
    assert status.state == "IN_PROGRESS"
    assert status.phase == "BUILD"
    assert status.started_at == int(NOW)
    assert status.log_url == "https://console/log"
    assert status.finished() is False
    assert status.succeeded() is False


def test_a_finished_build_reports_finished():
    assert provision.build_status({"buildStatus": "SUCCEEDED"}).succeeded() is True
    assert provision.build_status({"buildStatus": "FAILED"}).finished() is True
    # An empty description is not "finished" -- it is "we do not know".
    assert provision.build_status({}).finished() is False


class FakeCodeBuild:
    def __init__(self) -> None:
        self.started: list[dict] = []

    def start_build(self, **kwargs):
        self.started.append(kwargs)
        return {"build": {"id": "shiny-app-build:abc-123"}}

    def batch_get_builds(self, ids):
        return {"builds": [{"buildStatus": "SUCCEEDED", "currentPhase": "COMPLETED"}]}


class FakeLogs:
    def __init__(self, events=None, fail=False) -> None:
        self.events = events or [{"message": "one\n"}, {"message": "two"}]
        self.fail = fail

    def get_log_events(self, **kwargs):
        if self.fail:
            raise RuntimeError("logs are unhappy")
        return {"events": self.events}


REPO_URI = "652063276768.dkr.ecr.us-east-1.amazonaws.com/shiny-tarpeyo"


@pytest.mark.asyncio
async def test_start_build_sends_the_documented_env_overrides():
    codebuild = FakeCodeBuild()
    builds = provision.Boto3Builds(codebuild, FakeLogs(), "shiny-app-build")

    build_id = await builds.start(
        app_key="tarpeyo",
        zip_key=UPLOAD,
        release_tag="r1",
        repository_uri=REPO_URI,
        packages="shiny ggplot2",
    )

    assert build_id == "shiny-app-build:abc-123"
    sent = codebuild.started[0]
    assert sent["projectName"] == "shiny-app-build"
    overrides = {e["name"]: e["value"] for e in sent["environmentVariablesOverride"]}
    # Pinned exactly, not with `<=`. An override that quietly disappears is a
    # build that dies in pre_build for every app at once.
    assert overrides == {
        "APP_KEY": "tarpeyo",
        "ZIP_KEY": UPLOAD,
        "RELEASE_TAG": "r1",
        "ECR_REPO_URI": REPO_URI,
        "PACKAGES": "shiny ggplot2",
    }
    assert [e["name"] for e in sent["environmentVariablesOverride"]] == list(
        provision.BUILD_ENV_OVERRIDES
    )
    assert all(
        e["type"] == "PLAINTEXT" for e in sent["environmentVariablesOverride"]
    )


def _render_module():
    """``buildspec/render.py``, loaded by path.

    It is not importable as a package member -- it is a standalone script that
    runs inside the CodeBuild container -- but its ``REQUIRED`` tuple IS the
    contract this module has to satisfy, so the test reads the real thing
    rather than a copy of it.
    """
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "buildspec" / "render.py"
    spec_ = importlib.util.spec_from_file_location("_buildspec_render", path)
    assert spec_ and spec_.loader
    module = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(module)
    return module


def test_the_overrides_plus_the_project_statics_are_the_whole_build_contract():
    """The reconciliation, against the file that enforces it.

    ``render.py`` fails the build in pre_build naming any REQUIRED variable
    that is unset. Every one of them must therefore be either an override
    sent here or a static value on the CodeBuild project. The statics are
    ``UPLOADS_BUCKET`` (one bucket for every app) and ``PIPELINE_PREFIX``
    (optional, not in REQUIRED), both set in ``proxy/codebuild.tf``.
    """
    required = set(_render_module().REQUIRED)

    assert set(provision.BUILD_ENV_OVERRIDES) <= required
    assert required - set(provision.BUILD_ENV_OVERRIDES) == set(
        provision.BUILD_ENV_PROJECT_STATIC
    )
    # ECR_REPO_URI specifically: per-app, so it CANNOT be a project static.
    assert "ECR_REPO_URI" in provision.BUILD_ENV_OVERRIDES


@pytest.mark.asyncio
async def test_a_vanished_build_is_an_error_not_an_empty_status():
    class Empty(FakeCodeBuild):
        def batch_get_builds(self, ids):
            return {"builds": []}

    builds = provision.Boto3Builds(Empty(), FakeLogs(), "shiny-app-build")
    with pytest.raises(provision.ProvisionError, match="no longer exists"):
        await builds.status("gone")


@pytest.mark.asyncio
async def test_the_log_tail_is_best_effort():
    builds = provision.Boto3Builds(FakeCodeBuild(), FakeLogs(fail=True), "p")
    status = provision.BuildStatus(log_group="g", log_stream="s")
    assert await builds.log_tail(status, 10) == []

    good = provision.Boto3Builds(FakeCodeBuild(), FakeLogs(), "p")
    assert await good.log_tail(status, 10) == ["one", "two"]
    # No stream yet (the build has not reached a phase that logs).
    assert await good.log_tail(provision.BuildStatus(), 10) == []


# --- presigned upload: signature version ----------------------------------
#
# Regression guard for a bug that only reproduces in a browser. boto3's
# default for an S3 presign against the global endpoint is legacy SigV2,
# which folds Content-Type into the string-to-sign. A browser always sends
# a Content-Type for a File; the presigner signed an empty one; S3 answers
# 403 SignatureDoesNotMatch. curl sends no Content-Type, so the same URL
# tests fine from a terminal and the fault looks like it is in the UI.
#
# Verified against the real bucket on 2026-09-10: SigV2 + Content-Type gave
# 403 with `StringToSign: PUT\n\napplication/x-zip-compressed...`; SigV4 with
# the identical header gave 200.


def test_presign_rejects_a_client_that_is_not_signing_with_sigv4():
    class V2Client:
        def generate_presigned_url(self, *_a, **_k):
            return (
                "https://b.s3.amazonaws.com/uploads/x.zip"
                "?AWSAccessKeyId=AKIA&Signature=abc&Expires=1"
            )

    uploads = provision.Boto3Uploads(V2Client(), "b")
    with pytest.raises(provision.ProvisionError, match="SigV4"):
        uploads.presign("uploads/x.zip")


def test_presign_accepts_a_sigv4_url():
    class V4Client:
        def generate_presigned_url(self, *_a, **_k):
            return (
                "https://b.s3.amazonaws.com/uploads/x.zip"
                "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-SignedHeaders=host"
            )

    uploads = provision.Boto3Uploads(V4Client(), "b")
    assert "AWS4-HMAC-SHA256" in uploads.presign("uploads/x.zip")


def test_the_real_boto3_client_we_build_signs_with_sigv4():
    """The default config is the bug; assert the explicit one is in use."""
    import boto3
    from botocore.config import Config as BotoConfig

    session = boto3.session.Session(
        aws_access_key_id="AKIAtest",
        aws_secret_access_key="secret",
        region_name="us-east-1",
    )
    client = session.client("s3", config=BotoConfig(signature_version="s3v4"))
    url = provision.Boto3Uploads(client, "bucket").presign("uploads/x.zip")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    # Only `host` signed: whatever Content-Type the browser picks is fine.
    assert "X-Amz-SignedHeaders=host" in url
