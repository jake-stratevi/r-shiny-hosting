"""The Stratevi authorizing proxy (ADR-0014, docs/design/proxy.md).

One always-on aiohttp service that fronts every ``*.tools.stratevi.com`` app
hostname: it resolves the app from the Host header, decides whether the caller
may use it, wakes the app's ECS service if it is asleep, reverse-proxies to the
task (HTTP and websockets), and scales idle apps back to zero.

Module map -- the split exists so the decision logic is testable without AWS:

    config     The environment contract and JSON logging.
    identity   x-amzn-oidc-* parsing. Mirrors access.R and the portal Lambda.
    registry   App rows, host normalization, DynamoDB store, ~10s read cache.
    access     The entitlement decision. Pure, fails closed.
    ecsctl     Task-IP discovery, wake, sleep, with caches.
    activity   Requests + open websockets; persists last_active.
    audit      Best-effort, never-blocking audit recorder.
    pages      The six embedded branded pages.
    sleeper    The 60s sleeper/reaper background loop.
    server     The request path and the reserved /__proxy/ endpoints.

Only ``registry``, ``ecsctl`` and ``audit`` contain boto3 calls, and in each of
those the AWS class sits behind a small async protocol the rest of the code
depends on instead.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
