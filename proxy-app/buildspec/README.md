# `shiny-app-build` — the P2a image pipeline

This directory is phase 2 of P2a (`docs/design/portal-p2a.md`). It turns an
uploaded zip of an R Shiny app into a pushed ECR image, with no Terraform and
no engineer in the loop.

Nothing here is Terraform and nothing here is Python application code. Per
ADR-0012, Terraform owns the pipeline's *infrastructure* (uploads bucket,
CodeBuild project, IAM boundary) and the portal drives it through the SDK.
These files are the pipeline's *behaviour*.

| File | What it is |
|---|---|
| `buildspec.yml` | The CodeBuild buildspec (version 0.2). The only entry point. |
| `Dockerfile.template` | `dashboard-app/Dockerfile`, generalized. Rendered per build. |
| `validate.py` | Bundle safety checks, safe extraction, wrapper flattening. |
| `render.py` | Env-var contract enforcement + template substitution. |
| `make_test_bundles.py` | Generates the reference and adversarial zips. Test-only; not used at build time. |

---

## End to end

```
wizard ──▶ presigned PUT ──▶ s3://<uploads>/<ZIP_KEY>
                                   │
   portal: StartBuild(shiny-app-build, env overrides)
                                   ▼
  pre_build   locate assets ──▶ ECR login ──▶ download zip
              ──▶ validate.py  (reject / extract / flatten)
              ──▶ render.py    (check env, write Dockerfile)
  build       docker build ──▶ tag :$RELEASE_TAG and :latest
              ──▶ smoke test: run the image, curl :3838
  post_build  docker push both tags
              ──▶ SHINY_BUILD_RESULT {…} on stdout + exported variables
                                   ▼
   portal: BatchGetBuilds ──▶ register task def, create service @0, live
```

Two things are worth understanding before changing anything here.

**The rendered Dockerfile lives outside the build context.** The build context
is the extracted app; the Dockerfile is written to `$WORK/render/Dockerfile`
and passed with `-f`. An uploaded bundle may legitimately contain its own
`Dockerfile` (the dashboard bundle does), and it must be copied into the image
as an inert file rather than overwritten or — worse — used.

**Everything the pipeline rejects, it rejects in pre_build**, before a single
R package is downloaded. A bad bundle costs a few seconds, not fifteen minutes.

---

## Env var contract

**This is the interface.** The Terraform stack sets the defaults on the
CodeBuild project; the portal backend passes the rest as
`environmentVariablesOverride` on `StartBuild`. `render.py` enforces every row
of this table and fails with a message naming the variable.

### Required — the portal must pass all six on every build

| Variable | Format | Notes |
|---|---|---|
| `APP_KEY` | `^[a-z0-9](?!.*--)[a-z0-9-]{1,28}[a-z0-9]$` | 3–30 chars, lowercase alnum + hyphen, no leading/trailing/doubled hyphen. The app slug, also the hostname label and the ECR repo suffix. |
| `ZIP_KEY` | S3 object key | Key of the uploaded bundle **within `UPLOADS_BUCKET`**. No `s3://`, no bucket, no leading `/`. |
| `RELEASE_TAG` | valid Docker tag; use `r<N>` | Becomes `shiny-<key>:r<N>`. First release is `r1`. |
| `UPLOADS_BUCKET` | bucket name | `shiny-portal-uploads-<acct>`. Name only, no `s3://`. |
| `ECR_REPO_URI` | `<acct>.dkr.ecr.<region>.amazonaws.com/shiny-<key>` | **No tag.** Must end in `shiny-` + exactly `APP_KEY`; a mismatch is a hard error, because getting it wrong ships one app's image into another app's repository. |
| `PACKAGES` | space- or comma-separated R package names | Already resolved and **confirmed by the wizard** (`renv.lock` → `packages.txt` → `library()` scan). Each name must match `^[A-Za-z][A-Za-z0-9._]*$`. The pipeline never guesses; an empty value is an error. `shiny` is added automatically if the list omits it, and the build log says so. |

