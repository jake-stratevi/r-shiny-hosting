"""Creating an app: ECR, IAM, CodeBuild, ECS, Cognito -- through the SDK.

Per ADR-0012 and ``docs/design/portal-p2a.md``: Terraform owns what engineers
edit (the uploads bucket, the CodeBuild project, the permissions boundary),
and the portal creates the PER-APP resources at runtime. Nothing in here is
Terraform, and nothing in here is a listener rule, a target group, a waker or
a per-app Cognito client -- a P2a app is born proxied, which is why creating
one is five API calls instead of a stack.

The AWS classes sit at the bottom of the module behind the small async
protocols the orchestration depends on, exactly like `registry` and `ecsctl`,
so the tests need no fake AWS.

Three things here are load-bearing:

* **The permissions boundary cannot be skipped.** :class:`Boto3TaskRoles`
  refuses to be constructed without one, its create method takes no boundary
  argument for a caller to omit, and it re-reads the role afterwards and
  deletes it if the boundary is not attached. See the class banner -- this is
  the control that stops "the portal can create IAM roles" from meaning "the
  portal can grant itself anything".
* **The Cognito update is a read-modify-write and it replaces EVERYTHING.**
  ``UpdateUserPoolClient`` is not a patch: any field you do not resend is
  reset to its default. Sending only ``CallbackURLs`` would silently strip
  the client's OAuth flows, scopes, identity providers and token validities,
  and the failure would show up as every app on the platform refusing to
  sign anyone in. :func:`merged_client_config` is separated out and tested on
  its own for that reason, and :meth:`Boto3Clients.add_host` verifies the
  result afterwards.
* **A failure after the row is reserved never rolls resources back.** It sets
  ``build_failed``, records why, and leaves what exists for inspection
  (portal-p2a.md, step 6). A retry reuses them; deleting them is P2b's job.
  What must never happen is a row stuck in ``building`` forever, which is
  what :class:`BuildWatcher`'s 45-minute sweep exists to prevent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from . import audit as audit_mod, creation, registry
from .audit import Event
from .creation import CreateSpec
from .registry import App

#: How long a build may sit unfinished before the sweep calls it dead.
#: portal-p2a.md: "a create older than 45 minutes with no build is reaped to
#: build_failed". Generous on purpose -- a first R image build with a fat
#: package list genuinely takes 10-20 minutes, and killing a slow-but-working
#: build is worse than waiting.
STUCK_BUILD_SECONDS = 45 * 60

#: How long a presigned PUT is good for. Long enough for a 100 MB upload on a
#: hotel wifi, short enough that a URL left in a browser history is not a
#: standing write grant.
UPLOAD_URL_SECONDS = 900

#: The first release. P2b's version history increments this; P2a always
#: builds r1, and the row remembers which so a rebuild is reproducible.
FIRST_RELEASE_TAG = "r1"

#: Images kept per repository, per portal-p2a.md. Five is enough to roll back
#: through a bad afternoon and few enough that ECR storage stays cents.
LIFECYCLE_KEEP_IMAGES = 5

#: Cognito's documented ceiling on callback URLs per app client. Checked
#: before the update rather than after, so the failure is a legible message
#: instead of a Cognito 400 halfway through provisioning.
MAX_CALLBACK_URLS = 100

#: Fields ``DescribeUserPoolClient`` returns that ``UpdateUserPoolClient``
#: will not accept. Everything else must be resent -- see the module banner.
DESCRIBE_ONLY_FIELDS = ("ClientSecret", "LastModifiedDate", "CreationDate")

#: CodeBuild terminal states. Anything not IN_PROGRESS is over.
BUILD_IN_PROGRESS = "IN_PROGRESS"
BUILD_SUCCEEDED = "SUCCEEDED"

DEFAULT_LOG_TAIL_LINES = 50

#: The Terraform project prefix (`shiny`), which is also what every
#: portal-created resource carries as its ``Project`` tag. Kept next to the
#: other contract constants rather than only as a default argument, so the
#: tagging helper and the resource names cannot disagree about it.
DEFAULT_PREFIX = "shiny"

#: The env vars ``StartBuild`` overrides per build, in the order they are sent.
#: This IS the build contract's portal half: ``buildspec/render.py``'s
#: ``REQUIRED`` tuple is exactly these five plus ``UPLOADS_BUCKET``, which is
#: the same bucket for every app and is therefore set statically on the
#: CodeBuild project (``proxy/codebuild.tf``) rather than per build.
#:
#: ``ECR_REPO_URI`` is the one that is easy to lose: it is per-app, so it
#: CANNOT be static, and without it every build dies in ``pre_build`` before a
#: single R package is downloaded.
BUILD_ENV_OVERRIDES = (
    "APP_KEY",
    "ZIP_KEY",
    "RELEASE_TAG",
    "ECR_REPO_URI",
    "PACKAGES",
)

#: Required build variables the CodeBuild project sets once, for every app.
BUILD_ENV_PROJECT_STATIC = ("UPLOADS_BUCKET",)


# --- tagging ---------------------------------------------------------------
#
# THE COST TAG IS NOT COSMETIC. platform/monitoring.tf's $75 budget filters on
# `user:Project$shiny`, so a repository, role or service the portal creates
# WITHOUT `Project=shiny` spends money that no budget and no alert can see.
# The first sign of a missing tag is a bill, months later. Every portal-created
# resource goes through one of these two helpers.


def portal_tags(prefix: str = DEFAULT_PREFIX) -> list[dict[str, str]]:
    """``Key``/``Value`` tags for a portal-created resource (IAM, ECR)."""
    return [
        {"Key": "ManagedBy", "Value": f"{prefix}-portal"},
        # The budget tag. See the banner above before removing it.
        {"Key": "Project", "Value": prefix},
    ]


def portal_tags_ecs(prefix: str = DEFAULT_PREFIX) -> list[dict[str, str]]:
    """The same tags in ECS's shape -- it spells the keys lowercase."""
    return [
        {"key": tag["Key"], "value": tag["Value"]} for tag in portal_tags(prefix)
    ]


