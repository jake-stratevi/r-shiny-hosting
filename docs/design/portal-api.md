# Portal API contract — v1 (P1 scope)

The boundary between the React UI (`portal-ui/`) and the proxy service
(`proxy-app/`). Both sides build against THIS file; change it here first.
See portal.md for the why.

## Serving model

The proxy service answers these routes ONLY on portal hostnames (env
`PORTAL_HOSTS`, comma-separated — will be `dashboards.tools.stratevi.com` and
`proxy.tools.stratevi.com`). On portal hosts: `/api/v1/*` is JSON;
`/assets/*` and `/` (plus any non-API path, for client-side routing) serve
the built React bundle (`index.html` fallback). On app hosts nothing
changes. `/__proxy/*` remains reserved everywhere.

Auth: the ALB's authenticate-cognito session cookie, same as every app host.
Identity comes from `x-amzn-oidc-data` exactly as the proxy already does.
There is no login UI in the SPA — an unauthenticated request never reaches
the service.

CSRF: every mutating request (PATCH) must carry `X-Portal-Csrf: 1`. Same-origin
`fetch` adds it trivially; a cross-site form cannot.

Admin gate: `shiny-proxy-apps` row `host = "__config__"` with `admin_emails`
(SS). Rows whose host begins `__` are configuration, NOT apps — the
registry, sleeper, and menu must skip them. Non-admins get 403 on everything
under `/api/v1/apps`.

## Shapes

Timestamps: epoch seconds (numbers), `null` when absent. `status` (stored):
`active | disabled | expired`. `live_state` (derived from ECS + row):
`awake | starting | asleep | disabled | expired`.

### GET /api/v1/me
`{ "email": "jake@stratevi.com", "is_admin": true }`

### GET /api/v1/menu  (any authenticated user)
Apps whose entitlement includes the caller (same rules as the proxy's access
decision), never `__`-rows:
```json
{ "apps": [ { "host": "dashboard.tools.stratevi.com",
              "label": "Treatment Pathway Dashboard",
              "description": "Sankey of treatment sequences...",
              "url": "https://dashboard.tools.stratevi.com",
              "live_state": "asleep",
              "expires_at": 1793491200,
              "last_active": 1789000000 } ] }
```
`expires_at` and `last_active` are `number | null` (absent attribute →
null). They are on the MENU payload, not just the admin one, because the
card shows them: "an app I use disappears in six days" is the reader's
business, not only an administrator's. Neither is sensitive — the caller is
already entitled to open the app.

### GET /api/v1/apps  (admin)
Array of full app objects:
```json
{ "host": "...", "app_key": "dashboard", "label": "...", "description": "...",
  "ecs_service": "shiny-dashboard", "container_port": 3838,
  "status": "active", "live_state": "asleep",
  "access_mode": "users", "allowed_emails": ["..."],
  "idle_minutes": 15, "max_session_hours": 12,
  "expires_at": null, "last_active": 1789000000, "awake_since": null,
  "desired_count": 0, "running_count": 0 }
```

### GET /api/v1/apps/{host}  (admin)
One app object, 404 JSON if unknown.

### PATCH /api/v1/apps/{host}  (admin, CSRF header required)
Body: any subset of `label`, `description`, `access_mode` (`all_users|users`
only — reserved modes 400), `allowed_emails` (list, lowercased, each must
contain "@"), `idle_minutes` (1–1440), `max_session_hours` (0–168, 0 =
uncapped), `expires_at` (epoch seconds or null = never), `status` (`active`
or `disabled` only — expiry is the reaper's job). Unknown fields → 400.
Returns the updated app object. Every successful PATCH writes an audit event
`config_change` with the caller's email and the changed field names (not
values) in `path`.

### GET /api/v1/apps/{host}/audit?limit=100&cursor=...  (admin)
Newest first:
```json
{ "events": [ { "event": "allow", "email": "...", "path": "/", "ts": 1789000000 } ],
  "cursor": "opaque-or-null" }
```
`cursor` is the DynamoDB LastEvaluatedKey, base64-JSON, opaque to the client.

### Errors
Non-2xx bodies are `{ "error": "human-readable message" }` with 400/401/403/
404/409 semantics. 401 only when identity is missing entirely (shouldn't
happen behind the ALB).

## Clarifications — P1 as built (both sides conform to THESE)

- `GET /apps` returns a **bare array**; `GET /menu` returns `{"apps": [...]}`.
- `expires_at` is an instant (epoch seconds), compared strictly against the
  clock. The UI's date picker writes 23:59:59 local time of the chosen day.
- `last_active` / `awake_since` are `number | null` (absent attribute = null,
  rendered "never").