### Optional — sensible defaults, override deliberately

| Variable | Default | Notes |
|---|---|---|
| `R_VERSION` | `4.4.1` | Must be `X.Y.Z`. Pins `rocker/r-ver`. |
| `P3M_SNAPSHOT` | `2026-05-01` | `YYYY-MM-DD`. The Posit Package Manager snapshot date — this is what makes a rebuild reproducible. Same date the dashboard image pins. Bump deliberately. |
| `P3M_DISTRO` | `jammy` | The P3M binary distro. `rocker/r-ver:4.4.x` is Ubuntu 22.04; change this only if the base image moves. Getting it wrong silently falls back to source builds and turns a 5-minute build into a 40-minute one. |
| `EXTRA_SYSTEM_PACKAGES` | `""` | Extra apt packages appended to the base set. This is the escape hatch for apps needing `pandoc` (rmarkdown), `libgdal-dev`/`libproj-dev` (sf), `unixodbc-dev`, etc. |
| `PIPELINE_PREFIX` | `_pipeline/` | Where this directory's helper files are mirrored in the uploads bucket. Used only when the project has no source checkout. |
| `PUSH_LATEST` | `true` | Also tag and push `:latest`. |
| `TARGET_PLATFORM` | `linux/amd64` | The task definitions set `cpu_architecture = "X86_64"`. An arm64 image pulls fine and then dies with an exec format error. |
| `SMOKE_TEST` | `true` | Run the built image and curl it before pushing. |
| `DOCKER_BUILDKIT` | `1` | Required for `--progress plain`. |
| `WORK` | `/tmp/shiny-build` | Scratch directory. |

CodeBuild supplies `AWS_REGION` / `AWS_DEFAULT_REGION` itself; the buildspec
falls back to `us-east-1` if neither is present. `CODEBUILD_BUILD_ID` is used
as the image's build-id label.

### Outputs the portal reads

Two channels, both authoritative — use whichever is convenient.

1. **Exported variables** (`BatchGetBuilds` → `build.exportedEnvironmentVariables`):
   `IMAGE_URI`, `IMAGE_DIGEST`, `RELEASE_TAG`, `APP_KEY`, `BUILD_RESULT`
   (`succeeded` | `failed` | `running`), `BUILD_ERROR`.

2. **One line on stdout**, prefixed with a fixed marker so it survives a log
   tail:

   ```
   SHINY_BUILD_RESULT {"result":"failed","app_key":"demo","release_tag":"r1","image_uri":"","image_digest":"","build_id":"shiny-app-build:8f2…","phase":"pre_build","error":"no app.R or ui.R/server.R found at the bundle root or in a single wrapper directory — the bundle's top level contains: data, helpers.R"}
   ```

   `result` is `succeeded` or `failed`; `phase` is `pre_build`, `build` or
   `post_build`; `error` is the operator-readable reason and is safe to show
   to the admin who uploaded the bundle. Exactly one such line is emitted per
   build, and it is always the last thing the build prints.

### Things the Terraform stack must get right

- **Pick one of the two source shapes** and be consistent:
  - `NO_SOURCE` + `buildspec = file(".../proxy-app/buildspec/buildspec.yml")`,
    plus three `aws_s3_object` resources mirroring `Dockerfile.template`,
    `validate.py` and `render.py` to `<bucket>/_pipeline/` with
    `etag = filemd5(...)` so a repo change redeploys them; **or**
  - a source checkout of this repo, in which case the buildspec finds the
    helpers at `$CODEBUILD_SRC_DIR/proxy-app/buildspec/` and never touches
    `_pipeline/`.

  The buildspec probes for a checkout first and falls back to S3, so either
  works without editing this file. `proxy/codebuild.tf` chose the first.
- The project needs `privileged_mode = true` (it builds images) and a
  30-minute timeout.
