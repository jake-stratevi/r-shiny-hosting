# proxy-app — the Stratevi authorizing proxy and portal

One always-on Python service that fronts every `*.tools.stratevi.com` app
hostname: it decides who may use each app, wakes sleeping ECS services, proxies
to the task (HTTP and websockets), and scales idle apps back to zero.

It is also the **portal**, on the hostnames named in `PORTAL_HOSTS`: the menu
every authenticated user sees, and the admin control plane. Same task, same
table, same deploy — ADR-0014's $9/month buys both roles.

Design: [`../docs/design/proxy.md`](../docs/design/proxy.md) — its "Contract
details (settled during the first build — normative)" section is what this
implementation is measured against — plus
[`../docs/design/portal.md`](../docs/design/portal.md) and the API contract
[`../docs/design/portal-api.md`](../docs/design/portal-api.md), which the
React UI in `portal-ui/` is built against too: **change that file before
changing either side.** Why any of it exists:
[ADR-0014](../docs/adr/0014-standalone-control-plane.md) and
[ADR-0008](../docs/adr/0008-authorization-strategy.md). This directory is
source only; the Terraform stack that runs it lives in `../proxy/`.

Python 3.12, aiohttp for both the server and the reverse-proxy client, boto3
for DynamoDB and ECS. Three runtime dependencies, pinned.

## Layout

```
proxy_app/__main__.py   Wiring: env -> clients -> caches -> server.
proxy_app/config.py     The environment contract and JSON logging.
proxy_app/identity.py   x-amzn-oidc-* parsing. Mirrors access.R and the portal Lambda.
proxy_app/registry.py   App rows, host normalization, DynamoDB store, 10s read cache.
proxy_app/access.py     The entitlement decision. Pure, fails closed, tested hard.
proxy_app/ecsctl.py     Task-IP discovery, wake, sleep, with caches.
proxy_app/activity.py   Requests + open websockets; persists last_active.
proxy_app/audit.py      Best-effort, never-blocking audit recorder.
proxy_app/pages.py      The six branded pages (page.html is package data).
proxy_app/sleeper.py    The 60s sleeper/reaper loop (+ the P2a build sweep).
proxy_app/server.py     The request path and the reserved /__proxy/ endpoints.
proxy_app/portal.py     The portal: /api/v1/*, the admin gate, the React bundle.
proxy_app/creation.py   P2a validation: key rules, upload cap, wizard body. Pure.
proxy_app/provision.py  P2a provisioning: ECR, IAM, CodeBuild, ECS, Cognito.
buildspec/              The Dockerfile template and buildspec CodeBuild runs.
seed.py                 Migration-time tool: catalog.yaml -> shiny-proxy-apps rows.
tests/                  pytest. No AWS, no network, no credentials.
```

Only `registry`, `ecsctl`, `audit` and `provision` contain boto3 calls, and in
each the AWS class sits behind a small async protocol the rest of the code
depends on instead — which is why the tests need no fake AWS.

## Build and test

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

The suite covers the pure logic: claim parsing including the federated
no-email cases, the access decision matrix (modes × status × expiry ×
identity), host normalization, the registry cache, task-discovery caching and
wake idempotence, the sleeper/reaper pass, audit dedupe and item shape, header
plumbing, the reserved endpoints, the branded pages, and seed parsing driven
against a synthetic catalog written to a temp directory — the repo-root
`catalog.yaml` those tests used to read went away with ADR-0013's Lambda
portal, so the fixture mirrors its shape instead. For the portal: admin gating including every fail-closed
path, menu entitlement filtering, the whole PATCH validation matrix,
`live_state` derivation, the audit cursor round trip, CSRF rejection,
`__`-row skipping, and portal-host routing versus app-host proxying. For
P2a: the whole key/upload/create validation matrix, creator-versus-admin
gating on every route, the slug-collision 409, the boundary invariant from
all four directions, the Cognito read-modify-write proving every
pre-existing field survives (including a fake that "loses" a URL on write),
the provisioning failure paths, and the stuck-in-building reaper.

Container:

```powershell
docker buildx build --platform linux/amd64 -t shiny-proxy:dev .
```

## Running locally against fakes

DynamoDB Local is enough; boto3 finds it through `AWS_ENDPOINT_URL_DYNAMODB`.
No ECS is needed — without one, every allowed request lands on the starting
page, which is the correct answer for a cluster that does not exist.

```powershell
docker run -d -p 8000:8000 amazon/dynamodb-local

$env:AWS_ENDPOINT_URL_DYNAMODB = "http://localhost:8000"
$env:AWS_REGION            = "us-east-1"
$env:AWS_ACCESS_KEY_ID     = "local"
$env:AWS_SECRET_ACCESS_KEY = "local"

aws dynamodb create-table --table-name shiny-proxy-apps `
  --attribute-definitions AttributeName=host,AttributeType=S `
  --key-schema AttributeName=host,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST --endpoint-url http://localhost:8000

aws dynamodb create-table --table-name shiny-proxy-audit `
  --attribute-definitions AttributeName=host,AttributeType=S AttributeName=ts,AttributeType=S `
  --key-schema AttributeName=host,KeyType=HASH AttributeName=ts,KeyType=RANGE `
  --billing-mode PAY_PER_REQUEST --endpoint-url http://localhost:8000

python seed.py --table shiny-proxy-apps --dry-run
python seed.py --table shiny-proxy-apps

$env:ECS_CLUSTER = "shiny-cluster"
$env:APPS_TABLE  = "shiny-proxy-apps"
$env:AUDIT_TABLE = "shiny-proxy-audit"
$env:LOG_LEVEL   = "debug"
python -m proxy_app
```

Then drive it with a fake ALB identity. `x-amzn-oidc-data` is a JWT whose
payload is read without verifying the signature (see below), so a hand-made
one works:

```powershell
# payload is base64url of {"email":"jake@stratevi.com"}
$payload = "eyJlbWFpbCI6Impha2VAc3RyYXRldmkuY29tIn0"
curl.exe -H "Host: model.tools.stratevi.com" `
         -H "x-amzn-oidc-data: h.$payload.s" `
         http://localhost:8080/
```

`/__proxy/healthz` and `/__proxy/readyz` answer regardless of Host.

To exercise the portal locally, name a host and write yourself a `__config__`
row before starting the service:

```powershell
aws dynamodb put-item --table-name shiny-proxy-apps --endpoint-url http://localhost:8000 `
  --item '{\"host\":{\"S\":\"__config__\"},\"admin_emails\":{\"SS\":[\"jake@stratevi.com\"]}}'

$env:PORTAL_HOSTS = "dashboards.tools.stratevi.com"
python -m proxy_app

curl.exe -H "Host: dashboards.tools.stratevi.com" `
         -H "x-amzn-oidc-data: h.$payload.s" `
         http://localhost:8080/api/v1/menu
