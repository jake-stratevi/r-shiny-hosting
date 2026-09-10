# Self-service portal — design spec (ADR-0014, second half)

Status: DRAFT for Jake's review, 2026-09-09. Nothing here is built. The proxy
spec (proxy.md) is the first half and is live; this layers the control plane
on top of it.

## Shape

The portal is not a new service. It is new routes on the existing proxy
service (`proxy-app/`), served on the proxy's own hostname
(`proxy.tools.stratevi.com` — Cognito callback already registered). Same
task, same table, same deploy pipeline; the always-on $9/month buys both
roles, exactly as ADR-0014 promised.

Two faces:

- **`/` (the menu)** — every authenticated user sees tiles for the apps their
  email is entitled to, live status included (awake / starting / asleep).
  This REPLACES the ADR-0013 Lambda portal at dashboards.tools.stratevi.com:
  that hostname joins `app_hosts`, a special row routes it to the portal
  rather than to an ECS service, and the `portal/` stack + `catalog.yaml`
  retire. One entitlements source, drift problem gone (the last ADR-0013
  consequence closed).
- **`/admin`** — the control plane: create, configure, release, expire,
  audit. Visible only to platform admins.

## Decisions

**UI stack: React SPA + JSON API.** (Revised from Jinja2/htmx at Jake's
request, 2026-09-09.) Vite + React + TypeScript + Tailwind, served as a
static bundle by the proxy service and talking to `/api/v1/*` JSON endpoints
on the same service — same origin, so auth is simply the ALB's Cognito
session cookie, with a CSRF header on mutating calls. The API is the real
product boundary: anything the UI can do, a future CLI or automation can do
against the same endpoints. Build is a Docker multi-stage (Node builds the
bundle, the runtime image stays pure Python and just serves `dist/`); no
Node in production, but the repo gains a `portal-ui/` package and a Node
build-time dependency. Stratevi design system (plain, professional) — NOT
Assembled Intelligence branding.

**Hard rule:** the proxy's own operational pages (starting-up, 401/403,
expired, unknown-host) stay as the embedded plain-HTML pages they are today.
They render during cold starts and failures and must never depend on a JS
bundle loading.

**Roles, minimal.** A `__config__` row in `shiny-proxy-apps` holds
`admin_emails` (managed as a Terraform `aws_dynamodb_table_item`, so admins
are code-reviewed). Admins see `/admin`; everyone else sees only the menu.
Owner-level roles, teams, invitations: portal phase 2+, ported from the
assembled.work model only when a second organization actually needs them.

**App creation wizard — 4 steps, ported from assembled.work:**

1. *Details*: label, key (slug, becomes `<key>.tools.stratevi.com` and
   `shiny-<key>` everywhere), description, task size (dropdown of the two
   known-good sizes: 0.5 vCPU/2 GB and 4 vCPU/16 GB — ADR-0001's "size up"
   rule in the helptext).
2. *Upload*: a zip of the app directory (`app.R` or `ui.R`/`server.R` layout,
   data files included). Validated: size caps, zip-slip, has an entrypoint.
   No malware scanning theater — an R app is code we choose to run; the
   controls are containment (per-app role, no NAT, capped compute), and the
   security page says so honestly.
3. *Access*: access mode (`all_users` / `users` + email list; the reserved
   modes shown greyed out), `idle_minutes` (default 15), `max_session_hours`
   (default 12).
4. *Expiry — required, explicit*: a date or a deliberate "never expires"
   choice. No silent default. Then review-and-create.

**Provisioning (ADR-0012: SDK, not Terraform), in this order:**

ECR repo `shiny-<key>` → per-app IAM role `shiny-app-<key>-task` → task
definition → ECS service (desired 0, no load balancer — born proxied) →
Cognito callback for the new host added to the shared client → row in
`shiny-proxy-apps` with `status: building`. The proxy serves a "release in
progress" page until the first release is live.

**Per-app IAM roles from a web service, safely.** Handing the portal
`iam:CreateRole` is the one spicy grant. Contained the standard way: the
portal's task role may create/delete only `role/shiny-app-*`, and ONLY with a
`iam:PermissionsBoundary` condition pinning a Terraform-managed boundary
policy (ECS-Exec + that app's future S3 prefix, nothing else). A compromised
portal can mint roles that can do nothing interesting. The boundary policy
and the IAM grants live in `proxy/iam.tf`, reviewed like everything else.

**Releases: CodeBuild builds the image; the portal never runs Docker.**
One shared, Terraform-owned CodeBuild project. The portal uploads the zip to
`shiny-portal-uploads-<acct>` (covered by the deploy policy's `shiny-*`
pattern), starts a build with the app's ECR repo as a parameter; the
buildspec wraps the bundle in the standard Dockerfile template (pinned rocker
base + P3M snapshot, `SHINY_CPU_WORKERS` per ADR-0011, NO heartbeat —
proxied apps don't need it), pushes `shiny-<key>:r<N>`. The portal registers
a new task-def revision pointing at the immutable tag and updates the
service. **Rollback = repoint the service at the previous revision** — the
assembled.work symlink swap, translated. Release history lives in
`shiny-proxy-apps` sub-attributes (last 5 kept, like their
`keep_successful_releases`).

**Lifecycle, completing the reaper.** The proxy already enforces expiry at
request time and scales expired apps down. The portal adds: 7-day and 1-day
email reminders (idempotent via audit-event markers, the assembled.work
trick), owner-initiated extension ("revive" allowed only if a good release
exists), disable/enable, and delete = disable + `purge_after` 30 days, after
which a nightly job deletes service, task defs, ECR repo, role, Cognito
callback, and row. Email via SES — requires one-time domain verification on
tools.stratevi.com (DNS records; Jake action when we get there).

**Status badges everywhere.** awake / starting / asleep / building /
build-failed / disabled / expired — derived live from ECS + the row, same
palette as the proxy's pages.

## Explicitly deferred

Client magic links (contract-triggered, per ADR-0014), teams/organizations
modes, per-app data-in-S3 upload UI (do data-to-S3 as infra first), CI-driven
Terraform, pre-warm windows (add to the proxy only if the soak says mornings
hurt).

## Phasing (each lands independently useful)

| Phase | Contents | Rough size |
|---|---|---|
| P1 | Menu (retires ADR-0013 Lambda + catalog.yaml) + read-only admin: app list, live status, audit viewer, edit allowlist/idle/cap/expiry/disable — includes the one-time React/Vite + API scaffolding | ~1.5 weeks of agent work |
| P2 | Creation wizard + SDK provisioning + CodeBuild releases + rollback | ~2 weeks |
| P3 | SES reminders, purge job, extension requests | ~3–4 days |

Costs: no new always-on compute. CodeBuild ~½¢/build-minute (an R image build
≈ 10–20 min, so ~5–10¢ per release). Uploads bucket + SES: cents.

## Open questions for Jake

1. Menu hostname: keep `dashboards.tools.stratevi.com` (continuity) as the
   menu, or move to something like `apps.tools.stratevi.com`?
2. Admin list day one: just you, or Nick/Yi/Josh too?
3. P1 before model migrates, or model first the moment its source lands?
   (They don't conflict; it's about which review lands on your desk first.)
