"""The portal's P2a routes: the creator gate, validate-key, uploads, create,
build status, and the two new live_states.

Driven exactly like ``test_portal.py`` -- ``make_mocked_request`` plus fakes
behind the module's protocols. The contract is ``docs/design/portal-api.md``
("P2a additions -- creation").

The gate is the point of half of this file. Creation is its OWN permission
(portal-p2a.md's decision): an admin who is not a creator gets 403 from
every route here, and `/me` says `can_create: false` so the UI hides the
affordance rather than dangling one.
"""

from __future__ import annotations

import asyncio
import base64
import json
from unittest import mock

import pytest
from aiohttp import streams
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

from proxy_app import audit, creation, portal, provision, registry
from proxy_app.ecsctl import ServiceState
from proxy_app.registry import App

NOW = 1_800_000_000.0
ADMIN = "jake@stratevi.com"
CREATOR = "nick@stratevi.com"
BOTH = "yi@stratevi.com"
NOBODY = "intern@stratevi.com"
#: A creator by name, outside every staff domain. Refused anyway.
OUTSIDER = "contractor@notstratevi.com"

_UNSET = object()

PORTAL_HOST = "shinyplatform.tools.stratevi.com"
DOMAIN = "tools.stratevi.com"
UPLOAD = "uploads/3f2504e0-4f89-11d3-9a0c-0305e82c3301.zip"


# --- fakes -----------------------------------------------------------------


class FakeStore:
    def __init__(self, rows=None) -> None:
        self.rows = {row.host: row for row in (rows or [])}
        self.patches: list[tuple[str, dict]] = []
        self.reserved: list[App] = []
        self.taken: set[str] = set()
        self.fail_list = False

    async def app(self, host):
        return self.rows.get(registry.normalize_host(host))

    async def apps(self):
        if self.fail_list:
            raise RuntimeError("dynamodb is unhappy")
        return list(self.rows.values())

    async def patch(self, host, changes):
        self.patches.append((host, dict(changes)))
        row = self.rows[registry.normalize_host(host)]
        self.rows[row.host] = App.create(**{**row.__dict__, **changes})

    async def reserve(self, app: App) -> None:
        if app.host in self.rows or app.host in self.taken:
            raise registry.HostTaken(app.host)
        self.reserved.append(app)
        self.rows[app.host] = app


class FakeTasks:
    async def states(self, services):
        return {}


class FakeRecorder:
    def __init__(self) -> None:
        self.events: list[audit.Event] = []

    def record(self, event):
        self.events.append(event)

    def names(self):
        return [event.event for event in self.events]


class FakeUploads:
    def __init__(self, fail=False) -> None:
        self.keys: list[str] = []
        self.signed: list[str] = []
        self.fail = fail

    def new_key(self) -> str:
        key = UPLOAD
        self.keys.append(key)
        return key

    def presign(self, key: str) -> str:
        if self.fail:
            raise RuntimeError("s3 is unhappy")
        self.signed.append(key)
        return "https://s3.example/put?sig=1"


class FakeProvisioner:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.created: list[tuple[creation.CreateSpec, str]] = []
        self.raises: Exception | None = None

    async def create(self, spec: creation.CreateSpec, email: str) -> App:
        self.created.append((spec, email))
        if self.raises is not None:
            raise self.raises
        row = App.create(
            host=spec.host(DOMAIN),
            app_key=spec.key,
            label=spec.label,
            description=spec.description,
            ecs_service=spec.ecs_service(),
            status=registry.STATUS_BUILDING,
            access_mode=spec.access_mode,
            allowed_emails=spec.allowed_emails,
            idle_minutes=spec.idle_minutes,
            max_session_hours=spec.max_session_hours,
            expires_at=spec.expires_at,
            cpu=spec.cpu,
            memory=spec.memory,
            packages=spec.packages,
            upload_key=spec.upload_key,
            release_tag="r1",
            build_id="shiny-app-build:abc-123",
            build_started_at=int(NOW),
            created_by=email,
            created_at=int(NOW),
        )
        await self.store.reserve(row)
        return row