```

With no `PORTAL_DIST` build present, `/` answers with the "not built yet"
page and the API works regardless — that is the deployable-before-the-UI
case, not a broken one.

To point it at a real Shiny container instead of ECS, run one locally and put
its address in the row's `ecs_service`… you cannot — discovery goes through
ECS. Use `docker run` plus DynamoDB Local to exercise the decision path, and
the `tests/` fakes to exercise the rest.

## Environment contract

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `AWS_REGION` | no | SDK resolution | Region for DynamoDB and ECS. Terraform sets it. |
| `ECS_CLUSTER` | **yes** | — | Cluster holding every app service. From SSM. |
| `APPS_TABLE` | **yes** | — | `shiny-proxy-apps`. |
| `AUDIT_TABLE` | **yes** | — | `shiny-proxy-audit`. |
| `PORT` | no | `8080` | Listen port; the ALB target group points here. |
| `LOG_LEVEL` | no | `info` | `debug`, `info`, `warn`, `error`. |
| `PORTAL_HOSTS` | no | *(empty)* | Comma-separated hostnames the **portal** answers on, e.g. `dashboards.tools.stratevi.com,proxy.tools.stratevi.com`. Empty means no portal and this service behaves exactly as it did before. Case, ports and trailing dots are normalized. |
| `PORTAL_DIST` | no | `./portal-dist` | Directory holding the built React bundle. A missing directory is **not** an error — the API answers and `/` serves a "not built yet" page. |

### P2a self-service creation

One more block, **all of it or none of it**. With none of these set, creation
is simply off: `/me` reports `can_create: false` and the four P2a routes
answer `503`. With *some* of them set, creation is **also** off and the
missing names are logged at ERROR on startup — a half-configured pipeline
that fails at its third API call has already reserved a hostname and created
an ECR repository, which is far more expensive to clean up than a feature
that never started.

This is the one part of the contract that degrades instead of exiting 2, and
the reason is blast radius: this task is in the request path for **every**
app, so refusing to boot over a misconfigured wizard would take the
dashboard, the model and the portal down to protect a feature nobody is
currently using.

| Variable | Meaning |
|---|---|
| `UPLOADS_BUCKET` | `shiny-portal-uploads-<acct>`; the presigned PUT target. |
| `CODEBUILD_PROJECT` | `shiny-app-build`; the one shared build project. |
| `APP_ROLE_BOUNDARY_ARN` | The Terraform-owned permissions boundary **every** created role carries. See below — there is no mode in which a role is created without it. |
| `APP_DATA_BUCKET` | `shiny-app-data-<acct>`; each app's role may read only its own `<app-key>/` prefix. |
| `APP_DOMAIN` | `tools.stratevi.com`; the host is `<key>.<APP_DOMAIN>`. |
| `APP_SUBNET_IDS` | Comma-separated subnets for the created service. |
| `APP_SECURITY_GROUP_ID` | The shared apps SG the proxy is already allowed into (`proxy/ecs.tf`'s `apps_from_proxy`). |
| `APP_EXECUTION_ROLE_ARN` | The shared platform execution role — image pull and log writes, same one every app stack uses. |
| `APP_LOG_GROUP` | Log group created apps write to; the stream prefix is the app key. |
| `COGNITO_USER_POOL_ID` | The Hub pool. |
| `COGNITO_CLIENT_ID` | The **one** shared app client whose callback list grows by one URL per created app. |

Anything required and missing is exit code 2 at startup, not a degraded mode.
There are no other environment variables: nothing here reads a config file, a
Parameter Store path, or a secret.

A portal hostname needs **no row** in `shiny-proxy-apps`: `PORTAL_HOSTS` is
checked before the table, so the portal cannot be shadowed by a row and does
not need one to exist.

Logs are one JSON object per line on stdout, for the awslogs driver.
`LOG_LEVEL=debug` deliberately does **not** turn on botocore's several hundred
lines per DynamoDB call.

## The portal

Served only on `PORTAL_HOSTS`. `/__proxy/*` stays reserved everywhere,
including there — the ALB health check must not depend on the portal.

The contract is [`../docs/design/portal-api.md`](../docs/design/portal-api.md)
and it is normative; this is the summary.

| Method + path | Who | What |
|---|---|---|
| `GET /api/v1/me` | any signed-in user | `{ "email", "is_admin", "can_create" }` |
| `GET /api/v1/menu` | any signed-in user | the apps that caller is entitled to, with `live_state` |
| `GET /api/v1/apps` | admin | every app, full objects |
| `GET /api/v1/apps/{host}` | admin | one app, 404 if unknown |
| `PATCH /api/v1/apps/{host}` | admin + CSRF header | edit `label`, `description`, `access_mode`, `allowed_emails`, `idle_minutes`, `max_session_hours`, `expires_at`, `status` |
| `GET /api/v1/apps/{host}/audit?limit=&cursor=` | admin | the trail, newest first, `cursor` is an opaque base64-JSON `LastEvaluatedKey` |
| `POST /api/v1/apps/validate-key` | **creator** | `{ "key" }` → `{ "ok", "host" }` or `{ "ok": false, "reason" }`. Always 200 — a form affordance, not an error. No CSRF header (it mutates nothing). |
| `POST /api/v1/uploads` | **creator** + CSRF header | `{ "filename", "size" }` → `{ "upload_key", "url", "expires_in": 900 }`. Over-size is refused **before** a URL exists. |
| `POST /api/v1/apps` | **creator** + CSRF header | the wizard's answers → **202** with the app object at `status: building`. **409** if the slug was taken while submitting. |
| `GET /api/v1/apps/{host}/build` | **creator** | `{ "state", "phase", "started_at", "elapsed_s", "log_url", "log_tail" }` (plus `reason` when failed). Poll while `live_state` is `building`. |
| anything else | any signed-in user | the React bundle, with `index.html` as the SPA fallback |

Non-2xx bodies are `{"error": "…"}`. A store or ECS failure the portal cannot
paper over is a `503` with the same body shape.

**The menu reuses `access.decide`.** It cannot offer a tile the proxy would
then refuse — which is exactly the drift ADR-0013's `catalog.yaml` suffered
from and this replaces.

**`live_state` is derived, never stored**: `expired` (status *or* the clock)
→ `disabled` → then ECS: `desired == 0` is `asleep`, `desired > 0` with
`running == 0` is `starting`, otherwise `awake`. A describe failure degrades
the badge to `asleep`; it never fails the page.

**PATCH needs `X-Portal-Csrf`** (any non-empty value; `1` by convention).
Presence is the whole check: a same-origin `fetch` sets a custom header
trivially and a cross-site HTML form cannot set one at all. Auth is still the
ALB's Cognito session. `status` accepts only `active` and `disabled` — expiry
is the reaper's to declare — and reserved access modes are refused at the
door rather than stored and 403'd later. Every successful PATCH writes a
`config_change` audit event carrying the caller's email and the changed field
**names** (never their values, so the trail is not somewhere an allowlist can
be read out of), and is never deduplicated.

**Admins come from a `__config__` row**, and `__`-prefixed rows are
configuration, not apps — skipped by the registry, the sleeper and the menu
alike. The list is cached ~30s, and fails **closed**: no row, no
`admin_emails`, an unreadable table, or a principal with no resolvable email
all mean "not an admin". A *failed* read is cached for only ~5s, so a
DynamoDB blip does not lock the control plane for half a minute.

```
host            S    "__config__"
admin_emails    SS   ["jake@stratevi.com", …]   lowercased on read
creator_emails  SS   who may CREATE apps — not implied by admin
key_denylist    SS   substrings banned from a public hostname
```

Managed as a Terraform `aws_dynamodb_table_item` (ADR-0014), so granting
yourself admin is a code review, not a console edit.

**Creation is its own permission** (portal-p2a.md's decision). `admin_emails`
governs editing apps that exist; `creator_emails` governs putting a new
hostname on the public internet and running an uploaded bundle in it. Being
an admin grants **none** of the second: an admin who is not a creator gets
403 from every P2a route, and `/me` reports `can_create: false` so the UI
hides the "+ New app" affordance rather than dangling a 403. Both lists are
cached ~30s and fail closed in every direction.

## Self-service creation (P2a)

The pipeline is `wizard → zip to S3 → CodeBuild → ECR → SDK provisioning →
live`, and none of it is Terraform: per ADR-0012, Terraform owns the
pipeline's own infrastructure and the portal creates the per-app resources
through the SDK. A created app is **born proxied**, which is why this is
possible at all — no listener rule, no target group, no waker, no per-app
Cognito client.

**Provisioning order, and it is not arbitrary:**

1. **Reserve the row** — a conditional `PutItem` on `attribute_not_exists(host)`
   at `status: building`. This is the mutex: two wizards submitting the same
   key both pass `validate-key` (a form affordance, not a lock) and exactly
   one wins here. It is step one so the loser has provisioned **nothing**.
2. ECR repository `shiny-<key>` + a keep-5 lifecycle policy.
3. IAM role `shiny-app-<key>-task`, always inside the boundary.
4. `StartBuild` with `APP_KEY`, `ZIP_KEY`, `RELEASE_TAG`, `ECR_REPO_URI` and
   `PACKAGES` overrides; the row records the build id. Those five plus
   `UPLOADS_BUCKET` — static on the project, one bucket for every app — are
   exactly `buildspec/render.py`'s `REQUIRED` tuple, and a missing one fails
   the build in `pre_build` naming the variable. `ECR_REPO_URI` is the
   repository step 2 just made, which is why step 2 comes first.
5. On success (detected by **polling in the sleeper loop**, not a webhook):
   register the task definition, create the ECS service at **desired 0** —
   nobody has opened it yet, and the proxy wakes it like any other app — add
   the host's callback and logout URL to the shared Cognito client, then flip
   the row to `active`.

**On any failure after step 1** the row goes to `build_failed` with the
reason recorded, and whatever exists is **left in place for inspection**
rather than thrashing. A retry reuses it; deleting it is P2b.

**Nothing stays "building" forever.** The sleeper loop hands every `building`
row to the build watcher once a minute, and a build that has not finished
45 minutes after it started — or a row that never got a build id at all, or
one whose build CodeBuild can no longer describe — is reaped to
`build_failed`. Audit events: `app_created`, `build_started`,
`build_succeeded`, `build_failed`, `provision_failed`, each carrying the
creator's email, none of them ever deduplicated.

**Hostnames are policed** in three layers, in order: shape (3–30 chars,
lowercase `a-z0-9-`, no leading/trailing/double hyphen), a reserved list
(`www`, `api`, `auth`, `admin`, `proxy`, `shinyplatform`, `dashboards`,
`portal`, `mail`, plus every existing row's key **and** host label), and
substring matching against `__config__.key_denylist`. A denylist rejection
never says which term matched — the list is who Stratevi works with and on
what, and a wizard that plays hot-and-cold with it is a disclosure oracle.
An unreadable denylist reports "temporarily unavailable" rather than
"nothing matched": a name that reaches DNS cannot be taken back.

**`expires_at` must be explicitly present on create.** `null` means never
and is accepted; *absent* is a 400. An app that quietly lives forever
because a field was omitted is how a client demo becomes permanent
infrastructure.

**The API never proxies bytes.** `POST /uploads` checks the declared size
against the 100 MB cap *before* issuing anything, then returns a 15-minute
presigned PUT for `uploads/<uuid>.zip`. Only Bucket and Key are signed —
signing `ContentLength` would let S3 enforce the size, at the cost of an
opaque 403-as-CORS-error whenever the browser's byte count differs. The
build validates the bundle for real. On create, `upload_key` is re-checked
against the shape this service issues, so a caller cannot point the build at
some other object in the bucket.

### The permissions boundary — the one genuinely sensitive part

A service that can call `iam:CreateRole` is a privilege-escalation engine
unless it is fenced. The fence is the Terraform-owned `shiny-app-boundary`
policy, and it only works if **every** role carries it. Four independent
things make a role without it impossible, none of them a convention a future
edit can quietly drop:

1. `APP_ROLE_BOUNDARY_ARN` is in the **required** creation env, so with no
   boundary there is no creation at all.
2. `provision.Boto3TaskRoles.__init__` **raises** on a blank ARN — the object
   that creates roles cannot exist without one.
3. `ensure(app_key)` takes **no boundary parameter**. There is nothing for a
   caller to pass wrongly, pass as `None`, or forget.
4. After creating, the role is **read back** and the attached boundary
   compared; a mismatch deletes the role and raises. Even an IAM that
   accepted the call and ignored the parameter cannot leave an unbounded
   `shiny-app-*` role in the account. An *existing* role is reused only if it
   passes the same check.

### The Cognito call — read this before touching it

`UpdateUserPoolClient` is a **replace, not a patch**. Send it `CallbackURLs`
alone and Cognito resets `AllowedOAuthFlows`, `AllowedOAuthScopes`,
`SupportedIdentityProviders`, `ExplicitAuthFlows` and every token validity to
their defaults — and because this is the one shared client the ALB
authenticates every app against, that means the whole platform stops signing
anyone in. It fails **silently**: the API returns 200.

So the call is Describe → carry every field forward except the three
`Describe`-only ones (`ClientSecret`, `LastModifiedDate`, `CreationDate`) →
Update → **Describe again and verify no previously-present callback URL
vanished**. The merge is a pure function (`provision.merged_client_config`)
tested against a full, realistic client config for exactly this reason. It is
also the **last** provisioning step, because it is the only one that mutates
state shared with every other app.

**The bundle.** `PORTAL_DIST` (default `/app/portal-dist`, created empty by
the Dockerfile) holds the Vite output. `/assets/*` is served with a one-year
immutable cache — the filename hash is the cache key — and everything else
falls back to `index.html`, served `no-store` so a deploy is picked up. A
missing asset is a 404, not the SPA document: an SPA fallback there turns a
broken build into a blank page. With no bundle at all, `/` serves a plain
"not built yet" page and the API still works, so the backend deploys before
`portal-ui/` exists.

The proxy's own operational pages (starting-up, 401, 403, expired, unknown
host) stay embedded plain HTML on every host, portal included — portal.md's
hard rule. They render during cold starts and failures and must never depend
on a JS bundle loading.

## IAM contract (task role `shiny-proxy-task`)

- `ecs:DescribeServices`, `ecs:DescribeTasks`, `ecs:ListTasks`,
  `ecs:UpdateService` — condition-scoped to `ECS_CLUSTER`.
- `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`,
  `dynamodb:Scan` — on the two tables only.
- `dynamodb:Query` on the **audit** table — added by the portal's audit
  viewer. It is the only thing in the service that reads the trail back.

Two of those are not in the design spec's original sketch and are load-bearing:
`ecs:ListTasks` (DescribeTasks takes ARNs, and only ListTasks produces them for
a service) and `dynamodb:Scan` (the sleeper enumerates the apps table once a
minute; there is no partition key to Query on).

`dynamodb:DescribeTable` is deliberately **not** required: `/__proxy/readyz`
probes with a GetItem against the sentinel key `__readyz__` instead, so
readiness does not widen the policy.

**P2a creation adds**, each narrowly scoped (and only needed once the
creation env is set):

- `s3:PutObject` on the uploads bucket — a presigned URL carries the
  *signer's* permissions, so the browser's PUT fails without this even
  though the service makes no S3 call.
- `codebuild:StartBuild`, `codebuild:BatchGetBuilds` on the one project.
- `logs:GetLogEvents` on the CodeBuild log group — the build screen's tail.
- `ecr:CreateRepository`, `ecr:DescribeRepositories`, `ecr:PutLifecyclePolicy`,
  `ecr:TagResource` on `shiny-*`.
- `ecs:RegisterTaskDefinition` (no resource scoping is possible),
  `ecs:CreateService` on the cluster.
- `iam:CreateRole`, `iam:PutRolePolicy`, `iam:TagRole`, `iam:GetRole`,
  `iam:DeleteRole` on `role/shiny-app-*` **only** with
  `iam:PermissionsBoundary` equal to the boundary policy. `GetRole` and
  `DeleteRole` are what invariant 4 above is made of — without them the
  read-back verification cannot run.
- `iam:PassRole` on `role/shiny-app-*` and on the app execution role, to
  `ecs-tasks.amazonaws.com`.
- `cognito-idp:DescribeUserPoolClient`, `cognito-idp:UpdateUserPoolClient`
  on the one shared client. Describe is not optional — see above.

`seed.py` needs `dynamodb:PutItem` on the apps table, run from a human's
credentials, not the task role.

## Table shapes

`shiny-proxy-apps` — PK `host` (S):

| Attribute | Type | Notes |
|---|---|---|
| `host` | S | normalized: lowercase, no port, no trailing dot. A **`__`-prefixed** key is configuration, not an app (see `__config__` above) and is skipped everywhere |
| `app_key` | S | matches the Terraform `app_key` |
| `label` | S | optional; what the portal's tile is called. Falls back to `app_key` |
| `description` | S | optional; one line under the tile |
| `ecs_service` | S | service name inside `ECS_CLUSTER` |
| `container_port` | N | default 3838 |
| `status` | S | `active` / `disabled` / `expired` |
| `access_mode` | S | `all_users` / `users`; `team`, `organizations`, `client_magic_link` are reserved and **refuse** |
| `allowed_emails` | SS | lowercased; a list (L) of strings is also read |
| `idle_minutes` | N | default 15 |
| `expires_at` | N | epoch seconds, optional; 0/absent means never |
| `last_active` | N | epoch seconds, written at most once a minute |
| `max_session_hours` | N | optional; 0/absent means uncapped. Hard ceiling on continuous awake time — see "The force-sleep cap" below |
| `awake_since` | N | epoch seconds, optional; 0/absent means not currently tracked awake. Set when the proxy wakes the app or when the sleeper first observes it running; cleared once the service is observed at 0 |

P2a adds, on portal-created rows only (a row seeded before P2a has none of
them, which is exactly what a hand-provisioned app should look like):

| Attribute | Type | Notes |
|---|---|---|
| `status` | S | also `building` / `build_failed`. `access.decide` refuses both — an unrecognised status is a 403, which is the right answer for a host whose container does not exist |
| `cpu`, `memory` | N | the chosen Fargate size; kept on the row because the task definition is not registered until the build succeeds |
| `packages` | SS | the confirmed R package list, so a rebuild is reproducible |
| `upload_key` | S | `uploads/<uuid>.zip`, the build's input |
| `release_tag` | S | `r1`; P2b's version history hangs off this |
| `image` | S | full ECR image URI including the tag |
| `build_id` | S | CodeBuild build id, polled by the sleeper loop |
| `build_started_at` | N | epoch seconds; what the 45-minute reaper measures |
| `build_error` | S | why the last build or provisioning step failed |
| `created_by`, `created_at` | S, N | who asked for it and when |

Only `status`, `build_id`, `build_started_at`, `build_error` and `image` are
ever rewritten. What a build was *made from* — key, size, packages, upload —
is written once by the conditional put and is not patchable by anything,
including the admin API.

`shiny-proxy-audit` — PK `host` (S), SK `ts` (S), TTL attribute `ttl`:

| Attribute | Type | Notes |
|---|---|---|
| `ts` | S | `<13-digit epoch ms>#<8 hex>` — sortable, collision-proof across tasks |
| `event` | S | `allow` / `deny` / `wake` / `sleep` / `expired` / `force_sleep` / `config_change` / `app_created` / `build_started` / `build_succeeded` / `build_failed` / `provision_failed` |
| `email`, `path`, `outcome` | S | present when known |
| `ts_epoch` | N | seconds, for humans reading the console |
| `ttl` | N | 90 days after the event |

## Behaviour worth knowing before changing this

**The JWT signature is not verified.** Same reasoning as `access.R` and the
portal Lambda, and the comment is repeated at the top of `proxy_app/identity.py`:
only the ALB can reach the proxy's target group, so a forged `x-amzn-oidc-data`
has no path in. Add a second ingress to the proxy's security group and that
stops being true — verify against
`https://public-keys.auth.elb.<region>.amazonaws.com/<kid>` before you do.

**401 is only "no parseable identity at all."** A signed-in principal with no
resolvable email is *allowed* under `all_users` (matching the portal Lambda)
and 403'd under `users`. Federated Entra users routinely arrive with only a
synthetic Cognito username.

**Expiry is enforced per request against the clock**, not against the `status`
attribute, so an expired app is unreachable during the up-to-60s before the
reaper flips it.

**Everything fails closed.** An unreadable registry row is a 503, never an
open door; an unknown `access_mode` or `status` is a 403; a reserved mode is a
403.

**The starting page is HTTP 200 with `Retry-After: 3` and a 3s meta-refresh**,
not a 503 — some browsers cache a 503 for the retry window and never come back.
It says the cold start takes 30–60 seconds, because it does.

**`/__proxy/healthz` is unconditionally 200.** A proxy that deregisters itself
when DynamoDB blips takes every app on the platform down at once.
`/__proxy/readyz` does a GetItem on the sentinel key. The whole `/__proxy/`
prefix is reserved on **every** host and never forwarded to an app.

**No timeouts that can kill work.** The upstream client is
`ClientTimeout(total=None, sock_connect=5, sock_read=None)`; only the TCP
connect is bounded. A Shiny `downloadHandler` can compute for minutes before
writing a header, and a websocket is supposed to live for hours. The aiohttp
server sets no read/write deadline either — `keepalive_timeout` bounds only an
*idle* connection between requests. Idle cleanup is the sleeper's job, not a
socket timer's. `max_msg_size=0` on both websocket halves for the same reason:
aiohttp's 4 MB default would sever a session that ships a large plot.

**Nothing is buffered.** Request and response bodies stream chunk by chunk, and
`auto_decompress=False` relays the app's encoding byte for byte.

**Activity is requests plus open websockets.** That is what retires the
ADR-0006 heartbeat for a migrated app: the proxy sees the socket, so the
snippet can come out of that app's UI. Each proxy task tracks its own sockets
and they share `last_active` through the table; on restart, boot time counts as
activity so a deploy does not sleep a busy app.

**Allow events are deduplicated for 10 minutes** per host+email. One Shiny page
load is dozens of asset requests. Denials, wakes, sleeps, expiries and
force-sleeps are never collapsed, and audit writes never block or fail a
request.

**The sleeper/reaper interval is a 60s code constant**, not config.

**The force-sleep cap (`max_session_hours`, C1).** The sleeper treats an open
websocket as activity, so a browser tab left open keeps an expensive app — the
model at $0.233/hr — awake indefinitely. `max_session_hours` is a hard
ceiling, set per app (absent/0 = uncapped): once a service has been
continuously awake longer than the cap, the sleeper scales it to zero even
with open sockets or recent requests, and audits `force_sleep` (never
deduplicated, distinct from `sleep`). This check runs *before* the idle check
and ignores activity entirely — it is not a longer idle timeout, it is a
ceiling. Users lose their session; that is the intended trade, and the
starting page is one refresh away.

Enforcement is measured from `awake_since`, not from `last_active`. The proxy
sets it (best-effort, off the request path, same pattern as `last_active`)
the moment it actually wakes an app — not on every request during the cold
start, only the one that flips `desiredCount` 0→1. The sleeper loop fills in
the two cases the wake path cannot see by itself:

- a service it observes running with no `awake_since` on the row (woken by
  something other than this proxy, or a proxy that restarted mid-session) —
  it sets `awake_since` to *now* rather than guessing at history it never saw;
- a service observed with `desiredCount` at 0 — it clears `awake_since`.

Both directions are deliberately conservative: a freshly-set `awake_since`
can never make the cap trip early on unknown history, and a stale one is
never left around once the service is actually asleep.

## Seeding

```powershell
python seed.py --table shiny-proxy-apps --dry-run          # always first
python seed.py --table shiny-proxy-apps --only model       # migrate model first
```

`--table` has no default: there is exactly one production table and one obvious
name for it, which is precisely why typing it should be a deliberate act. The
tool derives each row's `host` from the `url` its `catalog.yaml` tile already
links to, defaults `ecs_service` to `<prefix>-<key>` (prefix `shiny`), and
**refuses** an entry whose `access_mode` is reserved or unknown, or one in
`users` mode with an empty `allowed_emails` — both would produce a row the
proxy refuses every request to.

`max_session_hours` is an optional per-app `catalog.yaml` key (the force-sleep
cap, see above); omitted entries seed as uncapped, and `--dry-run` prints the
attribute only for an entry that sets it.

Each entry's `label` and `description` are carried onto the row: ADR-0014
retires `catalog.yaml`, and the portal's menu reads them from the table. A
migration that dropped them would leave every tile nameless the day the
ADR-0013 Lambda portal is switched off.

Keep `catalog.yaml` in sync with each app's `terraform.tfvars` `allowed_emails`
until the portal phase collapses the two (ADR-0013).