- **The uploads bucket's 7-day expiry currently covers `_pipeline/` too**
  (`proxy/uploads.tf`, rule `expire-uploads`, `filter {}`). That is a
  deliberate call recorded in `proxy/codebuild.tf`: the assets are re-uploaded
  on every apply, and a loud "pipeline asset missing" is judged better than a
  build silently running last month's `validate.py`.

  Know the consequence before relying on it: **eight days without a
  `terraform apply`, and every app build fails in `pre_build`** — including
  the first build of a new app, which is exactly when a non-engineer is
  watching. Nothing else in the platform requires a weekly apply. If that
  trade stops looking right, the fix is a lifecycle filter excluding
  `_pipeline/`, not disabling the expiry. Worth Jake's explicit sign-off
  either way, because the failure lands on the self-service path.

---

## What the validator enforces

`validate.py`, straight from the spec's "Validation" section. Every rejection
names the problem and, where there is one, the offending file.

| Rule | Message shape |
|---|---|
| zip ≤ 100 MB, non-empty, actually a zip | `upload is 130.4 MB, larger than the 100.0 MB limit` |
| ≤ 5000 entries | `bundle contains 5201 entries, more than the 5000 allowed — this is almost always a stray renv/ library…` |
| ≤ 250 MB extracted | `bundle expands to more than 250.0 MB (limit exceeded while reading 'data/huge.rds')` |
| no path escaping the root | `path escapes the bundle root (zip-slip): '../../etc/evil.R'` |
| no absolute or drive-qualified paths | `absolute path in archive (zip-slip): '/etc/passwd'` |
| no symlinks | `symlink in bundle: 'secrets' — symlinks are rejected because they can point outside the image` |
| no special files, no CRC failures | `corrupt entry in archive (CRC mismatch): 'app.R'` |
| an entrypoint exists | `no app.R or ui.R/server.R found at the bundle root or in a single wrapper directory — the bundle's top level contains: data, helpers.R` |
| a complete entrypoint | `found ui.R at the bundle root but no server.R` |
| exactly one wrapper | `more than one directory looks like the app: 'one', 'two'` |

`app.R` at the root wins. Otherwise `ui.R` + `server.R` at the root. Otherwise,
if exactly one top-level directory contains an entrypoint, its contents are
moved up to the root ("flattened", like assembled.work) and the build proceeds;
a name collision during flattening is an error rather than a silent overwrite.
`__MACOSX/`, `.DS_Store`, `Thumbs.db` and `desktop.ini` are skipped entirely,
so a zip made with the macOS Finder behaves like one made anywhere else.

The archive is extracted **by this script**, member by member, with the
destination re-checked against its realpath — not by shelling out to `unzip`.
There is therefore no window between "checked" and "extracted" for a malicious
entry to exploit. Archive permission bits are discarded: files land 0644.

**There is no malware scanning, deliberately.** An R app is code we have chosen
to execute; scanning it is theater. Containment is the control — per-app IAM
role under the Terraform-owned permissions boundary, per-app container, no NAT
egress, capped compute. Say exactly that in the security questionnaire.

---

## The Dockerfile template

`Dockerfile.template` is `dashboard-app/Dockerfile` generalized, not a new
design: same `rocker/r-ver` base pinned by patch release, same pinned P3M
snapshot (which is what buys binary packages and a 5-minute build instead of a
40-minute one), same verify-the-install step, same single-process
`shiny::runApp` CMD with no Shiny Server layer.

Placeholders are `__UPPER_SNAKE__` tokens, substituted by `render.py` into
**`ARG` defaults**. That means the rendered Dockerfile is a complete,
self-describing record: `docker build .` on it with no arguments reproduces the
image, and `--build-arg` still overrides. The buildspec passes both, on
purpose. `render.py` errors if the template contains a token it does not know
(comment lines excluded, so the header prose is safe, as is the literal
`__linux__` in the P3M URL).

