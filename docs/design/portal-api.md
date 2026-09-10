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
              "live_state": "asleep" } ] }
```

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
  `sleep`, `force_sleep`, `expired`, `config_change`. Events also carry
  `outcome` where useful. Render unknown names neutrally.
- `GET /me` is 200 for every authenticated user, `is_admin: false` included.
- Empty PATCH body → 400. The `config_change` audit lists fields *written*
  (not a value-diff — the UI sends diffs, keeping the two equivalent).
- Field caps: label ≤ 200 chars, description ≤ 2000, allowed_emails ≤ 500.
- Wrong method → 405; store/ECS failure → 503; same `{"error": …}` body.
- `/assets/*` served immutable (1 year); `index.html` is `no-store`; a
  missing asset is a 404, never the SPA fallback.

## Out of P1 scope (do not stub half-built)
POST /apps (creation), releases, uploads, reminders, purge. The API grows in
P2; nothing in P1 should block those additions.
