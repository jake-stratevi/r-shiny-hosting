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

**Where that inspection runs: in the browser, before the upload** — see
portal-api.md, "Bundle inspection is CLIENT-SIDE". Not on the proxy task,
which every app's traffic flows through. It is advisory only; `validate.py`
in CodeBuild is the authority and re-checks everything server-side.

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

1. Reserve the row: `shiny-proxy-apps` item with `status: building`, via a
   conditional put on `attribute_not_exists(host)`.

   **That put is no longer the same-key mutex, and this matters.** It was,
   while the host was exactly the key — two wizards submitting `model` raced
   for one partition key and one lost. Now the host carries a random suffix,
   so two racers no longer collide: both rows would be written, both named
   `shiny-model`, sharing one ECR repository, one IAM role and one ECS
   service, and the second build would overwrite the first app's image at the
   same tag. Silently.

   `Provisioner._claim_key` asks the question again immediately after the
   reserve and before the first AWS call: re-read the table, and if another
   row holds the key, tie-break on `(created_at, host)` — a verdict every
   racer computes identically, so exactly one survives. The loser goes to
   `build_failed` having provisioned nothing.

   It is weaker than the conditional put it replaces, because it reads
   through an eventually-consistent Scan. Full strength wants a
   `__key__<key>` guard row written in the same `TransactWriteItems` as the
   app row — a data-model change, so it belongs with P2b's delete/purge work
   (which has to clean the guard row up anyway). No new IAM: transactions
   authorise through `dynamodb:PutItem`, which the task role already holds.
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

## Decisions (Jake, 2026-09-10)

**Creation is its own permission, not implied by admin.** The `__config__`
row gains `creator_emails` (Terraform-owned, exactly like `admin_emails`).
Creating an app requires membership in that set; being an admin does **not**
grant it. Fail-closed: no row, empty set, or a read error means nobody may
create. Admin remains what it is — editing access, expiry and settings on
apps that exist. The API returns `can_create` from `/me` so the UI can hide
the "+" affordance rather than dangle a 403.

**Hostnames are policed, and unguessable.** A created app lives at
`<key>-<6 random base32 chars>.tools.stratevi.com` — e.g. key `q3-uptake`
becomes `q3-uptake-k4mr2t.tools.stratevi.com`. The suffix is minted with
`secrets` at create time, is not derived from anything, and is never chosen
by the caller (a chosen suffix is a guessable one). ~1.07 billion names per
key. Only the HOSTNAME carries it: the key stays human-readable in the UI
and in every resource name (`shiny-<key>` repo, `shiny-app-<key>-task` role,
`shiny-<key>` service).

This is defence in depth, not the control — entitlement is. Someone who
guesses or is forwarded a link and is not on the allowlist still gets a 403
and appears in the audit trail. The suffix only means a name cannot be found
by someone who was never sent it. Apps created before 2026-09-11 keep their
unsuffixed hostnames; nothing rewrites them.

The wizard shows the SHAPE, not a link: `<key>-xxxxxx.tools.stratevi.com`
with the random part marked, because `validate-key` reserves nothing and any
suffix it returned would be a different one from the app's. The real,
clickable address appears on the build screen.

The key itself is validated against, in order: shape (3–30 chars, lowercase
`a-z0-9-`, no leading/trailing/double hyphen), a reserved list (`www`,
`api`, `auth`, `admin`, `proxy`, `shinyplatform`, `dashboards`, `portal`,
`mail`, plus every existing app key), and a **denylist of substrings** held
in `__config__.key_denylist` so the terms are editable without a deploy.
Seed it with brand/molecule names and client names; the wizard rejects with
"that name can't be used in a public hostname — pick a project codename",
never echoing why a specific term is banned. **Jake still owes the actual
term list**; ship with the mechanism plus an obvious starter set.

**Packages: accept either, confirm always.** Prefer `renv.lock` when
present (best reproducibility); else `packages.txt`; else scan
`library()`/`require()` calls. Whatever the source, show the resolved list
back in the wizard for explicit confirmation before the build starts, and
record it on the release so a rebuild is reproducible.