Differences from `dashboard-app/Dockerfile` a reviewer should know:

1. **No heartbeat, and that is correct.** ADR-0006's snippet exists because the
   sleeper Lambda infers idleness from the ALB's `RequestCountPerTarget`, and a
   Shiny websocket emits zero HTTP requests. Apps built here are *born
   proxied*: they have no listener rule, no target group, no waker, and no
   per-app Cognito client. The proxy holds the websocket and knows who is
   connected as a fact rather than an inference (`docs/design/proxy.md`, "Sleep
   is server-side"). A heartbeat would be dead code that generates phantom
   traffic. Do not add one, and do not "fix" an uploaded app that lacks one.
2. **Runs as uid 10001**, not root. Per-app containment is a stated P2a
   control; the container layer should not hand an uploaded bundle root.
3. **Wider apt set** — the dashboard's four packages plus the graphics, font
   and text-shaping libraries that ggplot2/ragg/systemfonts, plotly, readxl and
   stringi need. Geospatial and ODBC families are excluded on size grounds and
   available through `EXTRA_SYSTEM_PACKAGES`.
4. **`ENV SHINY_CPU_WORKERS=1`.** ADR-0011: the app must read this instead of
   calling `parallel::detectCores()`, which reports the Fargate *host's* cores.
   The task definition sets the real value and an ECS environment entry
   overrides the image ENV, so the image never fights it — the default exists
   only so an app run outside ECS gets a sane `1` rather than an empty string.
   Nothing in the image sets `options(cl.cores)` or otherwise second-guesses
   the app.
5. **OCI labels** record app key, release, R version, snapshot and package
   list, so `docker inspect` on a pulled image answers "what is this and how
   was it built" without a DynamoDB lookup.
6. **A `HEALTHCHECK`** for local `docker run`. ECS uses the task definition's
   `healthCheck`, which wins; this is informational.
7. The app-key build arg is named **`APP_SLUG`**, not `APP_KEY`. BuildKit's
   `SecretsUsedInArgOrEnv` lint fires on any ARG whose name ends in `KEY` and
   would print an alarming false positive in every build log. The environment
   variable the portal passes is still `APP_KEY`; the buildspec maps it.

A bundle's own `.dockerignore` **is** honoured, because it sits in the build
context root. The dashboard bundle's excludes `README.md`, so the reference
image below does not contain it. If an app's files go missing from an image,
look there first.

---

## Testing it locally

You cannot run CodeBuild on a laptop, but every step except `StartBuild` and
`docker push` runs locally. Docker Desktop must be running.

```bash
cd proxy-app/buildspec

# 1. Bundle an app exactly as the wizard would receive it.
cd ../../dashboard-app && zip -r /tmp/bundle.zip . && cd -

# 2. Validate + extract + flatten.
python3 validate.py --zip /tmp/bundle.zip --dest /tmp/appsrc --json-out /tmp/validate.json

# 3. Render. Same variables the portal will pass.
export APP_KEY=dashboard RELEASE_TAG=r1 \
       ZIP_KEY=uploads/dashboard/r1.zip \
       UPLOADS_BUCKET=shiny-portal-uploads-652063276768 \
       ECR_REPO_URI=652063276768.dkr.ecr.us-east-1.amazonaws.com/shiny-dashboard \
       PACKAGES="shiny dplyr tidyr stringr plotly scales readxl jsonlite"
python3 render.py --out /tmp/Dockerfile --json-out /tmp/render.json \
                  --packages-out /tmp/packages.txt

# 4. Build and smoke-test, exactly as the build phase does.
docker build --platform linux/amd64 -f /tmp/Dockerfile -t shiny-dashboard:r1 /tmp/appsrc
docker run --rm -d -p 3838:3838 --name smoke shiny-dashboard:r1
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:3838/    # expect 200
docker rm -f smoke
```

On PowerShell the same commands work with `python` instead of `python3`, and
`$env:APP_KEY = "..."` instead of `export`. Note that on Windows Docker paths
must be passed in Windows form (`C:/Users/...`); Git Bash mangles POSIX-looking
paths unless you set `MSYS_NO_PATHCONV=1`.

To check the buildspec's shell without CodeBuild:

```bash
python3 -c "
import yaml, subprocess, os
d = yaml.safe_load(open('buildspec.yml'))
for phase, v in d['phases'].items():
    for i, c in enumerate(v['commands']):
        open('/tmp/blk.sh','w',newline='\n').write(c)
        r = subprocess.run(['bash','-n','/tmp/blk.sh'])
        print(phase, i, 'OK' if r.returncode == 0 else 'SYNTAX ERROR')
"
```

---

## Known-good reference test

The spec makes phase 2 provable: build the **existing dashboard bundle**
through this pipeline and compare with today's image. That is the regression
test for any change to these files.

**Inputs**

| | |
|---|---|
| Bundle | everything in `dashboard-app/` — `app.R`, `access.R`, `Tx_sequence_after_2022_tarpeyo.xlsx`, `Dockerfile`, `README.md`, `.dockerignore` (~25 KB zipped, ~51 KB extracted, 6 entries) |
| `PACKAGES` | `shiny dplyr tidyr stringr plotly scales readxl jsonlite` |
| `R_VERSION` | `4.4.1` |
| `P3M_SNAPSHOT` | `2026-05-01` |

**Expected — these are measured, not guessed (2026-09-10, Docker 28.2.2)**

- `validate.py` → exit 0, `entrypoint: app.R`, 6 entries, 51.5 KB extracted.
  Zipped with a `dashboard-app/` wrapper directory instead, it reports
  `flattened wrapper directory 'dashboard-app'` and is otherwise identical.
- `render.py` → exit 0, no `__TOKEN__` left in a non-comment line.
- `docker build` → success in about 4 minutes on a warm base image.
  **76 packages install as `*binary*` and 0 as `*source*`** — that ratio is the
  single best signal that the P3M snapshot URL and the `HTTPUserAgent` option
  are both right. If you ever see `installing *source* package`, stop and fix
  `P3M_DISTRO`; the build will otherwise take ten times as long.
- Image size **1.7 GB**, against the 1.2–1.5 GB `dashboard-app/README.md`
  records for today's image. The delta is the wider apt set (fonts, image
  codecs, ICU) plus the extra transitive R packages P3M resolved at this
  snapshot. Worth knowing for cold-start: ~1.7 GB is roughly 40–60 seconds of
  pull, so the proxy's "starting up" page copy ("30–60s") still holds.
- `docker run` + `curl localhost:3838/` → **HTTP 200 within ~4 seconds**, the
  Shiny page renders, and `docker exec … id` reports
  `uid=10001(shiny) gid=100(users)`. The app boots even though `ACCESS_MODE`
  is unset, because `access.R`'s gate is a no-op without it.
- The only intended behavioural difference from the shipped dashboard image is
  the non-root user. The heartbeat *does* appear in the served HTML, because
  it is in this particular bundle's own `app.R` — the real dashboard source.
  The template never adds one; it also never strips one.

**Negative cases**, all of which must fail in `pre_build` with the messages in
the validator table above. `make_test_bundles.py` generates every one of them
alongside the two real bundles, so this is reproducible rather than folklore:

```bash
python3 make_test_bundles.py --out /tmp/bundles
for z in /tmp/bundles/*.zip; do
  echo "== $(basename "$z")"
  python3 validate.py --zip "$z" --dest /tmp/x || true
done
```

Expect `dashboard-root.zip`, `dashboard-wrapped.zip` and `macjunk.zip` to pass
and the other seven — `slip`, `absolute`, `symlink`, `noentry`, `twodirs`,
`halfpair`, `toomany` — to be rejected, each with a message naming what is
wrong and where.
