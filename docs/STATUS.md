# Current state

Last updated: 9 September 2026 (evening — proxy stack live, model hard off).

Read this before touching anything. It records what is actually deployed, what
is half-done, and what has never been tested.

## Deployed and verified

**Phase 1 — DNS delegation. Complete.**
`tools.stratevi.com` is delegated from Google Cloud DNS to Route 53 zone
`Z07112442ZAIA7CFJKV72` via four NS records added by IT.

**IAM. Complete.**
`ShinyPlatformDeploy` customer-managed policy attached to
`arn:aws:iam::652063276768:user/Stratevi_Testing`. All 17 preflight checks pass.

**Platform stack. Applied.**

| Resource | Identifier |
|---|---|
| VPC | `vpc-054c7e1c8535ba747` (10.40.0.0/16) |
| Public subnets | `subnet-03032650cb0d36d9c` (1a), `subnet-0d8046451b51463cc` (1b) |
| ALB | `shiny-alb`, suffix `app/shiny-alb/4c68c1633e2eebc5` |
| Wildcard cert | `*.tools.stratevi.com`, ACM `5d882983-…` |
| HTTPS listener | Default action: 404 fixed response |
| ECS cluster | `shiny-cluster`, FARGATE capacity provider |
| IAM roles | `shiny-task-execution`, `shiny-task`, `shiny-scaler` |
| SSM exports | Parameters under `/shiny/platform/` |
| Budget | `shiny-monthly`, $75, alerts to jake@stratevi.com |

**Auth: dedicated user pool (ADR-0015, supersedes ADR-0007). Live 2026-09-10.**
The platform owns pool `us-east-1_LI3CZpwAF` (`shiny-platform`): invite-only,
email sign-in, managed login v2, hosted UI `stratevi-shinyplatform`. The
proxy's shared client lives in this pool; the login-page branding
association is applied (see GOTCHAS — every new client needs one).

**Entra federation is live** (`Microsoft365`, added by hand in the console —
unmanaged drift, its client secret exists only in the Cognito API and is
overdue for rotation). Staff sign in with the Microsoft button and are
auto-provisioned on first sign-in; external clients get native
admin-created accounts. **Terraform declares no users** — the four seeded
staff accounts were deleted once federation worked, because email is the
pool's username and a native account collides with the same person's
federated identity. See RUNBOOK.md "User administration", including the
break-glass procedure (there is no standing native admin).
**Sequencing trap:** the ADR-0013 Lambda portal stack and the un-migrated
model stack were last applied against the old Hub pool and still work — do
NOT re-apply either; retire/migrate them instead (see platform/ssm.tf's
banner). Their leftover clients in the Hub pool get deleted by hand at
retirement.

**Dashboard stack. Deployed, in use — and MIGRATED TO THE PROXY (2026-09-09,
`proxied = true`).** The full lifecycle was verified live on a rehearsal host
and then on the real hostname: proxy `wake` on request (branded starting
page), `allow` with the caller's email in the audit table, server-side `sleep`
after 15 idle minutes with no heartbeat involved. The legacy machinery (both
listener rules, both target groups, waker/sleeper Lambdas, per-app Cognito
client, CloudWatch alarm + dashboard) is destroyed; the proxy is the only
scaler. Its row in `shiny-proxy-apps` carries the 8-address allowlist,
`idle_minutes = 15`, `max_session_hours = 12`. Users re-authenticate once
(new shared auth client); pre-warm no longer exists (~$7.60/month saved,
first morning visitor sees the ~30–60s starting page instead). The heartbeat
snippet is still in the image — harmless, remove at next rebuild
(ADR-0006 is retired for this app). **Soak period: watch
`shiny-proxy-audit` and awake-hours for a few days before migrating model.**

**Portal stack. Deployed and in use.**
Live at https://dashboards.tools.stratevi.com (verified 2026-09-09: 302 to the
Hub pool's hosted UI). Serves the per-user app menu from `catalog.yaml` per
ADR-0013.

**Model stack. Applied — hard off. See "Broken right now".**
`shiny-model` service exists but is deliberately held at 0/0.

**Proxy stack. Deployed and live (evening of 2026-09-09).**
`shiny-proxy` service ACTIVE, 1/1, healthy behind the ALB on the catch-all
listener rule at priority 5000. Wildcard DNS for app hostnames is live, the
image is pushed, and both DynamoDB tables it depends on exist:
`shiny-proxy-apps` (routing) and `shiny-proxy-audit` (per-request log).
**dashboard is cut over to it** (see the dashboard entry above); model
follows once its image exists. See [RUNBOOK.md](../RUNBOOK.md#proxy-operations)
for the migration checklist and [ADR-0014](adr/0014-standalone-control-plane.md)
for why it exists.

**Portal P1. Live at https://shinyplatform.tools.stratevi.com (2026-09-10;
renamed from proxy.tools same day).** React SPA served by the proxy service:
entitlement-filtered app menu (assembled.work-style card workspace, Stratevi
branding), admin area (app list with live state, per-app settings editing —
audited — and the audit viewer). Admins: the `__config__` row in
shiny-proxy-apps, Terraform-owned (proxy/portal.tf). Idle policy per Jake:
dashboards 20 min, models 10 min. The ADR-0013 Lambda portal still serves
dashboards.tools.stratevi.com until Jake blesses the new UI; switching that
hostname (and retiring portal/ + catalog.yaml) is the next small step. P2
(creation wizard + provisioning) and P2.5 (user management against the new
pool) are specced in docs/design/portal.md.