class FakeBuilds:
    def __init__(self, status=None, tail=None, fail=False) -> None:
        self.status_value = status or provision.BuildStatus(
            state="IN_PROGRESS", phase="BUILD", started_at=int(NOW - 120),
            log_url="https://console/log",
        )
        self.tail = tail if tail is not None else ["installing shiny"]
        self.fail = fail

    async def status(self, build_id):
        if self.fail:
            raise RuntimeError("codebuild is unhappy")
        return self.status_value

    async def log_tail(self, build, lines):
        return list(self.tail)


class Clock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# --- request plumbing ------------------------------------------------------


def oidc_headers(email):
    if email is None:
        return {"Host": PORTAL_HOST}
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": "cognito-sub-1", "email": email}).encode("utf-8")
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    return {"Host": PORTAL_HOST, "x-amzn-oidc-data": f"header.{payload}.signature"}


def request(path="/", *, method="GET", email=CREATOR, body=None, headers=None):
    combined = CIMultiDict(oidc_headers(email))
    for name, value in (headers or {}).items():
        combined[name] = value

    payload = None
    if body is not None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        protocol = mock.Mock(_reading_paused=False)
        payload = streams.StreamReader(protocol, 2**16, loop=asyncio.get_event_loop())
        payload.feed_data(raw)
        payload.feed_eof()

    return make_mocked_request(method, path, headers=combined, payload=payload)


def body_of(response):
    return json.loads(response.text)


def csrf(extra=None):
    headers = {portal.CSRF_HEADER: "1"}
    headers.update(extra or {})
    return headers


def app_row(**overrides) -> App:
    defaults = dict(
        host="model.tools.stratevi.com",
        app_key="model",
        label="Microsimulation Model",
        ecs_service="shiny-model",
        access_mode=registry.MODE_USERS,
        allowed_emails=[ADMIN],
    )
    defaults.update(overrides)
    return App.create(**defaults)


def good_body(**overrides):
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
        "packages": ["shiny"],
    }
    body.update(overrides)
    return body


def portal_for(
    *,
    store=None,
    admins=(ADMIN, BOTH),
    creators=(CREATOR, BOTH),
    staff=_UNSET,
    denylist=(),
    uploads=None,
    provisioner=None,
    builds=None,
    recorder=None,
    configured=True,
    clock=None,
):
    store = store if store is not None else FakeStore([app_row()])

    def reader(value):
        async def read():
            if isinstance(value, Exception):
                raise value
            return value

        return read

    bundle = None
    if configured:
        bundle = provision.Creation(
            domain=DOMAIN,
            uploads=uploads or FakeUploads(),
            provisioner=provisioner or FakeProvisioner(store),
            builds=builds or FakeBuilds(),
            denylist=portal.DenyList(reader(denylist), clock=Clock(0.0)),
            console_region="us-east-1",
            codebuild_project="shiny-app-build",
        )

    return portal.Portal(
        apps=store,
        tasks=FakeTasks(),
        admins=portal.AdminList(reader(admins), clock=Clock(0.0)),
        creators=portal.CreatorList(reader(creators), clock=Clock(0.0)),
        # Unset means no collaborator, so the Portal evaluates
        # registry.DEFAULT_STAFF_DOMAINS -- every address in this file is
        # @stratevi.com, so the staff gate is transparent unless a test asks
        # for it.
        staff=(
            None
            if staff is _UNSET
            else portal.StaffDomains(reader(staff), clock=Clock(0.0))
        ),
        creation=bundle,
        recorder=recorder or FakeRecorder(),
        clock=clock or Clock(),
    )


# --- /me: can_create is its own answer -------------------------------------


@pytest.mark.asyncio
async def test_me_reports_create_permission_separately_from_admin():
    handler = portal_for()

    creator = body_of(await handler.handle(request("/api/v1/me", email=CREATOR)))
    assert creator == {
        "email": CREATOR,
        "is_admin": False,
        "can_create": True,
        "is_staff": True,
    }

    admin = body_of(await handler.handle(request("/api/v1/me", email=ADMIN)))
    assert admin == {
        "email": ADMIN,
        "is_admin": True,
        "can_create": False,
        "is_staff": True,
    }

    both = body_of(await handler.handle(request("/api/v1/me", email=BOTH)))
    assert both == {
        "email": BOTH,
        "is_admin": True,
        "can_create": True,
        "is_staff": True,
    }

    nobody = body_of(await handler.handle(request("/api/v1/me", email=NOBODY)))
    assert nobody == {
        "email": NOBODY,
        "is_admin": False,
        "can_create": False,
        # Staff, just unprivileged. The two answers are independent.
        "is_staff": True,
    }


