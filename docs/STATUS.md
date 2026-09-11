# Current state

Last updated: 11 September 2026 (legacy path retired).

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
| IAM roles | `shiny-task-execution`; `shiny-scaler` (unused, delete) |
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
The sequencing trap that lived here is closed: both stacks that still held
old-Hub-pool clients (the ADR-0013 Lambda portal and `model/`) are destroyed,
and their leftover clients went with them. Nothing in this repo points at the
Hub pool any more.

**The login page is branded** (`brand/cognito/`, applied 2026-09-11):
Stratevi logo, palette and copy via Managed Login v2 — a `settings.json` plus
ten base64 assets, applied with `brand/cognito/apply.ps1`. Terraform cannot
express this, so the branding is an out-of-band artefact that is
version-controlled and re-appliable rather than console-only. **Session
timeout is 3 hours** (`10800`, `proxy/alb.tf`), down from 12.

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
(ADR-0006 is retired for this app). Soaked without incident.

**Model stack. RETIRED 2026-09-11.** `model/` was destroyed (24 resources)
rather than migrated: that app now lives in the portal as
`microsimulation-model.tools.stratevi.com`, created through the wizard. Its
stale Cognito client in the old Hub pool went with it, and listener
priorities 200 and 900 are free.

**There is no legacy path left.** Both apps are proxied, no waker or sleeper
Lambda exists anywhere in the account, and the ALB listener holds exactly two
rules: 4900 (signed-out) and 5000 (the catch-all). ADR-0006 is retired;
ADR-0002's mechanism is superseded by the proxy, though its cost model stands.

**Proxy stack. Deployed and live (evening of 2026-09-09).**
`shiny-proxy` service ACTIVE, 1/1, healthy behind the ALB on the catch-all
listener rule at priority 5000. Wildcard DNS for app hostnames is live, the
image is pushed, and both DynamoDB tables it depends on exist:
`shiny-proxy-apps` (routing) and `shiny-proxy-audit` (per-request log).
**Everything is cut over to it.** See [RUNBOOK.md](../RUNBOOK.md#proxy-operations)
for the migration checklist and [ADR-0014](adr/0014-standalone-control-plane.md)
for why it exists.

**Portal P1. Live at https://shinyplatform.tools.stratevi.com (2026-09-10;
renamed from proxy.tools same day).** React SPA served by the proxy service:
entitlement-filtered app menu (assembled.work-style card workspace, Stratevi
branding), admin area (app list with live state, per-app settings editing —
audited — and the audit viewer). Admins: the `__config__` row in
shiny-proxy-apps, Terraform-owned (proxy/portal.tf). Idle policy per Jake:
dashboards 20 min, models 10 min. P2a (creation wizard + provisioning) is live — see below; P2.5 (user
management against the new pool) is still only specced, in
docs/design/portal.md.

**ADR-0013 Lambda portal: retired 2026-09-10.** The `portal/` stack is
destroyed (11 resources, incl. listener rule 50 and its stale client in the
old Hub pool) and `catalog.yaml` is deleted. `dashboards.tools.stratevi.com`
now resolves through the wildcard record to the catch-all rule and is served
by the React portal — verified: it redirects to the new pool with the shared
client and a valid `state`. Entitlements live in exactly one place now
(`shiny-proxy-apps`); the two-hand-synced-lists problem is closed. The
`portal/` directory can be deleted from the repo at any time; its state file
in S3 is empty.

**Portal P2a — self-service app creation. Deployed 2026-09-10, untested by a
real create.** The "+ New app" wizard is live at
https://shinyplatform.tools.stratevi.com for anyone in the `__config__`
row's `creator_emails` (currently Jake only; admin does NOT imply create).
Pipeline: browser inspects the zip locally → presigned PUT to
`shiny-portal-uploads-652063276768` → CodeBuild `shiny-app-build` renders
the bundle into the pinned rocker Dockerfile and pushes `shiny-<key>:r1` →
the portal SDK-provisions ECR repo, an IAM role under the
`shiny-app-boundary` permissions boundary, task definition, service at
desired 0, and the Cognito callback. Deploy policy is at **v6**
(codebuild + IAM policy management + PassRole to codebuild).
See docs/design/portal-p2a.md. **Proven end to end 2026-09-11** by creating
`microsimulation-model` from a real zip. **The hostname denylist is still the
placeholder set** (`acme`, `clientco`, `confidential`, `tarpeyo`) — Jake owes
the real brand/client terms before anyone names a client-facing app.

**Hostnames carry a random suffix** (2026-09-11). A created app's hostname is
`<slug>-<6 random chars>.tools.stratevi.com`, so a URL cannot be guessed from
a client's name even by someone who knows the naming convention. This is
defence in depth only — **entitlement is the real control**, checked by the
proxy on every request. See docs/design/portal-p2a.md for why referer checks
and token links were rejected.

**Framing protection is live** (`proxy-app/proxy_app/security.py`, verified on
the wire 2026-09-11): `Content-Security-Policy: frame-ancestors 'none'`,
`X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` on
every proxied response. An app link pasted into someone else's page will not
render.

**Costs page. Live 2026-09-11 (option B: awake-hours ledger).** The proxy
meters each app's awake seconds against its task size as it sleeps and wakes,
and the portal shows per-app and total spend. Deliberately NOT Cost Explorer:
its tags are not retroactive, lag a day, and cannot attribute shared cost.
Rates are pinned in `proxy-app/proxy_app/usage.py` ($0.04048/vCPU-hr,
$0.004445/GB-hr) and must be revisited if AWS repricing Fargate. The ALB and
proxy task are shown as the fixed base, unattributed, because they are.

