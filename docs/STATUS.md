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

**Cognito split resolved (ADR-0007). Complete.**
The pool this stack originally created (`us-east-1_AZmmbFBy0`) has been deleted.
Auth runs on the Assembled Hub pool `us-east-1_6vtAiYEpv`, which is already
federated to Entra as `Microsoft365` and holds native accounts for external
partners. Verified 2026-09-09: `list-user-pools` shows only the Hub pool (plus
the unrelated `assembled-platform-dev-user-pool`), and the portal's sign-in
redirect goes to the Hub pool's hosted UI. Because Cognito hands back a
different email per identity provider, the same person appears twice in
allowlists (`@stratevi.com` native + `@assembledintelligence.co.uk` federated)
— keep both listed everywhere or the Microsoft365 sign-in path gets refused.

**Dashboard stack. Deployed and in use.**
`shiny-dashboard` service ACTIVE (verified 2026-09-09, desired 1 / running 1).
Image pushed to ECR 2026-09-08, tag `latest`. Live at
https://dashboard.tools.stratevi.com. **The sleep cycle has been observed
working** — the cost model in ADR-0002 is validated.

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
`shiny-proxy-apps` (routing) and `shiny-proxy-audit` (per-request log). No
apps have been cut over to it yet — see [RUNBOOK.md](../RUNBOOK.md#proxy-operations)
for the migration checklist and [ADR-0014](adr/0014-standalone-control-plane.md)
for why it exists.

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