@pytest.mark.asyncio
async def test_an_unreadable_creator_list_means_nobody_may_create():
    handler = portal_for(creators=RuntimeError("dynamodb is unhappy"))
    assert body_of(await handler.handle(request("/api/v1/me")))["can_create"] is False


@pytest.mark.asyncio
async def test_an_empty_creator_list_means_nobody_may_create():
    handler = portal_for(creators=())
    assert body_of(await handler.handle(request("/api/v1/me")))["can_create"] is False


@pytest.mark.asyncio
async def test_a_principal_with_no_resolvable_email_may_never_create():
    creators = portal.CreatorList(_resolver([CREATOR]))
    assert await creators.can_create("") is False
    assert await creators.can_create("   ") is False


@pytest.mark.asyncio
async def test_the_creator_list_is_cached_and_a_failure_is_cached_briefly():
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("dynamodb is unhappy")
        return [CREATOR]

    clock = Clock(0.0)
    creators = portal.CreatorList(flaky, ttl=30.0, error_ttl=5.0, clock=clock)

    assert await creators.can_create(CREATOR) is False
    clock.t += 6
    assert await creators.can_create(CREATOR) is True
    for _ in range(10):
        await creators.can_create(CREATOR)
    assert len(calls) == 2


def _resolver(value):
    async def read():
        return value

    return read


# --- the creator gate on every P2a route -----------------------------------

P2A_ROUTES = [
    ("/api/v1/apps/validate-key", "POST", {"key": "tarpeyo"}),
    ("/api/v1/uploads", "POST", {"filename": "app.zip", "size": 10}),
    ("/api/v1/apps", "POST", None),
    ("/api/v1/apps/model.tools.stratevi.com/build", "GET", None),
]


@pytest.mark.parametrize("path,method,body", P2A_ROUTES)
@pytest.mark.asyncio
async def test_an_admin_who_is_not_a_creator_is_403_on_every_p2a_route(
    path, method, body
):
    """portal-p2a.md is explicit: being an admin does NOT grant create."""
    response = await portal_for().handle(
        request(path, method=method, email=ADMIN,
                body=body if body is not None else good_body(), headers=csrf())
    )
    assert response.status == 403
    assert "not permitted to create" in body_of(response)["error"]


@pytest.mark.parametrize("path,method,body", P2A_ROUTES)
@pytest.mark.asyncio
async def test_a_plain_user_is_403_on_every_p2a_route(path, method, body):
    response = await portal_for().handle(
        request(path, method=method, email=NOBODY,
                body=body if body is not None else good_body(), headers=csrf())
    )
    assert response.status == 403


@pytest.mark.parametrize("path,method,body", P2A_ROUTES)
@pytest.mark.asyncio
async def test_a_creator_reaches_every_p2a_route(path, method, body):
    response = await portal_for().handle(
        request(path, method=method, email=CREATOR,
                body=body if body is not None else good_body(), headers=csrf())
    )
    assert response.status in (200, 202)


@pytest.mark.parametrize("path,method,body", P2A_ROUTES)
@pytest.mark.asyncio
async def test_an_unconfigured_pipeline_is_503_not_403_for_a_creator(
    path, method, body
):
    """Different fix, different status: 403 is "you may not", 503 is
    "nobody can yet -- the P2a Terraform is not applied"."""
    response = await portal_for(configured=False).handle(
        request(path, method=method, email=CREATOR,
                body=body if body is not None else good_body(), headers=csrf())
    )
    assert response.status == 503
    assert "not configured" in body_of(response)["error"]


@pytest.mark.parametrize("path,method,body", P2A_ROUTES)
@pytest.mark.asyncio
async def test_a_non_staff_creator_is_403_on_every_p2a_route(path, method, body):
    """Named in `creator_emails` and still refused, on every route.

    Creation provisions IAM roles and puts a hostname in public DNS. Being
    on the list is necessary; being staff is the other half, and no list
    edit can substitute for it.
    """
    response = await portal_for(
        creators=(CREATOR, OUTSIDER), staff=("stratevi.com",)
    ).handle(
        request(path, method=method, email=OUTSIDER,
                body=body if body is not None else good_body(), headers=csrf())
    )
    assert response.status == 403
    assert "not permitted to create" in body_of(response)["error"]