**Portal front end reworked to match assembled.work (2026-09-10).** Their
token system verbatim (warm ink-on-paper, azure for focus/wayfinding only,
ink primaries), full dark mode with a light/dark/system toggle, a
collapsible icon rail that defaults collapsed, self-hosted Instrument Sans,
breadcrumbs, link chips with copy/open, icon metadata rows, and their
responsive list-column template. Reference notes in
docs/design/portal-visual-reference.md. **Sign-out works** — see proxy.md;
note the one unauthenticated listener rule at priority 4900, explained in
proxy/alb.tf.

## Broken right now

Nothing known. (The `shiny-model` empty-ECR problem that lived here is gone
with the stack — see the Model entry above.)

## Drift you need to know about

**`microsimulation-model` runs release `r2`; its DynamoDB row says `r1`.**
On 2026-09-11 a missing `library(parallel)` made every model run die at
`makeCluster`. The fix was rebuilt as `r2` and deployed by hand — CodeBuild
started with env overrides, task definition `shiny-microsimulation-model:3`
registered, `update-service` issued — because the portal has no release path
(ROADMAP Step 4b). Nothing wrote the new image back to the app row, so the
portal displays a release the task is not running. Revision 2 / `r1` still
exists; rollback is one `update-service`.

**`max_session_hours` for that app is 2, set by hand in the console.** The
1-hour default force-slept a live model run mid-computation on 2026-09-11
(audit event `force_sleep`). Two hours is a guess: stage 1 of 4 alone ran 38
minutes without finishing, and a full run has never been timed. Resuming
2026-09-14. Note the second, independent ceiling underneath it — the ALB's
`idle_timeout` is 3600s and cannot exceed 4000s, so a single blocking
`parLapply` longer than about 66 minutes drops the websocket no matter what
the session cap says. The fix for that one is app-side: chunk the
`parLapply` and call `incProgress` between chunks.

## Owed

- **Rotate the Entra client secret.** It is readable via
  `describe-identity-provider` and has been printed to a terminal more than
  once.
- **Import the `Microsoft365` IdP into Terraform.** Console-only today, so it
  is unmanaged drift that nothing would recreate.
- **The real hostname denylist.** Placeholders are shipped.
- **Regenerate `microsim`'s `renv.lock` with `renv::snapshot()`.** Three
  packages were added by hand at snapshot versions after a smoke test caught
  `shinyjs` missing; the lock is right but not derived.
- **Delete `aws_iam_role.scaler` and its SSM export** — nothing assumes it
  since the Lambdas went (see the note in platform/ssm.tf).
- **Remove the heartbeat snippet from `dashboard-app/`** at the next rebuild.

## Direction

The buy-versus-build and extend-versus-own questions are settled: **build a
standalone Stratevi control plane** — an always-on authorizing proxy plus a
self-service portal, porting the assembled.work domain model but none of its
code. See [ADR-0014](adr/0014-standalone-control-plane.md) and
[ROADMAP.md](ROADMAP.md).

## Foundations (Step 2) — deployed 9 September 2026

- **Terraform state in S3** (ADR-0009, now Accepted): bucket
  `stratevi-tf-state-652063276768`, versioned, encrypted, S3 native locking.
  All stacks migrated and verified (resource addresses identical to the
  pre-migration local state). Local `.tfstate.bak` files remain in each stack
  directory as a belt-and-suspenders copy; delete when comfortable.
- **Per-app IAM task roles** (ADR-0010, now Accepted): live task definitions
  verified running as `shiny-dashboard-task`; created apps get their own role
  under the `shiny-app-boundary` permissions boundary. The shared
  `shiny-task` role is deleted; only the execution role is shared, by design.
- **ALB access logs** to `shiny-alb-logs-652063276768`, 90-day expiry,
  verified enabled on the load balancer.
- The deployment policy reached **v4** at this point: S3 broadened to the
  state bucket + `shiny-*` buckets, `iam:ListInstanceProfilesForRole` (the
  provider calls it before any role delete), DynamoDB on the `shiny-*` tables
  and `ec2:ModifySecurityGroupRules` (a plain `Authorize`/`Revoke` pair
  doesn't cover the proxy's security group). **It is at v6 now** — v5/v6 added
  CodeBuild, IAM policy management and `iam:PassRole` to
  `codebuild.amazonaws.com` for P2a. Each version was written after an apply
  failed on exactly one missing action; assume the next stack needs another.

## Current run-rate

Fixed monthly cost is now **≈ $29**: ALB ~$19.93 (unchanged, it never sleeps)
plus the proxy task ~$9 (it's always-on by design — that's the tradeoff for
seeing every request server-side) plus cents for DynamoDB and the hosted
zone. Everything else is per awake-hour and now **metered** rather than
estimated — the portal's Costs page reads the proxy's awake-hours ledger, so
run-rate no longer has to be reasoned about from task sizes.

## Known-fragile

Ordered by how likely they are to cause an incident.

1. **The proxy is a single point of failure.** One always-on task in front of
   every app: if it is unhealthy, nothing is reachable. That is the accepted
   cost of seeing every request server-side (ADR-0014). The two failure modes
   that used to head this list — client-side heartbeats (ADR-0006) and
   hand-synced entitlement lists — are both gone.
2. **App creation is one-way in the UI.** P2a creates; releases, rollback and
   delete-purge are P2b. A failed create leaves resources behind that have to
   be cleaned up by hand, and the build screen does not yet distinguish a
   pre-build failure from a package or smoke-test failure.
3. **App data still lives inside container images** — updating data means a
   rebuild, and per-app roles have nothing to scope to until it moves to S3
   per-app prefixes (roadmap Step 2 remainder / Step 5 prep).
