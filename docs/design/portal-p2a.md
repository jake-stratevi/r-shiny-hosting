# Portal P2a — self-service app creation

Status: DRAFT for Jake's review, 2026-09-10. Builds on portal.md (product),
portal-api.md (the P1 contract this extends), proxy.md (the runtime).

**What it delivers:** the "+ New app" button works. A Stratevi admin uploads
a zip of an R Shiny app, chooses who may see it and when it expires, and a
few minutes later it is live at `<key>.tools.stratevi.com` in its own
container with its own credentials — no Terraform, no console, no engineer.

Out of scope here (P2b): version history, rollback, delete/purge. P2a ships
create + first release only, but its data model must not foreclose them.

## The pipeline

```
wizard  ->  zip to S3  ->  CodeBuild  ->  ECR image  ->  SDK provisioning  ->  live
            (validated)   (wraps in      shiny-<key>    repo, role, task-def,
                           Dockerfile)   :r1            service@0, callback, row
```

Nothing here is Terraform. Per ADR-0012: Terraform owns what engineers edit
(the pipeline's own infrastructure — bucket, CodeBuild project, boundary
policy); the portal creates per-app resources through the SDK.

## Terraform additions (proxy/ stack)

- **`shiny-portal-uploads-<acct>`** S3 bucket. SSE-S3, public access blocked,
  lifecycle expiring objects after 7 days (the zip is an input, not an
  archive — the image is the artifact). Covered by the deploy policy's
  existing `shiny-*` grant.
- **`shiny-app-build`** CodeBuild project. `aws/codebuild/standard:7.0`,
  privileged (it builds images), 30-minute timeout, its own service role
  scoped to: read the uploads bucket, write logs, and ECR push **restricted
  to `shiny-*` repositories**. Takes `APP_KEY`, `ZIP_KEY`, `RELEASE_TAG` as
  environment overrides per build.
- **`shiny-app-boundary`** IAM permissions boundary policy — see below.
- Portal task-role additions, each narrowly scoped: `s3:PutObject` on the
  uploads bucket; `codebuild:StartBuild`/`BatchGetBuilds` on that one
  project; `ecr:CreateRepository`/`DescribeRepositories`/`PutLifecyclePolicy`
  on `shiny-*`; `ecs:RegisterTaskDefinition`, `ecs:CreateService`,
  `ecs:DescribeServices` on the cluster; `cognito-idp:UpdateUserPoolClient`
  + `DescribeUserPoolClient` on the one client; `iam:CreateRole`,
  `PutRolePolicy`, `TagRole` on `role/shiny-app-*` **only with the boundary
  condition**; `iam:PassRole` for those roles to `ecs-tasks.amazonaws.com`.

### The IAM boundary — the one genuinely sensitive part

A web service that can create IAM roles is a privilege-escalation engine
unless it is fenced. The fence is a Terraform-owned permissions boundary
that every portal-created role must carry:

```
iam:CreateRole / iam:PutRolePolicy on arn:aws:iam::<acct>:role/shiny-app-*
  Condition: StringEquals { iam:PermissionsBoundary = <shiny-app-boundary arn> }
```

The boundary itself permits only: ECS Exec (`ssmmessages:*`), CloudWatch
Logs writes, and `s3:GetObject` on `shiny-app-data-<acct>/<app-key>/*`. A
role created by the portal therefore cannot exceed that set **even if the
portal is compromised and writes itself an `AdministratorAccess` inline
policy** — the boundary caps the effective permissions. The portal cannot
create roles outside the `shiny-app-*` prefix, cannot attach managed
policies, and cannot alter the boundary (that requires a Terraform apply).

This is the mechanism that makes "each app in its own secure environment"
true at the credential layer, not just the container layer.

## The build

`proxy-app/buildspec/` holds a Dockerfile **template** and the buildspec.
The buildspec: downloads and unzips the bundle, refuses anything with a path
escaping the root (zip-slip), renders the template, builds, pushes
`shiny-<key>:r<N>` and `:latest`.