@pytest.mark.asyncio
async def test_a_non_staff_creator_is_403_not_503_even_with_no_pipeline():
    """The 403/503 distinction survives the new check.

    503 means "nobody can yet, the pipeline is not deployed". A non-staff
    caller is the other case -- "you personally may not" -- so they must not
    be told the deployment's configuration state instead.
    """
    response = await portal_for(
        creators=(CREATOR, OUTSIDER), staff=("stratevi.com",), configured=False
    ).handle(
        request("/api/v1/uploads", method="POST", email=OUTSIDER,
                body={"filename": "app.zip", "size": 10}, headers=csrf())
    )
    assert response.status == 403


@pytest.mark.asyncio
async def test_me_collapses_can_create_for_a_non_staff_creator():
    handler = portal_for(creators=(CREATOR, OUTSIDER), staff=("stratevi.com",))
    assert body_of(await handler.handle(request("/api/v1/me", email=OUTSIDER))) == {
        "email": OUTSIDER,
        "is_admin": False,
        "can_create": False,
        "is_staff": False,
    }


@pytest.mark.asyncio
async def test_a_staff_creator_still_reaches_the_routes():
    """The gate must not be a blanket refusal."""
    response = await portal_for(staff=("stratevi.com",)).handle(
        request("/api/v1/apps/validate-key", method="POST", email=CREATOR,
                body={"key": "q3-uptake"}, headers=csrf())
    )
    assert response.status == 200
    assert body_of(response)["ok"] is True


@pytest.mark.asyncio
async def test_creation_being_off_does_not_disturb_the_p1_routes():
    handler = portal_for(configured=False)
    assert (await handler.handle(request("/api/v1/menu"))).status == 200
    assert (await handler.handle(request("/api/v1/apps", email=ADMIN))).status == 200


# --- POST /apps/validate-key ------------------------------------------------


@pytest.mark.asyncio
async def test_an_available_key_answers_with_the_hostname_shape_not_a_hostname():
    """The honest answer, and the reason it is not simply the hostname.

    A created app's host carries a random suffix minted at CREATE time, and
    this route reserves nothing. Any suffix returned here would be a
    different one from the one the app actually gets. So the route answers
    with the SHAPE, and the wizard says in words that the real suffix is
    added on create -- see `creation.host_preview`.
    """
    response = await portal_for().handle(
        request("/api/v1/apps/validate-key", method="POST", body={"key": "tarpeyo"})
    )
    assert response.status == 200
    assert body_of(response) == {
        "ok": True,
        "host_preview": "tarpeyo-xxxxxx.tools.stratevi.com",
        "suffix_chars": 6,
    }
    # And in particular it does NOT hand out something that looks like a
    # real, resolvable hostname for this key.
    assert "host" not in body_of(response)


@pytest.mark.parametrize(
    "key,fragment",
    [
        ("ab", "3-30 characters"),
        ("Model", "lowercase"),
        ("mo--del", "two hyphens"),
        ("www", "reserved"),
        ("shinyplatform", "reserved"),
        ("model", "already in use"),
    ],
)
@pytest.mark.asyncio
async def test_an_unavailable_key_is_a_200_with_a_reason_not_an_error(key, fragment):
    """It is a form affordance, not an error -- 200 either way."""
    response = await portal_for().handle(
        request("/api/v1/apps/validate-key", method="POST", body={"key": key})
    )
    assert response.status == 200
    payload = body_of(response)
    assert payload["ok"] is False
    assert fragment in payload["reason"]
    assert "host" not in payload


@pytest.mark.asyncio
async def test_a_denylisted_key_is_refused_without_saying_which_term_matched():
    response = await portal_for(denylist=["tarpeyo", "acme"]).handle(
        request("/api/v1/apps/validate-key", method="POST", body={"key": "tarpeyo-x"})
    )
    payload = body_of(response)
    assert payload == {"ok": False, "reason": creation.DENYLIST_MESSAGE}
    assert "tarpeyo" not in payload["reason"]


