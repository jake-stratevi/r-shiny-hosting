# Authorizing proxy — design spec (ADR-0014, first half)

Status: agreed 2026-09-09. This document settles the build decisions; ADR-0014
records why the proxy exists at all.

## Shape

One Python service running as an always-on ECS Fargate service in the
existing `shiny-cluster`. It is both the entitlement gate and the router for
every app hostname, and later grows the portal/admin API (ADR-0014 second
half). New top-level dirs:

```
proxy-app/   Python source + Dockerfile + catalog seed tool
proxy/       Terraform stack (same SSM-contract pattern as the app stacks)
```

## Decisions

**Language: Python 3.12 + aiohttp.** Revised 2026-09-09 from Go, at Jake's
call: every service a human maintains in this repo is already Python (waker,
sleeper, portal Lambda), and the person on the hook at 2am should be able to
read the proxy. aiohttp's client/server pair does async reverse proxying
including websocket pass-through; at this platform's traffic (a handful of
concurrent users) Go's performance edge buys nothing. boto3 for AWS.
`python:3.12-slim` image, non-root user. A Go implementation was fully built
first and is parked outside the repo as reference; the normative details it
settled are the "Contract details" section below.

**Storage: DynamoDB, on-demand.** Zero fixed cost, no VPC endpoints needed
(tasks have public IPs per ADR-0004; traffic to DynamoDB stays on AWS's edge).
Tables:

- `shiny-proxy-apps` — one item per app: `host` (PK), `app_key`,
  `ecs_service`, `container_port` (default 3838), `status`
  (active|disabled|expired), `access_mode` (all_users|users; the enum also
  reserves team/organizations/client_magic_link for the portal phase),
  `allowed_emails` (string set), `idle_minutes` (default 15), `expires_at`
  (epoch, optional), `last_active` (epoch, written at most once/min).
- `shiny-proxy-audit` — append-only events: `host` (PK), `ts#rand` (SK),
  `event` (allow|deny|wake|sleep|expired), `email`, `path`. TTL attribute at
  90 days.

**Auth stays on the ALB.** The proxy sits behind the same
`authenticate-cognito` action every app uses today, via one shared Cognito
app client on the catch-all rule. Cognito clients accept up to ~100 callback
URLs; each migrated/created app host adds
`https://<host>/oauth2/idpresponse` to the list (the portal later does this
via SDK — ADR-0012). The proxy reads `x-amzn-oidc-data` with the same claim
fallback order as `access.R` and the portal Lambda: `email`, `upn`,
`preferred_username`, `custom:email`. Signature not verified for the same
reason documented there: only the ALB can reach the target group. Fail
closed: missing/duff identity → 401 page.

**Routing: catch-all, lowest precedence, migrate app-by-app.** One listener
rule for `*.tools.stratevi.com` at priority **5000** (register in CLAUDE.md;
existing explicit rules at 50–900 all win). A wildcard Route 53 A-alias
`*.tools.stratevi.com` → ALB. Existing apps keep working untouched; migrating
an app = delete its listener rules + waker/sleeper + Cognito client from its
stack, add its host to the shared client's callbacks and a row to
`shiny-proxy-apps`. **Migrate `model` first — it is currently a dead link, so
it is the free guinea pig.**

**The proxy connects to app tasks directly** (ECS DescribeTasks → awsvpc ENI
private IP → `http://<ip>:3838`), not through per-app target groups. This is
what removes the ~50-target-group ceiling. Requires the app security group to
allow ingress from the proxy's security group on the container port —
a one-line addition to the app stacks at migration time (byte-identical rule
applies). Discovery result cached ~10s.

**Wake-on-request.** Request for an app whose service has 0 running tasks:
`UpdateService desiredCount=1` (idempotent), audit `wake`, and serve a
branded "starting up" page that meta-refreshes every 3s until the task IP
answers a TCP/HTTP probe, then proxies normally. Typical Shiny cold start
here is 30–60s; the page says so.

**Sleep is server-side.** Activity = any proxied HTTP request OR ≥1 open
websocket through the proxy. A background loop scales a service to 0 after
`idle_minutes` of neither, writes audit `sleep`. This retires the ADR-0006
client-side heartbeat for migrated apps — the snippet can come out of app UIs
once their host is proxied. `last_active` is persisted so a proxy restart
doesn't insta-sleep a busy app (on restart, treat boot time as activity).

**Expiry enforcement (reaper phase 1).** The same background loop marks apps
with `expires_at < now` as `expired`, scales them to 0, audits `expired`.
Requests to an expired/disabled app get the branded expired/disabled page
(no cold start to be refused — the proxy answers directly). Reminder emails
are the portal phase's job, not the proxy's.

**Pages.** Minimal branded HTML (Stratevi design system, NOT Assembled
Intelligence): starting-up, 401 not-signed-in (shouldn't occur behind ALB
auth), 403 no-access, 410 expired, 404 unknown host, 503 app-unhealthy,
and the signed-out page. Packaged with the app, no external assets.

**Sign-out** (`/__proxy/logout` on portal hosts) has to do two things or it
silently does nothing: expire **every** `AWSELBAuthSessionCookie-N` shard
(federated claims are large enough to split across several — leave one and
the ALB re-authenticates from its own session), then redirect through
Cognito's hosted-UI `/logout` to end that session too.

The landing page is the subtle part. Every listener rule authenticates
first, so a just-signed-out visitor sent anywhere normal is bounced into
Cognito — and with the session just ended, a federated user clears the
login page in one click and is back in, making sign-out look broken. So
`/__proxy/signed-out` has **its own listener rule at priority 4900 with no
authenticate action** — the only unauthenticated rule on the listener. It
is kept safe by being an exact path (not a prefix) under the `/__proxy/`
prefix that is reserved on every host, serving static HTML with no identity
in it. See the banner in `proxy/alb.tf`.

The page states plainly that the Microsoft session is still active, because
it is: ending the Cognito session does not end Entra's. Chaining Entra's
`end_session_endpoint` would, but it signs the person out of Outlook and
Teams too — wrong default for an internal tool.

## Contract details (settled during the first build — normative)

- Audit table: PK `host` (S), SK **`ts`** (S) valued `<13-digit epoch
  ms>#<8 hex>`, plus `ts_epoch` (N) and TTL attribute **`ttl`** (N). The
  Terraform tables match these names.
- Audit `allow` events are deduplicated per host+email for 10 minutes (one
  Shiny page load is dozens of asset requests); `deny`/`wake`/`sleep`/
  `expired` are never collapsed. Audit writes are best-effort and must never
  block or fail a request.
- Identity semantics: 401 only when there is no parseable identity at all.
  An authenticated principal with no resolvable email is allowed under
  `all_users` (matches the portal Lambda's behavior) and 403'd under
  `users`. Reserved modes (`team`/`organizations`/`client_magic_link`) are
  403 — never open. Fail closed on any store error.
- Expiry is enforced per-request against the clock, not the `status`
  attribute, so an expired app is unreachable during the up-to-60s before
  the reaper marks it.
- Starting page returns HTTP 200 with `Retry-After: 3` (meta-refresh
  behaves everywhere; 503 makes some browsers cache the failure).
- `/__proxy/healthz` is an unconditional 200 — a proxy that deregisters
  itself when DynamoDB blips takes every app down at once. `/__proxy/readyz`
  does a GetItem on sentinel key `__readyz__` (keeps IAM narrow — no
  DescribeTable).
- Sleeper/reaper loop interval is a 60s code constant, not config.
- IAM needs `ecs:ListTasks` (DescribeTasks takes ARNs; only ListTasks
  produces them for a service) and `dynamodb:Scan` (the sleeper enumerates
  the apps table once a minute) — both beyond the sketch above, both already
  in the Terraform stack.
- Timeouts: no response-header or server read/write timeouts that would kill
  a long-running Shiny computation or an idle-but-open websocket. Idle
  cleanup is the sleeper's job, not a socket timer's.

**Health & ops.** `/__proxy/healthz` (liveness, always 200) and
`/__proxy/readyz` (checks DynamoDB reachability) on the service port; the
ALB target group health check uses healthz. Structured JSON logs to
CloudWatch via awslogs driver. `desired_count` is a Terraform variable
(default 1 now, 2 when a client is live) — the proxy has NO waker/sleeper,
so **no lifecycle ignore blocks in its stack**; Terraform owns its desired
count, unlike the app stacks.

**Config contract.** Env: `AWS_REGION`, `ECS_CLUSTER` (from SSM),
`APPS_TABLE`, `AUDIT_TABLE`, `PORT` (8080), `LOG_LEVEL`. IAM (task role
`shiny-proxy-task`): `ecs:DescribeServices/DescribeTasks/UpdateService`
condition-scoped to the cluster, `dynamodb:*Item/Query` scoped to the two
tables, nothing else.

**Seed.** A seed script in `proxy-app/` reads the repo-root `catalog.yaml` plus each
app's host and writes `shiny-proxy-apps` items. Run manually at migration
time; the portal replaces it later.

## Costs

Proxy task 0.25 vCPU / 512 MB ≈ $9/month (×2 tasks ≈ $18 when HA matters).
DynamoDB on-demand at this traffic ≈ cents. No new NAT, no ALB change in
fixed cost.

## Explicitly out of scope (portal phase)

Creation wizard and SDK provisioning, CodeBuild image pipeline, reminder
emails, magic links, admin UI, per-app data buckets.