## Pending, half-done

**The ADR-0013 Lambda portal retirement is staged but not applied.** The
proxy's shared Cognito client already accepts the
`dashboards.tools.stratevi.com` callback, and a reviewed destroy plan for
the `portal/` stack (11 resources, incl. listener rule 50, the Lambda, and
its old Hub-pool client) is saved at `portal/retire.tfplan`. Until it is
applied, rule 50 still wins and that hostname still serves the OLD Lambda
menu. Apply it with `terraform apply -input=false retire.tfplan` from
`portal/`; the wildcard DNS record already covers the hostname, so removing
the stack's own A-record causes no gap. **Afterwards:** delete the repo-root
`catalog.yaml` (nothing reads it once that Lambda is gone — `seed.py` takes
`--catalog` and fails loudly rather than silently), and confirm
dashboards.tools.stratevi.com serves the React portal.

## Broken right now

**The `shiny-model` ECR repository still has no image — and the service is
now hard off rather than retry-looping.** The previous state here was
`CannotPullContainerError: shiny-model:latest not found`: someone hit the
model's URL, the waker scaled it up, and the task could never place. That's
now moved from "harmless but noisy" to "turned off on purpose" — the waker's
`waker_enabled` tfvar is set `false` for the model, its Lambda's reserved
concurrency is 0 (so it can't even attempt to invoke), and the service sits at
desired 0 / running 0. `model.tools.stratevi.com` is a dead link until the
model image is built and pushed *and* `waker_enabled` is flipped back. Building
the image needs the full app directory (`ui.R`, `global.R`,
`Rcode_Packages.R`, `Rcode_HelperFunctions.R`, `Images/`, `www/`) plus the R
changes in [ADR-0006](adr/0006-heartbeat-idle-detection.md) and
[ADR-0011](adr/0011-fargate-cpu-detection.md).

## Direction

The buy-versus-build and extend-versus-own questions are settled: **build a
standalone Stratevi control plane** — an always-on authorizing proxy plus a
self-service portal, porting the assembled.work domain model but none of its
code. See [ADR-0014](adr/0014-standalone-control-plane.md) and
[ROADMAP.md](ROADMAP.md).

## Foundations (Step 2) — deployed 9 September 2026

- **Terraform state in S3** (ADR-0009, now Accepted): bucket
  `stratevi-tf-state-652063276768`, versioned, encrypted, S3 native locking.
  All four stacks migrated and verified (resource addresses identical to the
  pre-migration local state). Local `.tfstate.bak` files remain in each stack
  directory as a belt-and-suspenders copy; delete when comfortable.
- **Per-app IAM task roles** (ADR-0010, now Accepted): live task definitions
  verified running as `shiny-dashboard-task` / `shiny-model-task`. The shared
  `shiny-task` role is deleted; only the execution role is shared, by design.
- **ALB access logs** to `shiny-alb-logs-652063276768`, 90-day expiry,
  verified enabled on the load balancer.
- The deployment policy is at **v4**: S3 broadened to the state bucket +
  `shiny-*` buckets, `iam:ListInstanceProfilesForRole` added (the provider
  calls it before any role delete), and — new for the proxy stack —
  DynamoDB access to the `shiny-*` tables plus
  `ec2:ModifySecurityGroupRules` (the proxy's security group needs rule
  updates that a plain `Authorize`/`Revoke` pair doesn't cover).

## Current run-rate

Fixed monthly cost is now **≈ $29**: ALB ~$19.93 (unchanged, it never sleeps)
plus the proxy task ~$9 (it's always-on by design — that's the tradeoff for
seeing every request server-side) plus cents for DynamoDB and the hosted
zone. The model being hard off removes its Fargate cost entirely rather than
just capping it, since the service can no longer be woken at all.

## Known-fragile

Ordered by how likely they are to cause an incident.

1. **Idle detection depends on client-side JavaScript.** If the heartbeat is
   removed from an app's UI, or a browser blocks it, the sleeper will scale a
   task to zero underneath an active user. See
   [ADR-0006](adr/0006-heartbeat-idle-detection.md). Retired by the ADR-0014
   proxy, which sees every request server-side.
2. **Access control is two hand-synced lists per app.** `catalog.yaml` decides
   what the portal shows; each app's `terraform.tfvars` decides who gets in
   (ADR-0008 allowlist). Drift means the menu lies. Retired by ADR-0014, which
   moves entitlements to one database.
3. **App data still lives inside container images** — updating data means a
   rebuild, and per-app roles have nothing to scope to until it moves to S3
   per-app prefixes (roadmap Step 2 remainder / Step 5 prep).
