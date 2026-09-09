# ADR-0007: Reuse the Assembled Hub Cognito pool

**Status:** Accepted — implemented September 2026 (old pool `us-east-1_AZmmbFBy0` deleted; all auth on `us-east-1_6vtAiYEpv`)
**Date:** 2026-09-08

## Context

The platform stack created its own Cognito user pool, `us-east-1_AZmmbFBy0`, on
the assumption that Entra federation would be configured fresh.

It then emerged that `us-east-1_6vtAiYEpv` — the Assembled Hub pool — already
exists, is already federated to Entra as `Microsoft365`, and already carries a
native directory for external partners.

The `auth.py` from the dashboards-mvp project documents several problems that
pool has already solved: federated users arriving with synthetic
`microsoft365_…` usernames, Entra not reliably populating `email` (requiring
fallback through `upn` and `preferred_username`), and federated users not
existing in the pool until first sign-in, which breaks group-based admin checks
on day one.

## Decision

Delete the pool this stack created. Reference the Hub pool by ID and construct
its ARN. Each app stack still creates its own app client in that pool, because
callback URLs are per-hostname.

## Consequences

One directory, one Entra app registration, one place to revoke someone's access
across the Hub, dashboards-mvp and the Shiny platform.

All of the federation edge cases above are already solved and debugged.

**This buys no authorization capability.** ALB's `authenticate-cognito` is
binary regardless of which pool backs it. See ADR-0008.

The ALB callback path is fixed at `/oauth2/idpresponse`, not the Hub's
`/oauth2callback` convention. The Shiny apps therefore cannot share an app
client with the Hub — each gets its own in the same pool, which is correct
anyway.

Users see the Cognito IdP chooser rather than going straight to Entra. ALB can
force an IdP via `authentication_request_extra_params`, but doing so would
remove the password form that external partners need. One hostname, one
behaviour.

Adding an app client to a pool owned by another team is additive and safe, but
if that pool is managed by another Terraform state, confirm nothing there prunes
app clients it doesn't recognise.

Must be done before anyone signs in to the new pool. Trivial now, painful later.
