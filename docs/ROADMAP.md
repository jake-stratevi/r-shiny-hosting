# Roadmap

Sequenced by dependency. Each item names its ADR and its trigger.

Revised 11 September 2026 — Steps 3 and 4 shipped; see "Done".
Originally rewritten 9 September 2026 after the direction was settled in
[ADR-0014](adr/0014-standalone-control-plane.md): build a standalone Stratevi
control plane (authorizing proxy + self-service portal in one always-on
service), porting the assembled.work domain model but none of its code.
Buy-versus-build is closed — Posit Connect rejected, assembled.work will not
be extended.

## Done

| Was | Completed |
|---|---|
| Step 0 — verify dashboard stack, push image, first sign-in, **observe the sleep cycle** | ✔ September 2026. Sleep cycle observed; ADR-0002's cost model is validated. |
| Step 1 — resolve the Cognito split (ADR-0007) | ✔ Superseded by ADR-0015: the platform owns pool `us-east-1_LI3CZpwAF`, with Entra federation. Nothing points at the Hub pool. |
| Lambda portal at dashboards.tools.stratevi.com (ADR-0013) | ✔ Retired 2026-09-10, replaced by the React portal on the proxy. |
| Step 2 core — state to S3 (ADR-0009), per-app IAM roles (ADR-0010), ALB access logs | ✔ 9 September 2026. Data-to-S3 and Secrets Manager remain, deferred to when the proxy/portal need them. |
| Step 1 — the model dead link | ✔ Closed by deletion, 2026-09-11. `model/` was destroyed rather than fixed; the app was re-created through the portal as `microsimulation-model`. |
| Step 3 — the authorizing proxy | ✔ Live 2026-09-09; both apps migrated. Waker/sleeper Lambdas and the heartbeat are gone. |
| Step 4 — the portal | ✔ P1 (menu, admin, audit) 2026-09-10; P2a (creation wizard + build pipeline + SDK provisioning) 2026-09-10, proven by a real create 2026-09-11. The expiry reaper runs (`sleeper.py`). **Releases, rollback and delete-purge are NOT done** — see Step 4b. |

## Step 2 — foundations

Cheap now, tedious later, and prerequisites for every path forward. Unchanged
from the original roadmap, and all of it is what a client security review asks
about first.

| Task | ADR | Effort |
|---|---|---|
| Terraform state to S3 with locking | 0009 | Hours |
| Per-app IAM task roles | 0010 | Hours |
| Data out of container images into S3 (KMS, per-app prefix) | — | Hours |
| Secrets Manager wired as task secrets | — | Hours |
| ALB access logs to S3 | — | Hours |

Per-app roles in particular: two apps is easy, fifteen is a slog — and the
portal will be creating apps programmatically, so the role-per-app pattern must
exist before it does.

## Step 4b — finish the portal

What Step 4 specced and P1/P2a did not deliver. In rough order of how much
their absence hurts:

| Task | Why it matters |
|---|---|
| **Releases, rollback and delete-purge (P2b)** | Now the top of this list. Creation is one-way: there is NO way to ship a code fix to a deployed app through the portal. Proven on 2026-09-11 — fixing one missing `library()` call in the microsimulation model took a hand-run CodeBuild, a hand-registered task definition and a hand-issued `update-service`, and left the app's DynamoDB row still claiming release `r1`. Every app fix costs that until this exists. |
| Expiry reminders + reaper health | The reaper ITSELF is built and running (`sleeper.py`'s `_expire`, every 60s: mark expired, scale to zero, audit). What Step 4 specced and is still missing is the idempotent 7-day/1-day warning mail and a scheduler heartbeat on a health page — so today an app expires silently and nobody is told in advance. |
| **Write the release metadata back** | A release that changes the running image must update `image` and `release_tag` on the app's row. After the manual r2 deploy the portal still displays `r1`, which is worse than displaying nothing. |
| Restore the full same-key mutex | `_claim_key()` holds it; the `__key__<key>` guard row in `TransactWriteItems` is still owed. |
| Build-screen failure detail + a retry button | Pre-build, package, smoke-test and provisioning failures currently look alike. |
| Reconcile `renv.lock` against `library()` calls at upload | A missing package is a 15-minute build that fails at smoke test. Has happened twice: `shinyjs` (caught by the smoke test) and `parallel` (NOT caught — it is a base package, so it is legitimately absent from the lock while still needing `library()`, and the failure only surfaced when a user pressed a button). |
| A run-level check, not just a serve-level one | The build's smoke test waits for HTTP 200 on `/`. Anything behind a button is unverified by the pipeline, which is how the `parallel` bug reached a colleague. |
| P2.5 — user management in the portal | Adding users still means the Cognito console. |

## Step 5 — before the first external client

| Task | Why |
|---|---|
| Federate the client's IdP into the platform pool | Better than passwords you create and manage. Staff federation (`Microsoft365`) is live and is the pattern. |
| Write the security questionnaire answers | The honest containment story: per-app IAM roles under a permissions boundary, no NAT egress, capped compute, frame-ancestors denial, unguessable hostnames, 3-hour sessions, per-request audit — not scanning. Do it before you're asked. Most of the material now exists; it needs assembling. |
| **Rotate the Entra client secret and import the IdP into Terraform** | Today it is console-only drift, and the secret has been on a terminal. |
| **The real hostname denylist** | Placeholders are shipped; a client-facing app must not be nameable after another client. |
| Client magic-link access mode | **Deferred until a contract needs it** (ADR-0014). The hardest piece of the assembled.work model; the `AccessMode` enum already reserves the value. |

## The thing to guard against

The product bet has been made and shipped — faster than the 6–9 weeks it was
sized at. The guard now points the other way: **the platform can create apps
it cannot yet expire, update or delete.** Finish Step 4b before the app count
grows, because every app created in the meantime is one more that has to be
cleaned up by hand.
