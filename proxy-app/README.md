# proxy-app — the Stratevi authorizing proxy

One always-on Python service that fronts every `*.tools.stratevi.com` app
hostname: it decides who may use each app, wakes sleeping ECS services, proxies
to the task (HTTP and websockets), and scales idle apps back to zero.

Design: [`../docs/design/proxy.md`](../docs/design/proxy.md) — its "Contract
details (settled during the first build — normative)" section is what this
implementation is measured against. Why it exists:
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
proxy_app/sleeper.py    The 60s sleeper/reaper loop.
proxy_app/server.py     The request path and the reserved /__proxy/ endpoints.
seed.py                 Migration-time tool: catalog.yaml -> shiny-proxy-apps rows.
tests/                  pytest. No AWS, no network, no credentials.
```

Only `registry`, `ecsctl` and `audit` contain boto3 calls, and in each the AWS
class sits behind a small async protocol the rest of the code depends on
instead — which is why the tests need no fake AWS.

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
against the **real** repo-root `catalog.yaml` rather than a fixture that can
drift away from it.

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

Anything required and missing is exit code 2 at startup, not a degraded mode.
There are no other environment variables: nothing here reads a config file, a
Parameter Store path, or a secret.

Logs are one JSON object per line on stdout, for the awslogs driver.
`LOG_LEVEL=debug` deliberately does **not** turn on botocore's several hundred
lines per DynamoDB call.

## IAM contract (task role `shiny-proxy-task`)

- `ecs:DescribeServices`, `ecs:DescribeTasks`, `ecs:ListTasks`,
  `ecs:UpdateService` — condition-scoped to `ECS_CLUSTER`.
- `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`,
  `dynamodb:Scan` — on the two tables only.

Two of those are not in the design spec's original sketch and are load-bearing:
`ecs:ListTasks` (DescribeTasks takes ARNs, and only ListTasks produces them for
a service) and `dynamodb:Scan` (the sleeper enumerates the apps table once a
minute; there is no partition key to Query on).

`dynamodb:DescribeTable` is deliberately **not** required: `/__proxy/readyz`
probes with a GetItem against the sentinel key `__readyz__` instead, so
readiness does not widen the policy. `dynamodb:Query` is not used by the proxy
either — it is only useful for reading the audit trail back, from a human's
credentials.

`seed.py` needs `dynamodb:PutItem` on the apps table, run from a human's
credentials, not the task role.

## Table shapes

`shiny-proxy-apps` — PK `host` (S):

| Attribute | Type | Notes |
|---|---|---|
| `host` | S | normalized: lowercase, no port, no trailing dot |
| `app_key` | S | matches the Terraform `app_key` |
| `ecs_service` | S | service name inside `ECS_CLUSTER` |
| `container_port` | N | default 3838 |
| `status` | S | `active` / `disabled` / `expired` |
| `access_mode` | S | `all_users` / `users`; `team`, `organizations`, `client_magic_link` are reserved and **refuse** |
| `allowed_emails` | SS | lowercased; a list (L) of strings is also read |
| `idle_minutes` | N | default 15 |
| `expires_at` | N | epoch seconds, optional; 0/absent means never |
| `last_active` | N | epoch seconds, written at most once a minute |

`shiny-proxy-audit` — PK `host` (S), SK `ts` (S), TTL attribute `ttl`:

| Attribute | Type | Notes |
|---|---|---|
| `ts` | S | `<13-digit epoch ms>#<8 hex>` — sortable, collision-proof across tasks |
| `event` | S | `allow` / `deny` / `wake` / `sleep` / `expired` |
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
load is dozens of asset requests. Denials, wakes, sleeps and expiries are never
collapsed, and audit writes never block or fail a request.

**The sleeper/reaper interval is a 60s code constant**, not config.

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

Keep `catalog.yaml` in sync with each app's `terraform.tfvars` `allowed_emails`
until the portal phase collapses the two (ADR-0013).
