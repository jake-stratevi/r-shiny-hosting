# ADR-0002: Scale to zero via waker and sleeper Lambdas

**Status:** Accepted
**Date:** 2026-09-01

## Context

These apps are used in bursts — a few hours a week each, concentrated in
working hours. Always-on Fargate for the microsimulation alone is $170/month at
4 vCPU / 16 GB. Paying that for a tool used twenty hours a month is indefensible.

An ALB returns 503 when a target group has no healthy targets; it does not fail
over to another target group. So something has to intercept the first request
and start the task.

## Decision

ECS services run at `desired_count = 0`. Each app has two target groups: the
real ECS group, and a Lambda group holding a "starting up" page.

The listener rule forwards to the waker Lambda while the app sleeps. On an
authenticated request the waker sets desired count to 1, returns a holding page
with a meta-refresh, and on a later refresh — once a target is healthy — rewrites
the listener rule to point at the ECS group. The browser's own refresh is the
polling loop, so the Lambda never blocks.

A sleeper Lambda runs every five minutes, scales idle apps back to zero, and
flips the rule back.

Authentication runs **before** the waker, so an unauthenticated visitor cannot
trigger a 4 vCPU task to start.

## Consequences

The microsimulation drops from ~$170/month to roughly $10. This single decision
is the difference between the platform being viable and not.

Terraform and the Lambdas co-own two attributes — the listener rule's `action`
and the service's `desired_count`. Both carry `ignore_changes`. Removing those
lifecycle blocks makes every `apply` fight the scaler.

Users pay a 45–90 second cold start on first access. Mitigated for the dashboard
with a weekday warm window; unmitigated for the model, deliberately, because
that is where the savings are.

Cold start is dominated by image size. A `rocker/r-ver` image with renv-pinned
packages is 2–3 GB.

The mechanism has never been observed working end to end. See STATUS.md.
