# ADR-0008: Authorization — in-app allowlist now, authorizing proxy later

**Status:** Accepted — the "now" allowlist is live; the "later" proxy is scoped and committed in [ADR-0014](0014-standalone-control-plane.md)
**Date:** 2026-09-08

## Context

ALB's `authenticate-cognito` action answers one question: did this person sign
in to this user pool. There is no group condition, no allowlist, no per-rule
authorization. Every authenticated user can reach every app behind the load
balancer.

That is adequate for two internal apps. It fails the moment one client should
not see another client's dashboard.

## Decision

**Now:** each app reads the identity ALB injects and checks it against an
allowlist supplied as a task-definition environment variable.

ALB injects `x-amzn-oidc-identity`, `x-amzn-oidc-accesstoken` and
`x-amzn-oidc-data`. Note that `x-amzn-oidc-data` is built from the IdP's
userinfo endpoint and therefore does **not** contain `cognito:groups` — group
claims live in the access token and must be decoded separately.

**Later:** an always-on authorizing proxy — two small Fargate tasks, roughly
$18/month — sitting between the ALB and the app tasks, performing entitlement
lookup, emitting audit events, and proxying to the app.

## Alternatives considered

**ShinyProxy** — purpose-built for this. Per-app `access-groups` in one config
file, per-user container isolation, an app catalogue that shows each user only
what they are entitled to. Rejected for now because it requires an always-on
host sized for peak concurrency, which partly undoes ADR-0002. Reconsider above
roughly 20 apps.

**The existing Laravel preview portal**, repointed at containers. Richest model
— `AccessMode` already covers AllStaff, Team, Users, Organizations and client
magic links, with an administration UI. The most work, and the eventual target.

## Consequences

The in-app allowlist is about 15 lines of R and one Terraform variable per app.
It gives real per-app control today and forecloses nothing.

Its weakness is administrative, not technical: granting access means editing a
variable and redeploying. Fine at three apps, intolerable at fifteen with client
turnover. **The trigger for building the proxy is administrative load, not app
count.**

In-app denial still costs a cold start — the app must be running to refuse
someone. Annoying, cheap, and eliminated by the proxy.

The proxy also removes the waker Lambda (it starts tasks itself), removes the
heartbeat dependency from ADR-0006, and removes the ~50-app ALB target-group
ceiling. Three hacks retired by one component.
