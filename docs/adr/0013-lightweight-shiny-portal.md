# ADR-0013: A lightweight Lambda portal at dashboards.tools.stratevi.com

**Status:** **Retired 2026-09-10.** It did its job: it was the menu for two months while the real control plane was built. The `portal/` stack (Lambda, target group, listener rule 50, its own Cognito client, DNS record) is destroyed, `catalog.yaml` is deleted, and `dashboards.tools.stratevi.com` now resolves through the wildcard to the [ADR-0014](0014-standalone-control-plane.md) proxy, which serves the React portal there. Everything below is history — the "Consequences" section's warning about two hand-synced lists is exactly the problem this retirement closes: entitlements now live once, in `shiny-proxy-apps`.
**Date:** 2026-09-08

## Context

With a second app (`model`) about to exist alongside `dashboard`, there is no
single place a user goes to see what they're allowed to use — they need to be
handed each app's own subdomain directly. ADR-0008 and ADR-0012 both already
name the eventual target for this: either an always-on authorizing proxy that
collapses every app onto one listener rule, or repointing the existing
Assembled Work (assembled.work) Laravel preview portal — which already models
exactly this (`AccessMode`: `AllStaff`, `Team`, `Users`, `Organizations`,
`ClientMagicLink`) — at these containers instead of static file releases.

Both of those are real projects: the proxy is roughly $18/month and removes
the waker Lambda entirely (a genuine architecture change); repointing
Assembled Work means extending a platform another team owns, used by multiple
client orgs beyond Stratevi. Neither is scoped or scheduled. Waiting for
either blocks the immediate, much smaller need: a landing page that shows each
person only the apps they can see.

## Decision

Add a fourth stack, `portal/`, that is not a Shiny app at all: a single AWS
Lambda behind the same ALB + Cognito `authenticate-cognito` pattern every app
already uses, serving a small HTML menu at `dashboards.tools.stratevi.com`.
On sign-in it reads the caller's email from the identity the ALB injects (same
mechanism as `access.R`, ADR-0008) and shows a tile, linking to that app's own
subdomain, for each app in a shared catalog (`catalog.yaml` at the repo root)
that lists that email.

This does not touch how any app enforces access — `dashboard` and `model`
keep deciding for themselves who may actually use them, unchanged. The portal
only decides what to display. Hiding a tile is a convenience; the real gate
stays where ADR-0008 put it.

Every dashboard and model keeps its own subdomain, ECS service and Cognito
client exactly as today. This does not collapse anything onto one host or
path — that would mean reworking the ALB rule pattern debugged today, for no
benefit at this scale.

## Alternatives considered

**Wait for the authorizing proxy (ADR-0008 "Later").** The more complete fix
— it also removes the waker Lambda and the ~50-app ALB target-group ceiling.
Rejected for now: unscoped, no timeline, and the team needs a menu today with
two apps, not eventually with fifteen.

**Repoint Assembled Work at these containers (ADR-0012 "eventual target").**
The richest model — access modes, an admin UI, audit logging, already built
and running. Rejected for now: it is another team's platform, shared across
client orgs, and extending it to provision/gate ECS-hosted Shiny apps (it
currently only handles static file releases) is a project of its own, not a
today task.

**A real Shiny app as the portal**, reusing the dashboard/model Terraform
module verbatim. Simpler to write by copy-paste, but it's an ECS service
with a target group and a waker/sleeper Lambda for a page that is a static
menu — fixed overhead for something that never needs to "wake up." Rejected
in favor of the Lambda, which has no idle cost and no target-group ceiling
concern at this scale.

## Consequences

One new stack, no new fixed monthly cost (the ALB is already shared and
paid for; the Lambda's own cost for a few page loads a day is fractions of a
cent).

**The catalog and each app's own allowlist are two lists that must currently
be kept in sync by hand.** `catalog.yaml` decides what the portal shows;
`dashboard/terraform.tfvars` and `model/terraform.tfvars` decide what each app
actually allows. They agree today because the portal's catalog was copied
from the existing tfvars when this stack was built. If they drift, the portal
is wrong about what it shows — not about who gets in, since each app still
enforces its own list regardless of what the portal displays. The fix,
deferred rather than done now to avoid touching already-working stacks in the
same session this was written: have `dashboard/terraform.tfvars` and
`model/terraform.tfvars` read `allowed_emails` from `catalog.yaml` via
`yamldecode(file("../catalog.yaml"))` instead of declaring it twice.

Adding a third app means: add it to `catalog.yaml`, copy the `dashboard/`
directory as usual (per the existing pattern this doc doesn't change), and
register its `listener_rule_priority`. No change to `portal/` itself.

This is an interim step, not a replacement for either alternative above. It
should be revisited — and likely retired — the moment either the authorizing
proxy or the Assembled Work integration actually gets built.