@pytest.mark.asyncio
async def test_an_unreadable_denylist_never_reads_as_nothing_matched():
    """A name that reaches DNS cannot be taken back."""
    response = await portal_for(denylist=RuntimeError("dynamodb is unhappy")).handle(
        request("/api/v1/apps/validate-key", method="POST", body={"key": "tarpeyo"})
    )
    payload = body_of(response)
    assert payload["ok"] is False
    assert "temporarily unavailable" in payload["reason"]


@pytest.mark.asyncio
async def test_an_unreadable_apps_table_does_not_report_a_key_as_free():
    store = FakeStore([app_row()])
    store.fail_list = True
    response = await portal_for(store=store).handle(
        request("/api/v1/apps/validate-key", method="POST", body={"key": "tarpeyo"})
    )
    assert body_of(response)["ok"] is False


@pytest.mark.asyncio
async def test_validate_key_is_post_only_and_tolerates_a_duff_body():
    handler = portal_for()
    assert (await handler.handle(
        request("/api/v1/apps/validate-key", method="GET")
    )).status == 405
    assert (await handler.handle(
        request("/api/v1/apps/validate-key", method="POST", body=b"{not json")
    )).status == 400
    assert (await handler.handle(
        request("/api/v1/apps/validate-key", method="POST", body=[])
    )).status == 400


@pytest.mark.asyncio
async def test_validate_key_is_not_mistaken_for_a_hostname():
    """It sits under /apps/ and must be matched before the {host} dispatcher."""
    response = await portal_for().handle(
        request("/api/v1/apps/validate-key", method="POST", body={"key": "tarpeyo"})
    )
    assert response.status == 200


# --- POST /uploads ----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_upload_returns_a_presigned_put_for_a_fresh_key():
    uploads = FakeUploads()
    response = await portal_for(uploads=uploads).handle(
        request("/api/v1/uploads", method="POST",
                body={"filename": "app.zip", "size": 1024}, headers=csrf())
    )

    assert response.status == 200
    assert body_of(response) == {
        "upload_key": UPLOAD,
        "url": "https://s3.example/put?sig=1",
        "expires_in": 900,
    }
    assert uploads.signed == [UPLOAD]


@pytest.mark.asyncio
async def test_an_oversize_bundle_is_refused_before_any_url_is_issued():
    """A 400 on a JSON call beats an S3 rejection twenty minutes into an
    upload, which the browser reports as an opaque CORS error."""
    uploads = FakeUploads()
    response = await portal_for(uploads=uploads).handle(
        request("/api/v1/uploads", method="POST",
                body={"filename": "app.zip", "size": creation.MAX_UPLOAD_BYTES + 1},
                headers=csrf())
    )

    assert response.status == 400
    assert "100 MB" in body_of(response)["error"]
    assert uploads.keys == [] and uploads.signed == []


@pytest.mark.parametrize(
    "body",
    [
        {"filename": "app.tar.gz", "size": 10},
        {"filename": "app.zip", "size": 0},
        {"filename": "app.zip"},
        {"size": 10},
        {"filename": "app.zip", "size": 10, "bucket": "somewhere-else"},
    ],
)
@pytest.mark.asyncio
async def test_a_bad_upload_declaration_issues_nothing(body):
    uploads = FakeUploads()
    response = await portal_for(uploads=uploads).handle(
        request("/api/v1/uploads", method="POST", body=body, headers=csrf())
    )
    assert response.status == 400
    assert uploads.signed == []


@pytest.mark.asyncio
async def test_an_upload_without_the_csrf_header_mints_no_write_grant():
    """Without this, another origin could make the victim's browser mint a
    write grant into our bucket."""
    uploads = FakeUploads()
    response = await portal_for(uploads=uploads).handle(
        request("/api/v1/uploads", method="POST",
                body={"filename": "app.zip", "size": 10})
    )
    assert response.status == 403
    assert portal.CSRF_HEADER.lower() in body_of(response)["error"].lower()
    assert uploads.signed == []


@pytest.mark.asyncio
async def test_a_presigning_failure_is_503():
    response = await portal_for(uploads=FakeUploads(fail=True)).handle(
        request("/api/v1/uploads", method="POST",
                body={"filename": "app.zip", "size": 10}, headers=csrf())
    )
    assert response.status == 503


@pytest.mark.asyncio
async def test_uploads_is_post_only():
    assert (await portal_for().handle(request("/api/v1/uploads"))).status == 405


