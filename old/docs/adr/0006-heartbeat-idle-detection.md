# ADR-0006: Client-side heartbeat for idle detection

**Status:** Accepted, known fragile
**Date:** 2026-09-01

## Context

The sleeper needs to know whether anyone is using an app. This is harder than it
looks.

A Shiny session holds an open WebSocket. Once established, it generates **zero**
HTTP requests. A user sitting on a page for twenty minutes, or waiting through a
long `parLapply` run, is invisible to `RequestCountPerTarget`.

Per-target-group active connection metrics do not exist — `ActiveConnectionCount`
is load-balancer-wide, and with two apps sharing one ALB it cannot tell them
apart. CPU utilisation cannot distinguish a user reading a chart from an empty
container.

## Decision

Each app's UI includes a five-line JavaScript snippet that fires a `HEAD`
request once a minute:

```r
tags$head(tags$script(HTML("
  setInterval(function () {
    fetch(window.location.pathname + '?heartbeat=' + Date.now(),
          { method: 'HEAD', cache: 'no-store' });
  }, 60000);
")))
```

The sleeper reads `RequestCountPerTarget` over the app's idle window and scales
to zero when it is zero. A `min_uptime_minutes` floor protects a task that just
started and has no datapoints yet.

## Alternatives considered

**Server-side custom CloudWatch metric** from `session$onSessionStart` /
`onSessionEnded`. More robust — Shiny knows exactly how many sessions it has.
Rejected because it adds an AWS SDK dependency to every app's renv lockfile and
custom metrics cost money.

**Two ALBs, one per app**, to make `ActiveConnectionCount` per-app. $16.43/month
per extra ALB.

## Consequences

Idle detection works, uses only free metrics, and needs no R packages.

**It depends on client-side JavaScript in every app.** If the snippet is omitted,
an extension blocks it, or a future app is added without it, the sleeper will
scale a task to zero underneath an active user. This is the most fragile thing
in the platform.

The heartbeat doubles as a usage signal — "is anyone actually using this" becomes
a readable metric.

The authorizing proxy in ADR-0008 removes this problem entirely, because the
proxy holds the WebSocket and knows who is connected as a fact rather than an
inference. That is one of the stronger arguments for building it.