The template is the dashboard's proven Dockerfile generalized: pinned
`rocker/r-ver:4.4.1`, a pinned P3M snapshot date, packages from the app's
declared list, `SHINY_CPU_WORKERS` honored (ADR-0011), **no heartbeat**
(proxied apps don't need one). Package installation is the slow part —
expect 10–20 minutes for a first build, and say so in the UI.

Package declaration: the wizard reads a `renv.lock` if present, else a
`packages.txt`, else scans `library()`/`require()` calls and shows the
detected list for confirmation. Never silently guess.

## Validation (before a build is started)

Zip ≤ 100 MB, ≤ 5000 entries, ≤ 250 MB extracted, no path escaping root, no
symlinks, and an entrypoint (`app.R`, or `ui.R` + `server.R`) at the root or
in exactly one wrapper directory (flatten it, like assembled.work does).
Reject with a specific message naming the file — "no app.R or ui.R/server.R
found" beats "invalid bundle".

**Malware scanning is deliberately absent.** An R app is code we have chosen
to execute; scanning it is theater. Containment is the control: per-app
role under the boundary, per-app container, no NAT egress, capped compute.
Say exactly this in the security questionnaire.

## Provisioning order (and rollback on failure)

1. Reserve the row: `shiny-proxy-apps` item with `status: building`. The
   slug reservation is the mutex — a conditional put on `attribute_not_exists`
   makes concurrent creation of the same key impossible.
2. ECR repository `shiny-<key>` + lifecycle policy (keep 5 images).
3. IAM role `shiny-app-<key>-task` with the boundary.
4. Start the CodeBuild job; poll it (`live_state: building`).
5. On success: register the task definition, create the ECS service at
   **desired 0**, add the host's callback URL to the shared Cognito client,
   flip the row to `status: active`.
6. **On any failure:** set `status: build_failed`, keep the row and the log
   link, and leave the half-built resources in place for inspection rather
   than thrashing. A retry reuses them; an explicit delete cleans them up
   (P2b). Never leave an app "creating" forever — a create older than 45
   minutes with no build is reaped to `build_failed`.

Because the app is born proxied, there is **no listener rule, no target
group, no waker, no per-app Cognito client** — provisioning is five API
calls, which is why this is possible at all.

## API additions (extends portal-api.md)

| Route | Purpose |
|---|---|
| `POST /api/v1/apps/validate-key` | Slug availability + shape, live in the wizard |
| `POST /api/v1/uploads` | Returns a presigned S3 PUT for the zip |
| `POST /api/v1/apps` | Create: body is the wizard's four steps + upload key |
| `GET /api/v1/apps/{host}/build` | Build status, phase, and a log-tail |

All admin-only, all CSRF-guarded, same error envelope. New `live_state`
values: `building`, `build_failed`.

## UI (portal-ui)

The 4-step wizard behind the "+ New app" card, replacing its "coming in P2"
tooltip: **Details** (label, key with live availability, description, task
size — the two known-good Fargate sizes with ADR-0001's "size up" note) →
**Upload** (drop zone, client-side size check, detected entrypoint and
package list shown back) → **Access** (mode, people, `idle_minutes`
defaulting to 20 for dashboards / 10 for models, `max_session_hours`) →
**Expiry** (a date or an explicit "never", no default) → review → create.

Then a build screen that streams status honestly: which phase, elapsed time,
"first builds take 10–20 minutes", and the log tail on failure. Do not fake
a progress bar.

## Phasing inside P2a

1. **Terraform** (bucket, CodeBuild, boundary, portal role) — reviewable and
   safe to apply on its own; nothing uses it yet.
2. **Buildspec + Dockerfile template**, validated by building the *existing*
   dashboard bundle through it and diffing against today's image. This
   proves the pipeline before any UI exists.
3. **Backend**: validation, provisioning, build polling, the four routes.
4. **UI**: wizard + build screen.
5. **End-to-end**: create a throwaway app, watch it build, open it, confirm
   it wakes and sleeps, then delete it by hand (P2b automates that).

## Costs

CodeBuild `general1.small` at ~$0.005/minute — a 15-minute R image build is
about **$0.08**. Uploads bucket and the extra ECR repos are cents. No new
always-on compute. A created app costs nothing until someone opens it.

## Open questions for Jake

1. **Who may create apps** — any admin (all four of you), or a separate
   "creator" flag? Simplest is admin = creator.
2. **Slug policy** — `<key>.tools.stratevi.com` is public in the URL. Any
   naming rules (client codenames, no drug names in hostnames)?
3. **Package pinning** — require `renv.lock` for reproducibility, or accept
   a loose `packages.txt`? Requiring renv is stricter and better for
   validated work; accepting a list is friendlier to your team's habits.