class ProvisionError(RuntimeError):
    """Provisioning failed after the row was reserved.

    Carries the message that goes on the row and into the audit trail. The
    row is already ``build_failed`` by the time this is raised -- the caller
    turns it into a response, it does not have to clean up.
    """


# --- what the orchestration needs from AWS ---------------------------------


@dataclass(frozen=True)
class BuildStatus:
    """One CodeBuild build, narrowed to what the build screen shows."""

    state: str = ""
    phase: str = ""
    started_at: int = 0
    log_group: str = ""
    log_stream: str = ""
    log_url: str = ""

    def finished(self) -> bool:
        return bool(self.state) and self.state != BUILD_IN_PROGRESS

    def succeeded(self) -> bool:
        return self.state == BUILD_SUCCEEDED


class Repositories(Protocol):
    """ECR, narrowed to "make me a repository to push into"."""

    async def ensure(self, name: str) -> str: ...


class TaskRoles(Protocol):
    """IAM, narrowed to "make me a task role that cannot exceed the boundary".

    Note what is NOT in this signature: a boundary argument. See
    :class:`Boto3TaskRoles`.
    """

    async def ensure(self, app_key: str) -> str: ...


class Builds(Protocol):
    """CodeBuild, narrowed to start-one and ask-how-it-went."""

    async def start(self, *, app_key: str, zip_key: str, release_tag: str,
                    repository_uri: str, packages: str) -> str: ...

    async def status(self, build_id: str) -> BuildStatus: ...

    async def log_tail(self, build: BuildStatus, lines: int) -> list[str]: ...


class Services(Protocol):
    """ECS, narrowed to the two calls that turn an image into a sleeping app."""

    async def register_task_definition(self, app: App) -> str: ...

    async def create_service(self, *, service: str, task_definition: str) -> None: ...


class Clients(Protocol):
    """Cognito, narrowed to "this hostname may now complete a sign-in"."""

    async def add_host(self, host: str) -> None: ...


class Uploads(Protocol):
    """S3, narrowed to issuing one presigned PUT."""

    def presign(self, key: str) -> str: ...

    def new_key(self) -> str: ...


class RecorderLike(Protocol):
    def record(self, event: Event) -> None: ...


class StoreLike(Protocol):
    async def app(self, host: str) -> App | None: ...

    async def apps(self) -> list[App]: ...

    async def reserve(self, app: App) -> None: ...

    async def patch(self, host: str, changes: dict[str, Any]) -> None: ...


# --- the provisioner -------------------------------------------------------