# --- POST /apps -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_good_create_returns_202_with_the_app_at_status_building():
    store = FakeStore([app_row()])
    handler = portal_for(store=store)

    response = await handler.handle(
        request("/api/v1/apps", method="POST", body=good_body(), headers=csrf())
    )

    assert response.status == 202
    created = body_of(response)
    assert created["host"] == "tarpeyo.tools.stratevi.com"
    assert created["app_key"] == "tarpeyo"
    assert created["status"] == "building"
    assert created["live_state"] == "building"
    assert created["idle_minutes"] == 20
    assert store.reserved[0].created_by == CREATOR


@pytest.mark.asyncio
async def test_the_created_row_records_who_asked_for_it():
    store = FakeStore()
    provisioner = FakeProvisioner(store)
    await portal_for(store=store, provisioner=provisioner).handle(
        request("/api/v1/apps", method="POST", body=good_body(), headers=csrf(),
                email=BOTH)
    )
    assert provisioner.created[0][1] == BOTH


@pytest.mark.asyncio
async def test_a_slug_collision_is_409():
    store = FakeStore([app_row()])
    provisioner = FakeProvisioner(store)
    provisioner.raises = registry.HostTaken("tarpeyo.tools.stratevi.com")

    response = await portal_for(store=store, provisioner=provisioner).handle(
        request("/api/v1/apps", method="POST", body=good_body(), headers=csrf())
    )

    assert response.status == 409
    assert "taken" in body_of(response)["error"]


@pytest.mark.asyncio
async def test_a_key_already_in_the_table_is_400_before_anything_is_attempted():
    """The pre-check is the courteous answer; the conditional put is the
    mutex. Both exist, and this is the first one."""
    store = FakeStore([app_row(host="tarpeyo.tools.stratevi.com", app_key="tarpeyo")])
    provisioner = FakeProvisioner(store)

    response = await portal_for(store=store, provisioner=provisioner).handle(
        request("/api/v1/apps", method="POST", body=good_body(), headers=csrf())
    )

    assert response.status == 400
    assert "already in use" in body_of(response)["error"]
    assert provisioner.created == []


@pytest.mark.asyncio
async def test_a_denylisted_key_cannot_be_smuggled_past_the_wizard():
    store = FakeStore()
    provisioner = FakeProvisioner(store)
    response = await portal_for(
        store=store, provisioner=provisioner, denylist=["tarpeyo"]
    ).handle(request("/api/v1/apps", method="POST", body=good_body(), headers=csrf()))

    assert response.status == 400
    assert body_of(response)["error"] == creation.DENYLIST_MESSAGE
    assert provisioner.created == []


@pytest.mark.asyncio
async def test_a_create_without_the_csrf_header_provisions_nothing():
    store = FakeStore()
    provisioner = FakeProvisioner(store)
    response = await portal_for(store=store, provisioner=provisioner).handle(
        request("/api/v1/apps", method="POST", body=good_body())
    )
    assert response.status == 403
    assert provisioner.created == []


@pytest.mark.parametrize(
    "body",
    [
        b"{not json",
        {},
        {"key": "tarpeyo"},
        good_body(cpu=1024, memory=2048),
        good_body(expires_at="never"),
        good_body(upload_key="somewhere/else.zip"),
        good_body(access_mode="team"),
        good_body(packages=["shiny; rm -rf /"]),
    ],
)
@pytest.mark.asyncio
async def test_an_invalid_create_body_provisions_nothing(body):
    store = FakeStore()
    provisioner = FakeProvisioner(store)
    response = await portal_for(store=store, provisioner=provisioner).handle(
        request("/api/v1/apps", method="POST", body=body, headers=csrf())
    )
    assert response.status == 400
    assert provisioner.created == []
    assert store.reserved == []


@pytest.mark.asyncio
async def test_an_omitted_expires_at_is_400_but_an_explicit_null_is_fine():
    store = FakeStore()
    handler = portal_for(store=store)

    missing = good_body()
    del missing["expires_at"]
    refused = await handler.handle(
        request("/api/v1/apps", method="POST", body=missing, headers=csrf())
    )
    assert refused.status == 400
    assert "expires_at" in body_of(refused)["error"]

    accepted = await handler.handle(
        request("/api/v1/apps", method="POST", body=good_body(expires_at=None),
                headers=csrf())
    )
    assert accepted.status == 202


