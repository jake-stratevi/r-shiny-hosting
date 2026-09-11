"""Entry point: ``python -m proxy_app``.

Wires the environment contract to boto3 clients, the caches, the audit
recorder, the sleeper loop and the aiohttp server, then serves until SIGTERM.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

import boto3
from aiohttp import web
from botocore.config import Config as BotoConfig

from . import activity as activity_mod
from . import audit as audit_mod
from . import config as config_mod
from . import ecsctl, portal as portal_mod, provision, registry, server, signout, sleeper
from . import usage as usage_mod

#: Both short enough that a revoked entitlement or a replaced task is picked up
#: within seconds, long enough that a page full of assets is one lookup rather
#: than forty.
REGISTRY_CACHE_TTL = 10.0
TASK_CACHE_TTL = 10.0

SHUTDOWN_GRACE = 15.0


async def serve(cfg: config_mod.Config, log: logging.Logger) -> None:
    session_kwargs = {"region_name": cfg.region} if cfg.region else {}
    boto_session = boto3.session.Session(**session_kwargs)
    dynamodb = boto_session.client("dynamodb")
    ecs = boto_session.client("ecs")

    apps = registry.CachedRegistry(
        registry.DynamoAppStore(dynamodb, cfg.apps_table, log.getChild("registry")),
        REGISTRY_CACHE_TTL,
    )
    tasks = ecsctl.Controller(
        ecsctl.Boto3EcsBackend(ecs, cfg.cluster), TASK_CACHE_TTL
    )
    tracker = activity_mod.Tracker(apps, log.getChild("activity"))

    recorder = audit_mod.Recorder(
        audit_mod.DynamoAuditSink(dynamodb, cfg.audit_table), log.getChild("audit")
    )
    recorder.start()

    # The P2a creation pipeline. All-or-nothing (config.Creation), so this is
    # either fully wired or entirely absent -- there is no half-configured
    # mode in which an app can be half-created.
    creation = None
    watcher = None
    if cfg.creation is not None:
        creation, watcher = _creation(cfg, boto_session, apps, recorder, log)
    elif cfg.creation_error:
        # Deliberately not fatal -- this task is in the request path for every
        # app, and a misconfigured wizard must not take the platform down.
        # Loud, though: the alternative is a "+ New app" button that has
        # quietly stopped appearing and nobody knowing why.
        log.error(cfg.creation_error)

    loop = sleeper.Loop(
        apps, tasks, tracker, recorder, log.getChild("sleeper"), builds=watcher
    )
    loop_task = asyncio.get_running_loop().create_task(loop.run())

    # Off unless PORTAL_HOSTS names something. With no portal hosts this
    # service is byte-for-byte the proxy it was before ADR-0014's second half.
    portal = None
    if cfg.portal_enabled():
        portal = portal_mod.Portal(
            apps=apps,
            tasks=tasks,
            admins=portal_mod.AdminList(
                apps.admin_emails, log=log.getChild("portal")
            ),
            creators=portal_mod.CreatorList(
                apps.creator_emails, log=log.getChild("portal")
            ),
            # ANDed with both lists above: named in admin_emails or
            # creator_emails is necessary but not sufficient, the address
            # also has to be staff. Falls back to
            # registry.DEFAULT_STAFF_DOMAINS when the row does not say --
            # see that constant for why this one does not fail closed.
            staff=portal_mod.StaffDomains(
                apps.staff_domains, log=log.getChild("portal")
            ),
            creation=creation,
            # The awake-hours ledger reads the wake/sleep events the recorder
            # above writes, and stores its daily rollups in the SAME table --
            # so it needs no new infrastructure and no new IAM (the task role
            # already has Query and PutItem there). See usage.py.
            usage=usage_mod.UsageLedger(
                usage_mod.DynamoUsageStore(
                    dynamodb, cfg.audit_table, log.getChild("usage")
                ),
                log=log.getChild("usage"),
            ),
            recorder=recorder,
            audit=audit_mod.DynamoAuditReader(dynamodb, cfg.audit_table),
            dist=cfg.portal_dist,
            log=log.getChild("portal"),
        )

    client = server.make_session()
    proxy = server.Proxy(
        apps=apps,
        tasks=tasks,
        activity=tracker,
        recorder=recorder,
        session=client,
        ready=apps.ping,
        log=log.getChild("server"),
        portal=portal,
        portal_hosts=cfg.portal_hosts,
        signout=cfg.signout,
    )

    if cfg.signout_error:
        # Not fatal, same reasoning as creation_error: sign-out still expires
        # the ALB cookies, and refusing to boot over it would take every app
        # on the platform down. Loud, because the symptom -- "sign out does
        # nothing" -- is otherwise indistinguishable from an SSO quirk.
        log.error(cfg.signout_error)

    runner = web.AppRunner(
        server.create_app(proxy),
        # aiohttp sets no read/write deadline of its own; keepalive_timeout
        # only bounds an IDLE connection between requests, so it cannot cut a
        # long computation or an open websocket short.
        keepalive_timeout=120.0,
        access_log=None,
    )
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.port, shutdown_timeout=SHUTDOWN_GRACE)
    await site.start()

    log.info(
        "proxy listening",
        extra={
            "port": cfg.port,
            "cluster": cfg.cluster,
            "apps_table": cfg.apps_table,
            "audit_table": cfg.audit_table,
            "region": cfg.region or "(sdk default)",
            "log_level": cfg.log_level,
            "portal_hosts": list(cfg.portal_hosts) or "(portal off)",
            "portal_dist": cfg.portal_dist if cfg.portal_enabled() else "(portal off)",
            "creation": (
                cfg.creation.domain if cfg.creation else "(creation off)"
            ),
            "signout": (
                signout.hosted_ui(cfg.signout)
                if cfg.signout
                else "(cookies only -- no cognito logout)"
            ),
        },
    )

    await _wait_for_signal()
    log.info("shutting down")

    loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop_task
    await runner.cleanup()
    await recorder.stop()
    await client.close()


def _creation(
    cfg: config_mod.Config,
    boto_session: "boto3.session.Session",
    apps: registry.CachedRegistry,
    recorder: audit_mod.Recorder,
    log: logging.Logger,
) -> tuple[provision.Creation, provision.BuildWatcher]:
    """Wire the P2a pipeline. Five clients, one boundary, no optional bits.

    ``Boto3TaskRoles`` is constructed FIRST and refuses a blank boundary ARN,
    so a deployment that somehow reached here without one dies at startup
    rather than at the third step of somebody's first app.
    """
    settings = cfg.creation
    assert settings is not None

    roles = provision.Boto3TaskRoles(
        boto_session.client("iam"),
        boundary_arn=settings.role_boundary_arn,
        data_bucket=settings.data_bucket,
        log=log.getChild("provision"),
    )
    builds = provision.Boto3Builds(
        boto_session.client("codebuild"),
        boto_session.client("logs"),
        settings.codebuild_project,
        log.getChild("provision"),
    )
    provisioner = provision.Provisioner(
        store=apps,
        repositories=provision.Boto3Repositories(
            boto_session.client("ecr"), log.getChild("provision")
        ),
        roles=roles,
        builds=builds,
        services=provision.Boto3Services(
            boto_session.client("ecs"),
            cluster=cfg.cluster,
            subnets=settings.subnet_ids,
            security_group=settings.security_group_id,
            execution_role_arn=settings.execution_role_arn,
            log_group=settings.log_group,
            region=cfg.region,
            roles=roles,
        ),
        clients=provision.Boto3Clients(
            boto_session.client("cognito-idp"),
            user_pool_id=settings.user_pool_id,
            client_id=settings.client_id,
            log=log.getChild("provision"),
        ),
        recorder=recorder,
        domain=settings.domain,
        log=log.getChild("provision"),
    )
    bundle = provision.Creation(
        domain=settings.domain,
        uploads=provision.Boto3Uploads(
            # SIGNATURE VERSION 4, EXPLICITLY. Do not drop this.
            #
            # boto3's default for an S3 presigned URL against the global
            # endpoint is the legacy SigV2, and SigV2 puts Content-Type into
            # the string-to-sign. A browser ALWAYS sends a Content-Type for a
            # File (Chrome on Windows picks application/x-zip-compressed for
            # a .zip), the presigner signed an empty one, and S3 rejects the
            # PUT with 403 SignatureDoesNotMatch. curl sends no Content-Type
            # by default, so every command-line test of the same URL passes
            # and the bug looks like it is in the browser.
            #
            # SigV4 signs only `host` here, so whatever Content-Type the
            # browser chooses is irrelevant. Verified against the real bucket:
            # SigV2 + Content-Type = 403, SigV4 + the same header = 200.
            boto_session.client("s3", config=BotoConfig(signature_version="s3v4")),
            settings.uploads_bucket,
        ),
        provisioner=provisioner,
        builds=builds,
        denylist=portal_mod.DenyList(apps.key_denylist, log=log.getChild("portal")),
        console_region=cfg.region,
        codebuild_project=settings.codebuild_project,
    )
    watcher = provision.BuildWatcher(
        provisioner=provisioner, builds=builds, log=log.getChild("provision")
    )
    return bundle, watcher


async def _wait_for_signal() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows: add_signal_handler is unavailable, and the container
            # this runs in is Linux. Fall back so `python -m proxy_app` on a
            # laptop still stops on Ctrl-C.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    await stop.wait()


def main() -> int:
    try:
        cfg = config_mod.from_env()
    except config_mod.ConfigError as exc:
        # Logging is not configured yet, and a startup contract failure has to
        # be legible in `docker run` output.
        print(f"proxy: {exc}", file=sys.stderr)
        return 2

    log = config_mod.configure_logging(cfg.log_level)
    try:
        asyncio.run(serve(cfg, log))
    except KeyboardInterrupt:  # pragma: no cover
        pass
    except Exception:
        log.exception("proxy exited")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