class Provisioner:
    """Runs portal-p2a.md's provisioning order, and its failure rule."""

    def __init__(
        self,
        *,
        store: StoreLike,
        repositories: Repositories,
        roles: TaskRoles,
        builds: Builds,
        services: Services,
        clients: Clients,
        recorder: RecorderLike,
        domain: str,
        prefix: str = DEFAULT_PREFIX,
        log: logging.Logger | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._repositories = repositories
        self._roles = roles
        self._builds = builds
        self._services = services
        self._clients = clients
        self._audit = recorder
        self._domain = domain
        self._prefix = prefix
        self._log = log or logging.getLogger("proxy.provision")
        self._clock = clock

    @property
    def domain(self) -> str:
        return self._domain

    # --- create ------------------------------------------------------------

    async def create(self, spec: CreateSpec, email: str) -> App:
        """Reserve the slug, provision, start the build. Returns the row.

        Step order is portal-p2a.md's and is not arbitrary: the conditional
        put comes FIRST, before any resource exists, so the loser of a race
        has created nothing to clean up. Everything after it is best-effort
        forward progress -- if any of it fails the row goes to
        ``build_failed`` and whatever was created stays for inspection.
        """
        now = int(self._clock())
        # Minted here, once, and never derived from the key: see
        # `creation.host_suffix`. The row's `host` IS the suffixed hostname --
        # nothing else about the app carries the suffix, so the ECR
        # repository, the task role and the ECS service all stay
        # `shiny-<key>` and stay readable in the console.
        host = spec.host(self._domain, creation.host_suffix())
        row = App.create(
            host=host,
            app_key=spec.key,
            label=spec.label,
            description=spec.description,
            ecs_service=spec.ecs_service(self._prefix),
            container_port=registry.DEFAULT_CONTAINER_PORT,
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
            release_tag=FIRST_RELEASE_TAG,
            created_by=email,
            created_at=now,
        )

        # Raises registry.HostTaken, which the handler turns into a 409. The
        # mutex, and the only step whose failure leaves nothing behind.
        await self._store.reserve(row)

        self._audit.record(
            Event(host=host, event=audit_mod.EVENT_APP_CREATED, email=email,
                  path=spec.key, at=now)
        )
        self._log.info(
            "app row reserved",
            extra={"host": host, "app_key": spec.key, "email": email,
                   "cpu": spec.cpu, "memory": spec.memory},
        )

        # Audited above, because the row genuinely exists by now -- but still
        # before the first AWS call, so a loser provisions nothing.
        await self._claim_key(row, email)

        try:
            repository = await self._repositories.ensure(spec.repository(self._prefix))
            role_arn = await self._roles.ensure(spec.key)
            build_id = await self._builds.start(
                app_key=spec.key,
                zip_key=spec.upload_key,
                release_tag=FIRST_RELEASE_TAG,
                # Per-app, so it cannot be static on the shared CodeBuild
                # project. ECR is ensured first precisely so this is in hand
                # by the time the build starts -- see BUILD_ENV_OVERRIDES.
                repository_uri=repository,
                packages=creation.package_list(spec.packages),
            )
        except Exception as exc:
            await self.fail(row, f"provisioning failed: {exc}",
                            event=audit_mod.EVENT_PROVISION_FAILED, email=email)
            raise ProvisionError(str(exc)) from exc

        image = f"{repository}:{FIRST_RELEASE_TAG}"
        changes = {"build_id": build_id, "build_started_at": now, "image": image}
        try:
            await self._store.patch(host, changes)
        except Exception as exc:
            # The build is already running, so this is not fatal to the image
            # -- but the row now has no build to poll, and the sweep will
            # reap it in 45 minutes. Loud, because the fix is manual.
            self._log.error(
                "build started but its id could not be recorded",
                extra={"host": host, "build_id": build_id, "reason": str(exc)},
            )

        self._audit.record(
            Event(host=host, event=audit_mod.EVENT_BUILD_STARTED, email=email,
                  path=build_id, at=now)
        )
        self._log.info(
            "build started",
            extra={"host": host, "app_key": spec.key, "build_id": build_id,
                   "role": role_arn, "image": image},
        )

        return App.create(**{**row.__dict__, **changes})

    async def _claim_key(self, row: App, email: str) -> None:
        """Confirm this row is the only one holding its ``app_key``.

        Before hostnames gained a random suffix (`creation.host_suffix`), the
        conditional put above WAS this check: two wizards submitting `model`
        at the same moment raced for one partition key and exactly one won.
        With a random suffix the two rows no longer collide, so both would be
        written -- and both would be named `shiny-model`, sharing one ECR
        repository, one IAM role and one ECS service, with the second build
        overwriting the first app's image at the same tag. Silently.

        So the same question is asked again here, after the write rather than
        during it: read the table back, and if another row already claims this
        key, decide which of us was first. The tie-break is
        ``(created_at, host)``, computed from rows every racer can see, so all
        of them reach the same verdict and exactly one survives -- rather than
        both politely standing down and nobody getting an app.

        The loser's row is marked ``build_failed`` with the reason on it and
        has provisioned nothing: this still runs before the first AWS call.

        This is a weaker mutex than the conditional put it replaces --
        ``apps()`` is a Scan, and a Scan is eventually consistent, so a tight
        enough race can let both rows through. Restoring the old strength
        needs a ``__key__<key>`` guard row written in the same transaction as
        the app row, which is a data-model change and is flagged for Jake
        rather than smuggled in here.
        """
        try:
            rows = await self._store.apps()
        except Exception as exc:
            # A table we cannot read is not evidence of a collision, and the
            # wizard's check and `_key_problem` have both already passed.
            # Log it and carry on rather than refusing a legitimate create.
            self._log.warning(
                "could not confirm the app key is unique",
                extra={"host": row.host, "app_key": row.app_key, "reason": str(exc)},
            )
            return

        rivals = [
            other
            for other in rows
            if other.app_key == row.app_key and other.host != row.host
        ]
        if not rivals:
            return

        mine = (row.created_at, row.host)
        if all(mine < (other.created_at, other.host) for other in rivals):
            return  # we were first; the others resolve the same way and stand down

        await self.fail(
            row,
            f"another app already uses the key {row.app_key}",
            event=audit_mod.EVENT_PROVISION_FAILED,
            email=email,
        )
        raise registry.HostTaken(row.host)

    # --- finish ------------------------------------------------------------

    async def finish(self, app: App) -> None:
        """The image exists: make it an app. Called by :class:`BuildWatcher`.

        Task definition, service at desired **0** (nobody has asked for it
        yet; the proxy wakes it on the first request like every other app),
        then the Cognito callback, then the row flips to active. The Cognito
        call is last on purpose -- it is the one that mutates SHARED state,
        so it is not made until everything private to this app has worked.
        """
        try:
            task_definition = await self._services.register_task_definition(app)
            await self._services.create_service(
                service=app.ecs_service, task_definition=task_definition
            )
            await self._clients.add_host(app.host)
        except Exception as exc:
            await self.fail(app, f"provisioning failed: {exc}",
                            event=audit_mod.EVENT_PROVISION_FAILED)
            return

        try:
            await self._store.patch(
                app.host, {"status": registry.STATUS_ACTIVE, "build_error": ""}
            )
        except Exception as exc:
            # Everything AWS-side is built; only the row disagrees. Leave it
            # in `building` -- the next sweep sees a succeeded build and
            # retries, and every step above is idempotent.
            self._log.error(
                "provisioned but could not activate the row",
                extra={"host": app.host, "reason": str(exc)},
            )
            return

        self._audit.record(
            Event(host=app.host, event=audit_mod.EVENT_BUILD_SUCCEEDED,
                  email=app.created_by, path=app.build_id)
        )
        self._log.info(
            "app active",
            extra={"host": app.host, "app_key": app.app_key,
                   "service": app.ecs_service, "image": app.image},
        )

    # --- fail --------------------------------------------------------------

    async def fail(
        self,
        app: App,
        reason: str,
        *,
        event: str = audit_mod.EVENT_BUILD_FAILED,
        email: str = "",
    ) -> None:
        """Mark a row failed. Never raises -- it is the error path itself."""
        reason = (reason or "the build failed").strip()[:1000]
        try:
            await self._store.patch(
                app.host,
                {"status": registry.STATUS_BUILD_FAILED, "build_error": reason},
            )
        except Exception as exc:
            self._log.error(
                "could not mark the app failed",
                extra={"host": app.host, "reason": str(exc)},
            )

        self._audit.record(
            Event(host=app.host, event=event, email=email or app.created_by,
                  path=app.build_id, outcome=reason)
        )
        self._log.error(
            "app creation failed",
            extra={"host": app.host, "app_key": app.app_key, "event": event,
                   "build_id": app.build_id, "reason": reason},
        )


# --- polling the build -----------------------------------------------------


class BuildWatcher:
    """Turns CodeBuild's state into the row's state, once a minute.

    Polling, not a webhook, and deliberately: a webhook needs a public
    endpoint, an authentication story and a Terraform-owned notification rule
    for a check that costs one API call a minute against a table that has a
    handful of rows. The sleeper loop is already running; this rides on it.

    Every path out of ``building`` is here, including the ones where AWS
    never answers. A row that stays ``building`` forever is the failure mode
    this class exists to make impossible.
    """

    def __init__(
        self,
        *,
        provisioner: Provisioner,
        builds: Builds,
        log: logging.Logger | None = None,
        stuck_after: float = STUCK_BUILD_SECONDS,
    ) -> None:
        self._provisioner = provisioner
        self._builds = builds
        self._log = log or logging.getLogger("proxy.provision")
        self._stuck_after = stuck_after

    async def sweep(self, app: App, now: float) -> None:
        """Advance one ``building`` row. Never raises."""
        started = app.build_started_at or app.created_at
        overdue = bool(started) and (now - started) > self._stuck_after

        if not app.build_id:
            # Reserved, but StartBuild never landed (or its id was never
            # recorded). Nothing will ever move this row; the sweep is the
            # only thing that can.
            if overdue or not started:
                await self._provisioner.fail(
                    app,
                    "no build was ever started for this app",
                    event=audit_mod.EVENT_PROVISION_FAILED,
                )
            return

        try:
            status = await self._builds.status(app.build_id)
        except Exception as exc:
            # CodeBuild unreachable, or the build aged out of its retention.
            # Keep waiting until the deadline, then give up rather than poll
            # a vanished build forever.
            self._log.warning(
                "cannot read build status",
                extra={"host": app.host, "build_id": app.build_id, "reason": str(exc)},
            )
            if overdue:
                await self._provisioner.fail(
                    app, f"the build could not be checked: {exc}",
                    event=audit_mod.EVENT_PROVISION_FAILED
                )
            return

        if not status.finished():
            if overdue:
                await self._provisioner.fail(
                    app,
                    f"the build did not finish within "
                    f"{int(self._stuck_after // 60)} minutes",
                )
            return

        if status.succeeded():
            await self._provisioner.finish(app)
            return

        phase = f" during {status.phase}" if status.phase else ""
        await self._provisioner.fail(
            app, f"the image build {status.state.lower()}{phase}"
        )


# --- what the portal holds -------------------------------------------------


@dataclass(frozen=True)
class Creation:
    """Everything the portal's P2a routes need, in one collaborator.

    Bundled rather than passed as six constructor arguments so that "creation
    is configured" is one nullable object: no env, no bundle, and every P2a
    route answers 503 instead of half-working.
    """

    domain: str
    uploads: Uploads
    provisioner: Provisioner
    builds: Builds
    #: The ``__config__`` denylist, cached and fail-closed -- the same
    #: `portal.ConfigList` machinery the admin and creator lists use.
    denylist: Any
    console_region: str = ""
    codebuild_project: str = ""

    def build_console_url(self, build_id: str) -> str:
        """A console deep link for a build id, for when CodeBuild gives none.

        The id is ``<project>:<uuid>``; the console URL wants it slash-
        separated. Not security-relevant, purely a link a human clicks.
        """
        if not build_id or not self._region():
            return ""
        return (
            f"https://{self._region()}.console.aws.amazon.com/codesuite/codebuild/"
            f"projects/{build_id.split(':', 1)[0]}/build/"
            f"{build_id.replace(':', '%3A')}/log"
        )

    def _region(self) -> str:
        return (self.console_region or "").strip()


# --- AWS implementations ---------------------------------------------------


class Boto3Uploads:
    """Presigned PUTs into the uploads bucket.

    The API never proxies bytes (portal-api.md): a 100 MB zip through a
    0.25 vCPU task would be miserable, and the browser can talk to S3
    directly. ``generate_presigned_url`` signs LOCALLY -- no network call, no
    thread -- which is why this is the one AWS class here that is not async.

    Only Bucket and Key are signed. Signing ``ContentLength`` would let S3
    enforce the declared size, but then a browser whose ``Content-Length``
    differs by a byte gets an opaque 403 that surfaces as a CORS error. The
    size is already refused before this is called, and the build validates
    the bundle for real.

    **The client MUST be configured for signature version s3v4** (see
    ``__main__``). boto3's default against the global endpoint is the legacy
    SigV2, which folds Content-Type into the string-to-sign — and a browser
    always sends one for a File, while the presigner signed an empty value.
    The result is a 403 SignatureDoesNotMatch that reproduces only in a
    browser: curl sends no Content-Type, so every command-line check of the
    same URL returns 200. ``assert_v4`` below refuses to let that ship.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @staticmethod
    def assert_v4(url: str) -> None:
        """Raise unless this URL is SigV4. Cheap insurance against a
        one-word config change that only breaks in browsers."""
        if "X-Amz-Algorithm=AWS4-HMAC-SHA256" not in url:
            raise ProvisionError(
                "the uploads client is not signing with SigV4 — a browser's "
                "Content-Type will make S3 reject the PUT with 403; "
                "construct the s3 client with "
                'Config(signature_version="s3v4")'
            )

    def new_key(self) -> str:
        return f"uploads/{uuid.uuid4()}.zip"

    def presign(self, key: str) -> str:
        url = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=UPLOAD_URL_SECONDS,
        )
        self.assert_v4(url)
        return url


class Boto3Repositories:
    """One ECR repository per app, with a keep-5 lifecycle policy."""

    def __init__(
        self,
        client: Any,
        log: logging.Logger | None = None,
        *,
        prefix: str = DEFAULT_PREFIX,
    ) -> None:
        self._client = client
        self._log = log or logging.getLogger("proxy.provision")
        self._prefix = prefix

    async def ensure(self, name: str) -> str:
        try:
            response = await asyncio.to_thread(
                self._client.create_repository,
                repositoryName=name,
                imageTagMutability="MUTABLE",  # :latest has to move
                imageScanningConfiguration={"scanOnPush": True},
                # Project=shiny included: the platform budget filters on it.
                tags=portal_tags(self._prefix),
            )
            uri = str(response["repository"]["repositoryUri"])
        except Exception as exc:
            if _error_code(exc) != "RepositoryAlreadyExistsException":
                raise
            # A retry after a failed create. Reuse it rather than failing --
            # portal-p2a.md's "a retry reuses them".
            described = await asyncio.to_thread(
                self._client.describe_repositories, repositoryNames=[name]
            )
            uri = str(described["repositories"][0]["repositoryUri"])

        await asyncio.to_thread(
            self._client.put_lifecycle_policy,
            repositoryName=name,
            lifecyclePolicyText=json.dumps(lifecycle_policy()),
        )
        return uri


def lifecycle_policy(keep: int = LIFECYCLE_KEEP_IMAGES) -> dict[str, Any]:
    """Keep the last ``keep`` images, expire the rest."""
    return {
        "rules": [
            {
                "rulePriority": 1,
                "description": f"keep the last {keep} images",
                "selection": {
                    "tagStatus": "any",
                    "countType": "imageCountMoreThan",
                    "countNumber": keep,
                },
                "action": {"type": "expire"},
            }
        ]
    }


# ---------------------------------------------------------------------------
# THE PERMISSIONS BOUNDARY. Read this before changing anything below.
#
# A web service that can call iam:CreateRole is a privilege-escalation engine
# unless it is fenced. The fence is the Terraform-owned `shiny-app-boundary`
# policy: a role that carries it cannot exceed ECS Exec + CloudWatch Logs +
# GetObject on its own data prefix, EVEN IF this service is compromised and
# writes the role an AdministratorAccess inline policy.
#
# The fence only works if EVERY role gets it. That is enforced three ways
# here, none of which is a convention a future edit can quietly drop:
#
#   1. The constructor REFUSES a blank boundary ARN. There is no way to hold
#      one of these objects without one.
#   2. `ensure()` takes no boundary parameter. A caller cannot pass the wrong
#      one, cannot pass None, and cannot forget the argument.
#   3. After creating, the role is READ BACK and the attached boundary
#      compared. A mismatch deletes the role and raises -- so even an IAM API
#      that accepted the call and ignored the parameter cannot leave an
#      unbounded `shiny-app-*` role in the account.
#
# And above all three: `config.Creation` requires APP_ROLE_BOUNDARY_ARN, so
# creation is OFF entirely when there is no boundary to attach.
# ---------------------------------------------------------------------------


class Boto3TaskRoles:
    """Per-app task roles, always inside the boundary."""

    def __init__(
        self,
        client: Any,
        *,
        boundary_arn: str,
        data_bucket: str,
        prefix: str = DEFAULT_PREFIX,
        log: logging.Logger | None = None,
    ) -> None:
        if not (boundary_arn or "").strip():
            # Invariant 1. Not a warning, not a default: this object cannot
            # exist without a boundary to attach.
            raise ValueError(
                "a permissions boundary ARN is required to create app task "
                "roles; set APP_ROLE_BOUNDARY_ARN"
            )
        self._client = client
        self._boundary = boundary_arn.strip()
        self._data_bucket = data_bucket
        self._prefix = prefix
        self._log = log or logging.getLogger("proxy.provision")

    def role_name(self, app_key: str) -> str:
        return f"{self._prefix}-app-{app_key}-task"

    async def ensure(self, app_key: str) -> str:
        """Create (or reuse) ``shiny-app-<key>-task``. Invariant 2: no
        boundary argument exists for a caller to get wrong."""
        name = self.role_name(app_key)

        try:
            await asyncio.to_thread(
                self._client.create_role,
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(TASK_TRUST_POLICY),
                PermissionsBoundary=self._boundary,
                Description=f"Task role for the {app_key} Shiny app (portal-created)",
                # Project=shiny included: the platform budget filters on it.
                Tags=[
                    *portal_tags(self._prefix),
                    {"Key": "AppKey", "Value": app_key},
                ],
            )
        except Exception as exc:
            if _error_code(exc) != "EntityAlreadyExists":
                raise
            self._log.info("reusing an existing app task role", extra={"role": name})

        arn = await self._verify_boundary(name)

        await asyncio.to_thread(
            self._client.put_role_policy,
            RoleName=name,
            PolicyName="app",
            PolicyDocument=json.dumps(self.inline_policy(app_key)),
        )
        return arn

    async def _verify_boundary(self, name: str) -> str:
        """Invariant 3: read the role back and prove the boundary is on it.

        A role that is not inside the fence is deleted, not used. Deleting
        can itself fail (a race, a permissions gap); the raise happens either
        way, so a role that escaped the boundary can never be passed to a
        task definition.
        """
        described = await asyncio.to_thread(self._client.get_role, RoleName=name)
        role = described.get("Role") or {}
        attached = str(
            (role.get("PermissionsBoundary") or {}).get("PermissionsBoundaryArn") or ""
        )
        if attached != self._boundary:
            self._log.error(
                "app task role is not inside the permissions boundary; deleting",
                extra={"role": name, "expected": self._boundary, "found": attached},
            )
            try:
                await asyncio.to_thread(self._client.delete_role, RoleName=name)
            except Exception as exc:  # pragma: no cover - defence in depth
                self._log.error(
                    "could not delete the unbounded role",
                    extra={"role": name, "reason": str(exc)},
                )
            raise ProvisionError(
                f"role {name} is not inside the permissions boundary"
            )
        return str(role.get("Arn") or "")

    def inline_policy(self, app_key: str) -> dict[str, Any]:
        """What the app itself may do. Capped by the boundary regardless.

        Kept to the same three things the boundary permits, so the effective
        permissions are legible from this file alone rather than only from
        the intersection with a Terraform policy.
        """
        statements: list[dict[str, Any]] = [
            {
                "Sid": "EcsExec",
                "Effect": "Allow",
                "Action": [
                    "ssmmessages:CreateControlChannel",
                    "ssmmessages:CreateDataChannel",
                    "ssmmessages:OpenControlChannel",
                    "ssmmessages:OpenDataChannel",
                ],
                "Resource": "*",
            },
            {
                "Sid": "Logs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": "*",
            },
        ]
        if self._data_bucket:
            statements.append(
                {
                    "Sid": "OwnData",
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{self._data_bucket}",
                        f"arn:aws:s3:::{self._data_bucket}/{app_key}/*",
                    ],
                }
            )
        return {"Version": "2012-10-17", "Statement": statements}


TASK_TRUST_POLICY: dict[str, Any] = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


class Boto3Builds:
    """The one shared CodeBuild project, driven by per-build overrides."""

    def __init__(
        self,
        client: Any,
        logs: Any,
        project: str,
        log: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._logs = logs
        self._project = project
        self._log = log or logging.getLogger("proxy.provision")

    async def start(
        self,
        *,
        app_key: str,
        zip_key: str,
        release_tag: str,
        repository_uri: str,
        packages: str,
    ) -> str:
        """StartBuild with the env overrides the buildspec reads.

        The names are ``buildspec/README.md``'s "Env var contract", and
        ``buildspec/render.py`` enforces them: its ``REQUIRED`` tuple is
        ``APP_KEY``, ``ZIP_KEY``, ``RELEASE_TAG``, ``UPLOADS_BUCKET``,
        ``ECR_REPO_URI``, ``PACKAGES``, and a missing one fails the build in
        ``pre_build`` naming the variable.

        Five of the six are sent here; ``UPLOADS_BUCKET`` is the same bucket
        for every app and is set statically on the project by
        ``proxy/codebuild.tf``. ``ECR_REPO_URI`` is per-app -- it is
        ``<acct>.dkr.ecr.<region>.amazonaws.com/shiny-<key>``, with no tag --
        so it has to travel with the build, and the caller has it from
        :meth:`Boto3Repositories.ensure`.
        """
        overrides = [
            {"name": "APP_KEY", "value": app_key, "type": "PLAINTEXT"},
            {"name": "ZIP_KEY", "value": zip_key, "type": "PLAINTEXT"},
            {"name": "RELEASE_TAG", "value": release_tag, "type": "PLAINTEXT"},
            {"name": "ECR_REPO_URI", "value": repository_uri, "type": "PLAINTEXT"},
            {"name": "PACKAGES", "value": packages, "type": "PLAINTEXT"},
        ]
        response = await asyncio.to_thread(
            self._client.start_build,
            projectName=self._project,
            environmentVariablesOverride=overrides,
        )
        return str((response.get("build") or {}).get("id") or "")

    async def status(self, build_id: str) -> BuildStatus:
        response = await asyncio.to_thread(
            self._client.batch_get_builds, ids=[build_id]
        )
        builds = response.get("builds") or ()
        if not builds:
            raise ProvisionError(f"build {build_id} no longer exists")
        return build_status(builds[0])

    async def log_tail(
        self, build: BuildStatus, lines: int = DEFAULT_LOG_TAIL_LINES
    ) -> list[str]:
        """The last few log lines, for the build screen.

        Best effort and never raises: a build screen with no tail is worse
        than one with a tail, but far better than a 503.
        """
        if not build.log_group or not build.log_stream:
            return []
        try:
            response = await asyncio.to_thread(
                self._logs.get_log_events,
                logGroupName=build.log_group,
                logStreamName=build.log_stream,
                limit=max(1, int(lines)),
                startFromHead=False,
            )
        except Exception as exc:
            self._log.warning("cannot read build logs", extra={"reason": str(exc)})
            return []
        return [
            str(entry.get("message", "")).rstrip("\n")
            for entry in (response.get("events") or ())
        ]


def build_status(described: dict[str, Any]) -> BuildStatus:
    """Decode one ``batch_get_builds`` entry."""
    logs = described.get("logs") or {}
    started = described.get("startTime")
    return BuildStatus(
        state=str(described.get("buildStatus") or ""),
        phase=str(described.get("currentPhase") or ""),
        started_at=int(started.timestamp()) if hasattr(started, "timestamp") else 0,
        log_group=str(logs.get("groupName") or ""),
        log_stream=str(logs.get("streamName") or ""),
        log_url=str(logs.get("deepLink") or ""),
    )


class Boto3Services:
    """Task definitions and services for created apps.

    No load balancer block, no target group, no waker: a P2a app is reached
    through the proxy's direct-to-task routing, and the proxy's own security
    group is already allowed into the shared apps SG (proxy/ecs.tf).
    """

    def __init__(
        self,
        client: Any,
        *,
        cluster: str,
        subnets: Sequence[str],
        security_group: str,
        execution_role_arn: str,
        log_group: str,
        region: str,
        roles: Boto3TaskRoles | None = None,
        prefix: str = DEFAULT_PREFIX,
    ) -> None:
        self._client = client
        self._cluster = cluster
        self._subnets = list(subnets)
        self._security_group = security_group
        self._execution_role_arn = execution_role_arn
        self._log_group = log_group
        self._region = region
        self._roles = roles
        self._prefix = prefix

    async def register_task_definition(self, app: App) -> str:
        container = {
            "name": "app",
            "image": app.image,
            "essential": True,
            "portMappings": [
                {"containerPort": app.container_port, "protocol": "tcp"}
            ],
            "environment": [
                # ADR-0011: the app must not call parallel::detectCores(),
                # which reports the Fargate HOST's cores, not the task limit.
                {
                    "name": "SHINY_CPU_WORKERS",
                    "value": str(creation.cpu_workers(app.cpu)),
                },
                {"name": "APP_KEY", "value": app.app_key},
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": self._log_group,
                    "awslogs-region": self._region,
                    "awslogs-stream-prefix": app.app_key,
                },
            },
        }
        kwargs: dict[str, Any] = {
            "family": f"{self._prefix}-{app.app_key}",
            "requiresCompatibilities": ["FARGATE"],
            "networkMode": "awsvpc",
            "cpu": str(app.cpu),
            "memory": str(app.memory),
            "executionRoleArn": self._execution_role_arn,
            "runtimePlatform": {
                "operatingSystemFamily": "LINUX",
                "cpuArchitecture": "X86_64",
            },
            "containerDefinitions": [container],
        }
        if self._roles is not None:
            kwargs["taskRoleArn"] = self._roles.role_name(app.app_key)
        response = await asyncio.to_thread(
            self._client.register_task_definition, **kwargs
        )
        return str((response.get("taskDefinition") or {}).get("taskDefinitionArn") or "")

    async def create_service(self, *, service: str, task_definition: str) -> None:
        """Create the service at desired **0**.

        Zero, not one: nobody has asked for this app yet, and the proxy wakes
        it on the first request exactly as it wakes every other app. Creating
        it awake would start the meter running on an app nobody has opened.
        """
        try:
            await asyncio.to_thread(
                self._client.create_service,
                cluster=self._cluster,
                serviceName=service,
                taskDefinition=task_definition,
                desiredCount=0,
                launchType="FARGATE",
                enableExecuteCommand=True,
                # Project=shiny included: the platform budget filters on it,
                # and a service is the expensive one of the three.
                tags=portal_tags_ecs(self._prefix),
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": self._subnets,
                        "securityGroups": [self._security_group],
                        # No NAT Gateway (ADR-0004): a task needs a public IP
                        # to pull its own image.
                        "assignPublicIp": "ENABLED",
                    }
                },
            )
        except Exception as exc:
            if _error_code(exc) in ("ServiceAlreadyExistsException",):
                return  # a retry; portal-p2a.md's "a retry reuses them"
            raise


# ---------------------------------------------------------------------------
# COGNITO. The single most dangerous call in P2a.
#
# UpdateUserPoolClient is a REPLACE, not a merge. Send it CallbackURLs alone
# and Cognito resets AllowedOAuthFlows, AllowedOAuthScopes,
# SupportedIdentityProviders, ExplicitAuthFlows and every token validity to
# their defaults -- which, for a client the ALB authenticates against, means
# every app on the platform stops signing anybody in. It fails silently: the
# API returns 200.
#
# So: Describe first, carry EVERY field forward, drop only the three fields
# Update does not accept, and verify afterwards. The merge is a pure function
# so it can be tested against a full, realistic client config.
# ---------------------------------------------------------------------------


def merged_client_config(
    described: dict[str, Any], *, callback: str, logout: str
) -> dict[str, Any]:
    """The complete UpdateUserPoolClient argument set, with the host added.

    Everything Describe returned is carried forward verbatim except the three
    read-only fields. New URLs are APPENDED; existing ones are never
    reordered or dropped.
    """
    config = {
        name: value
        for name, value in (described or {}).items()
        if name not in DESCRIBE_ONLY_FIELDS
    }
    config["CallbackURLs"] = _appended(config.get("CallbackURLs"), callback)
    config["LogoutURLs"] = _appended(config.get("LogoutURLs"), logout)

    if len(config["CallbackURLs"]) > MAX_CALLBACK_URLS:
        raise ProvisionError(
            f"the shared Cognito client already holds {MAX_CALLBACK_URLS} "
            "callback URLs; an app must be removed before another is created"
        )
    return config


def callback_url(host: str) -> str:
    return f"https://{host}/oauth2/idpresponse"


def logout_url(host: str) -> str:
    return f"https://{host}"


def _appended(existing: Any, value: str) -> list[str]:
    urls = [str(u) for u in (existing or ()) if str(u)]
    if value not in urls:
        urls.append(value)
    return urls


class Boto3Clients:
    """Adds a created app's URLs to the ONE shared app client."""

    def __init__(
        self,
        client: Any,
        *,
        user_pool_id: str,
        client_id: str,
        log: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._user_pool_id = user_pool_id
        self._client_id = client_id
        self._log = log or logging.getLogger("proxy.provision")

    async def add_host(self, host: str) -> None:
        callback = callback_url(host)
        logout = logout_url(host)

        before = await self._describe()
        existing = [str(u) for u in (before.get("CallbackURLs") or ())]
        if callback in existing:
            return  # idempotent: a retry must not rewrite the client

        await asyncio.to_thread(
            self._client.update_user_pool_client,
            **merged_client_config(before, callback=callback, logout=logout),
        )

        # Verify. The whole risk of this call is that it succeeds and quietly
        # drops something; a 200 is not evidence. Compare what came back
        # against what was there before.
        after = await self._describe()
        survivors = {str(u) for u in (after.get("CallbackURLs") or ())}
        lost = [url for url in existing if url not in survivors]
        if lost or callback not in survivors:
            raise ProvisionError(
                "the shared Cognito client was not updated correctly "
                f"({len(lost)} callback URL(s) missing afterwards)"
            )
        self._log.info(
            "cognito callback added",
            extra={"host": host, "callbacks": len(survivors)},
        )

    async def _describe(self) -> dict[str, Any]:
        response = await asyncio.to_thread(
            self._client.describe_user_pool_client,
            UserPoolId=self._user_pool_id,
            ClientId=self._client_id,
        )
        return dict(response.get("UserPoolClient") or {})


def _error_code(exc: BaseException) -> str:
    """A botocore error code, without importing botocore into this module."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or "")
    return ""


__all__ = [
    "Boto3Builds",
    "Boto3Clients",
    "Boto3Repositories",
    "Boto3Services",
    "Boto3TaskRoles",
    "Boto3Uploads",
    "BUILD_ENV_OVERRIDES",
    "BUILD_ENV_PROJECT_STATIC",
    "BuildStatus",
    "BuildWatcher",
    "Creation",
    "DEFAULT_PREFIX",
    "FIRST_RELEASE_TAG",
    "ProvisionError",
    "Provisioner",
    "STUCK_BUILD_SECONDS",
    "UPLOAD_URL_SECONDS",
    "build_status",
    "callback_url",
    "lifecycle_policy",
    "logout_url",
    "merged_client_config",
    "portal_tags",
    "portal_tags_ecs",
]