- **Reviving an expired app** is one PATCH: `{ "expires_at": <future or
  null>, "status": "active" }`. Neither half alone is enough — the clock
  check and the status attribute both gate access.
- `allowed_emails: []` is accepted by PATCH (removes the attribute). Under
  `users` mode that locks everyone out; the UI warns, `seed.py` refuses.
- Audit `limit` ≥ 1, no server ceiling (DynamoDB's 1 MB page cap applies);
  the UI pages by 50.
- Audit `event` is an **open enum**; currently: `allow`, `deny`, `wake`,
  `sleep`, `force_sleep`, `expired`, `config_change`, `app_created`,
  `build_started`, `build_succeeded`, `build_failed`, `provision_failed`,
  `signed_out`. Events also carry `outcome` where useful. Render unknown
  names neutrally. Note `signed_out` partitions under the PORTAL hostname,
  which has no app row — so it is in DynamoDB and CloudWatch but never
  appears in an app's audit viewer. Read it with a direct query.
- `GET /me` is 200 for every authenticated user, `is_admin: false` included.
- Empty PATCH body → 400. The `config_change` audit lists fields *written*
  (not a value-diff — the UI sends diffs, keeping the two equivalent).
- Field caps: label ≤ 200 chars, description ≤ 2000, allowed_emails ≤ 500.
- Wrong method → 405; store/ECS failure → 503; same `{"error": …}` body.
- `/assets/*` served immutable (1 year); `index.html` is `no-store`; a
  missing asset is a 404, never the SPA fallback.

## P2a additions — creation (see portal-p2a.md)

`GET /me` gains `can_create` (bool, from `__config__.creator_emails`;
independent of `is_admin`). Every route below requires creator permission —
admin alone is 403 — and the CSRF header on mutating calls.

### POST /api/v1/apps/validate-key
Body `{ "key": "tarpeyo-uptake" }` → `{ "ok": true, "host":
"tarpeyo-uptake.tools.stratevi.com" }` or `{ "ok": false, "reason":
"<human message>" }`. Checks shape, reserved names, `key_denylist`, and
collision with an existing row. 200 either way — this is a form affordance,
not an error.

### POST /api/v1/uploads
Body `{ "filename": "app.zip", "size": 12345678 }` → `{ "upload_key":
"uploads/<uuid>.zip", "url": "<presigned PUT>", "expires_in": 900 }`.
Rejects over-size before issuing a URL. The browser PUTs the zip directly
to S3; the API never proxies bytes. **Only Bucket and Key are signed** — the
client must NOT send a `Content-Type` header, or the signature won't match.

### Bundle inspection is CLIENT-SIDE — there is no inspect endpoint

portal-p2a.md requires the wizard to show the detected entrypoint and
resolved package list back for confirmation, and never to guess silently.
The obvious shape for that is a server route that reads the uploaded zip —
**deliberately rejected.** The proxy task is in the request path for every
app on the platform; making it download and unzip a 100 MB bundle would put
other people's page loads behind that work, on 0.25 vCPU. A router should
not become a worker.

The browser already holds the bytes, so it inspects them locally *before*
uploading: read the zip index, find the entrypoint (`app.R`, or
`ui.R`+`server.R`, at root or in exactly one wrapper directory), then
resolve packages from `renv.lock`, else `packages.txt`, else by scanning
`library()`/`require()` calls. The user confirms the resulting list, and it
is sent as `packages` on `POST /apps`.

This is a UX affordance, not a security control, and nothing downstream
trusts it: CodeBuild's `validate.py` re-checks the bundle server-side
(zip-slip, symlinks, entry count, extracted size, entrypoint) and is the
only authority. A client that lies gets a failed build, not a bad app.

### POST /api/v1/apps
Body: `key`, `label`, `description`, `cpu`/`memory` (one of the two allowed
sizes), `upload_key`, `access_mode`, `allowed_emails`, `idle_minutes`,
`max_session_hours`, `expires_at` (epoch or null — must be *explicitly*
present, no default), `packages` (the confirmed list). Validates the bundle,
reserves the row conditionally, provisions, starts the build. **202** with
the app object (`status: building`). Conflict on the key → **409**.

### GET /api/v1/apps/{host}/build
`{ "state": "building|succeeded|failed", "phase": "<CodeBuild phase>",
"started_at": <epoch>, "elapsed_s": 123, "log_url": "<console deep link>",
"log_tail": ["…"] }`. Poll while `live_state` is `building`.

New `live_state` values: `building`, `build_failed`. Renderers must already
tolerate unknown values (P1 rule), so this is additive.

## Out of P2a scope (do not stub half-built)
Releases/version history, rollback, delete/purge, reminders. P2b.
