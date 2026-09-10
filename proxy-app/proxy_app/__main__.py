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

from . import activity as activity_mod
from . import audit as audit_mod
from . import config as config_mod
from . import ecsctl, portal as portal_mod, registry, server, sleeper

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

    loop = sleeper.Loop(
        apps, tasks, tracker, recorder, log.getChild("sleeper")
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
    )

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