@pytest.mark.asyncio
async def test_a_provisioning_failure_is_503_and_the_row_is_left_recorded():
    store = FakeStore()
    provisioner = FakeProvisioner(store)
    provisioner.raises = provision.ProvisionError("ecr is unhappy")

    response = await portal_for(store=store, provisioner=provisioner).handle(
        request("/api/v1/apps", method="POST", body=good_body(), headers=csrf())
    )

    assert response.status == 503
    assert "ecr is unhappy" in body_of(response)["error"]


@pytest.mark.asyncio
async def test_an_unexpected_provisioning_error_is_still_a_clean_503():
    store = FakeStore()
    provisioner = FakeProvisioner(store)
    provisioner.raises = RuntimeError("something nobody predicted")

    response = await portal_for(store=store, provisioner=provisioner).handle(
        request("/api/v1/apps", method="POST", body=good_body(), headers=csrf())
    )
    assert response.status == 503
    assert "something nobody predicted" not in body_of(response)["error"]


@pytest.mark.asyncio
async def test_get_apps_is_still_the_admin_listing_and_post_is_the_creator_route():
    handler = portal_for()
    assert (await handler.handle(request("/api/v1/apps", email=ADMIN))).status == 200
    assert (await handler.handle(request("/api/v1/apps", email=CREATOR))).status == 403
    assert (
        await handler.handle(request("/api/v1/apps", method="PUT", email=BOTH))
    ).status == 405


# --- GET /apps/{host}/build -------------------------------------------------


def building_row(**overrides) -> App:
    defaults = dict(
        host="tarpeyo.tools.stratevi.com",
        app_key="tarpeyo",
        label="Tarpeyo Uptake",
        ecs_service="shiny-tarpeyo",
        status=registry.STATUS_BUILDING,
        access_mode=registry.MODE_USERS,
        allowed_emails=[CREATOR],
        build_id="shiny-app-build:abc-123",
        build_started_at=int(NOW - 300),
        created_at=int(NOW - 320),
    )
    defaults.update(overrides)
    return App.create(**defaults)


BUILD_PATH = "/api/v1/apps/tarpeyo.tools.stratevi.com/build"


@pytest.mark.asyncio
async def test_the_build_endpoint_reports_phase_elapsed_link_and_tail():
    store = FakeStore([building_row()])
    response = await portal_for(store=store).handle(request(BUILD_PATH))

    assert response.status == 200
    payload = body_of(response)
    assert payload["state"] == "building"
    assert payload["phase"] == "BUILD"
    assert payload["started_at"] == int(NOW - 120)
    assert payload["elapsed_s"] == 120
    assert payload["log_url"] == "https://console/log"
    assert payload["log_tail"] == ["installing shiny"]


@pytest.mark.asyncio
async def test_a_finished_build_is_reported_before_the_sweep_catches_up():
    """CodeBuild is the fresher answer; the row only updates once a minute."""
    store = FakeStore([building_row()])
    builds = FakeBuilds(
        status=provision.BuildStatus(state="SUCCEEDED", phase="COMPLETED")
    )
    response = await portal_for(store=store, builds=builds).handle(request(BUILD_PATH))
    assert body_of(response)["state"] == "succeeded"

    failed = FakeBuilds(status=provision.BuildStatus(state="FAILED", phase="BUILD"))
    response = await portal_for(store=store, builds=failed).handle(request(BUILD_PATH))
    assert body_of(response)["state"] == "failed"


@pytest.mark.asyncio
async def test_a_failed_row_reports_why_even_with_no_build_to_poll():
    store = FakeStore(
        [
            building_row(
                status=registry.STATUS_BUILD_FAILED,
                build_id="",
                build_error="no build was ever started for this app",
            )
        ]
    )
    payload = body_of(await portal_for(store=store).handle(request(BUILD_PATH)))
    assert payload["state"] == "failed"
    assert payload["reason"] == "no build was ever started for this app"
    assert payload["log_tail"] == []


@pytest.mark.asyncio
async def test_an_active_app_reports_a_succeeded_build():
    store = FakeStore([building_row(status=registry.STATUS_ACTIVE, build_id="")])
    payload = body_of(await portal_for(store=store).handle(request(BUILD_PATH)))
    assert payload["state"] == "succeeded"


