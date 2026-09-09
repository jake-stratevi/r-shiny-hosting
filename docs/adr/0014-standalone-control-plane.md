# ADR-0014: A standalone Stratevi control plane — proxy and portal as one service

**Status:** Accepted (decision made 2026-09-09; not yet built)
**Date:** 2026-09-09

## Context

The platform works — both apps deployed, sleep cycle observed, Hub-pool auth
live — but onboarding an app is an engineer's job: copy a Terraform directory,
hand-pick a listener priority, edit two allowlists that must stay in sync, and
apply. The goal is a client-facing product where a non-engineer adds a
dashboard or model through a UI, sets how long it lives, and controls who sees
it.

ADR-0008 ("later"), ADR-0012 and ADR-0013 all pointed at two candidate
endgames: an always-on authorizing proxy, or repointing the Assembled Work
(assembled.work) Laravel preview portal at these containers. A full read of
the assembled.work codebase (September 2026) confirmed its domain model is
almost exactly what we need — `PreviewApp` / immutable `PreviewRelease`,
required expiry with an hourly reaper and idempotent reminders, a scheduler
heartbeat, five access modes including client magic links, an audit log, and a
4-step creation wizard — but its runtime stack is nothing we run: Laravel/PHP,
nginx, oauth2-proxy, Cloudflare tunnels, Redis/Horizon, publishing static
files by symlink swap on a single host.

Two decisions were also settled by Jake on 2026-09-09:

- **Data residency:** client data and the hosting of R models must stay inside
  the Stratevi AWS account (652063276768). assembled.work is another team's
  platform shared across client orgs.
- **Buy versus build:** build. Posit Connect (five-figure annual licence) is
  rejected; the magic-link client-access pattern it cannot do is a real
  requirement for pharma clients who will not federate for a single dashboard.

## Decision

Build a standalone Stratevi control plane. **Port the assembled.work domain
model; port none of its code.**

One always-on Fargate service (small, ~$18–25/month) plays both roles:

1. **Authorizing proxy** — terminates every `*.tools.stratevi.com` app
   hostname behind a single ALB listener rule, resolves the app from the
   `Host` header, checks the caller (identity from the ALB's
   `authenticate-cognito` headers, same claims-fallback order as `access.R`)
   against an entitlements database, wakes sleeping services itself, emits
   audit events, and proxies to the app's task. Fails closed.
2. **Portal / admin UI** — on its own hostname: the app-creation wizard
   (details → upload → access → expiry → review), release management, status
   badges (starting / awake / asleep / expired), entitlement editing, and the
   audit trail.

Provisioning follows ADR-0012: the portal creates ECR repositories, task
definitions and services through the AWS SDK. With the proxy in place that is
*all* it creates — no per-app target group, listener rule, DNS record or
priority registration, because the wildcard cert and the proxy's single rule
already cover every hostname.

Concepts ported from assembled.work, renamed for what they are here:

| assembled.work | Here |
|---|---|
| `PreviewApp` (slug, owner, access_mode, expires_at, disabled/hidden states) | App record in the entitlements DB |
| `PreviewRelease` (immutable, own status lifecycle, rollback = repoint) | Release = image tag + task-definition revision; rollback = repoint service |
| Status machine draft→validating→publishing→active→expired/deleted, `purge_after` | Same, plus awake/asleep from the scaler |
| `AccessMode`: AllStaff / Team / Users / Organizations / ClientMagicLink | Same enum; magic links deferred (see Consequences) |
| Hourly `ExpirePreviewApps` reaper + idempotent 7d/1d reminders + scheduler heartbeat | EventBridge-scheduled reaper: expire = scale to zero + block at proxy; same reminders; heartbeat surfaces on a health page |
| Symlink swap publish | Update service to new task-definition revision |
| Upload validation / malware scan | Advisory only — R apps are arbitrary code we choose to run; the real control is containment (per-app IAM, no NAT egress, capped compute) |

The proxy retires four things at once: the waker Lambda (the proxy wakes
services), the client-side heartbeat of ADR-0006 (the proxy sees every request,
so idle detection moves server-side), the ~50-app ALB target-group ceiling
(one listener rule for all apps), and the catalog.yaml/tfvars dual-list drift
of ADR-0013 (one database is the single source of truth).

This is Stratevi-branded: it takes the Stratevi design system, not the
Assembled Intelligence one.

## Alternatives considered

**Extend assembled.work (ADR-0012/0013's "eventual target").** Richest
existing model, ~70% built. Rejected: it is another team's shared multi-org
platform; extending it puts Stratevi's AWS infrastructure inside it, and
client data must stay in the Stratevi environment. Forking the codebase was
also rejected — the entire runtime stack (PHP, nginx, oauth2-proxy,
cloudflared, Redis) would be ripped out and replaced with ALB/Cognito/ECS
equivalents on day one, leaving only the domain model, which can be ported
without the code.

**Posit Connect, self-hosted.** The name a pharma security team recognises.
Rejected: five-figure annual licence, and no equivalent of client magic-link
access.

**ShinyProxy.** Rejected in ADR-0008 for requiring an always-on host sized for
peak concurrency; unchanged.

## Consequences

Adding an app becomes a wizard action measured in seconds plus an image build,
with expiry and access recorded in one place and enforced at one gate.

Fixed monthly cost rises by the proxy task (~$18–25) on top of the ALB's ~$20.
In exchange, per-app denial no longer costs a cold start, and audit is
per-request.

Two systems provision AWS resources (Terraform for the platform, the portal's
SDK for apps) — the ADR-0012 boundary must be respected: if a human edits it,
Terraform owns it.

The proxy is in the request path for everything; it must be boring — two tasks
across two AZs, health-checked, with the 404/expired/starting pages served
even when the database is unhappy.

Magic-link client access (separate un-SSO'd hostname, OTP, device binding,
per-request revocation) is the hardest part of the assembled.work model and is
**deliberately deferred** until a client contract needs it. The `AccessMode`
enum ships with the value from day one so the schema doesn't change later.

ADR-0006 (heartbeat), the waker halves of ADR-0002, and ADR-0013 (the Lambda
portal) are retired when this ships. Each app's in-app allowlist (ADR-0008
"now") stays as defence in depth behind the proxy.