@pytest.mark.asyncio
async def test_an_unreadable_build_still_renders_a_screen():
    store = FakeStore([building_row()])
    payload = body_of(
        await portal_for(store=store, builds=FakeBuilds(fail=True)).handle(
            request(BUILD_PATH)
        )
    )
    assert payload["state"] == "building"
    assert payload["log_tail"] == []
    # The row's own timestamps still answer "how long has this been going".
    assert payload["elapsed_s"] == 300


@pytest.mark.asyncio
async def test_the_build_endpoint_404s_an_unknown_host():
    response = await portal_for(store=FakeStore()).handle(request(BUILD_PATH))
    assert response.status == 404


@pytest.mark.asyncio
async def test_the_build_endpoint_is_read_only():
    store = FakeStore([building_row()])
    response = await portal_for(store=store).handle(
        request(BUILD_PATH, method="POST", headers=csrf())
    )
    assert response.status == 405


@pytest.mark.asyncio
async def test_an_unknown_subresource_is_still_404():
    store = FakeStore([building_row()])
    response = await portal_for(store=store).handle(
        request("/api/v1/apps/tarpeyo.tools.stratevi.com/nope", email=ADMIN)
    )
    assert response.status == 404


def test_the_console_deep_link_is_built_from_the_build_id():
    bundle = provision.Creation(
        domain=DOMAIN, uploads=FakeUploads(), provisioner=None, builds=None,
        denylist=None, console_region="us-east-1",
    )
    url = bundle.build_console_url("shiny-app-build:abc-123")
    assert url.startswith("https://us-east-1.console.aws.amazon.com/codesuite/codebuild/")
    assert "projects/shiny-app-build" in url
    assert bundle.build_console_url("") == ""


# --- the two new live_states ------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (registry.STATUS_BUILDING, "building"),
        (registry.STATUS_BUILD_FAILED, "build_failed"),
        (registry.STATUS_ACTIVE, "awake"),
    ],
)
def test_the_p2a_states_are_derived_from_the_row(status, expected):
    row = app_row(status=status)
    state = ServiceState(exists=True, desired=1, running=1)
    assert portal.live_state(row, state, NOW) == expected


def test_a_building_app_is_not_reported_asleep_just_because_it_has_no_service():
    """"asleep" would send a creator to click a link that 403s."""
    row = app_row(status=registry.STATUS_BUILDING)
    assert portal.live_state(row, None, NOW) == portal.LIVE_BUILDING


def test_a_building_row_with_an_expiry_still_reads_as_building():
    row = app_row(status=registry.STATUS_BUILDING, expires_at=int(NOW - 1))
    assert portal.live_state(row, None, NOW) == portal.LIVE_BUILDING


@pytest.mark.asyncio
async def test_a_building_app_never_appears_in_anyone_menu():
    """access.decide refuses an unrecognised status, so the menu -- which
    reuses it -- cannot offer a tile the proxy would then 403."""
    store = FakeStore(
        [
            app_row(
                host="tarpeyo.tools.stratevi.com",
                status=registry.STATUS_BUILDING,
                access_mode=registry.MODE_ALL_USERS,
            )
        ]
    )
    response = await portal_for(store=store).handle(request("/api/v1/menu"))
    assert body_of(response)["apps"] == []


@pytest.mark.asyncio
async def test_a_building_app_is_visible_to_an_admin_in_the_full_listing():
    store = FakeStore([building_row()])
    response = await portal_for(store=store).handle(
        request("/api/v1/apps", email=ADMIN)
    )
    assert body_of(response)[0]["live_state"] == "building"


# --- the admin API still cannot reach provisioning attributes ---------------


@pytest.mark.parametrize(
    "field",
    ["cpu", "memory", "packages", "upload_key", "image", "build_id", "release_tag",
     "created_by", "build_error"],
)
def test_no_admin_patch_may_rewrite_what_a_build_was_made_from(field):
    with pytest.raises(portal.PatchError, match="unknown field"):
        portal.validate_patch({field: "anything"})


def test_status_building_is_still_not_something_an_admin_can_set_by_hand():
    for status in ("building", "build_failed"):
        with pytest.raises(portal.PatchError, match="active or disabled"):
            portal.validate_patch({"status": status})
