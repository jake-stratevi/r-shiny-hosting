# Roadmap

Sequenced by dependency. Each item names its ADR and its trigger.

Rewritten 9 September 2026 after the direction was settled in
[ADR-0014](adr/0014-standalone-control-plane.md): build a standalone Stratevi
control plane (authorizing proxy + self-service portal in one always-on
service), porting the assembled.work domain model but none of its code.
Buy-versus-build is closed — Posit Connect rejected, assembled.work will not
be extended.

## Done

| Was | Completed |
|---|---|
| Step 0 — verify dashboard stack, push image, first sign-in, **observe the sleep cycle** | ✔ September 2026. Sleep cycle observed; ADR-0002's cost model is validated. |
| Step 1 — resolve the Cognito split (ADR-0007) | ✔ Old pool deleted; everything runs on the Hub pool `us-east-1_6vtAiYEpv`. |
| Lambda portal at dashboards.tools.stratevi.com (ADR-0013) | ✔ Live. Interim — retired when the ADR-0014 proxy ships. |
| Step 2 core — state to S3 (ADR-0009), per-app IAM roles (ADR-0010), ALB access logs | ✔ 9 September 2026. Data-to-S3 and Secrets Manager remain, deferred to when the proxy/portal need them. |

## Step 1 — fix the model dead link

`shiny-model` has a service but its ECR repository is empty; anyone visiting
`model.tools.stratevi.com` triggers a `CannotPullContainerError` retry loop
(see STATUS.md "Broken right now"). Build and push the model image: needs the
full app directory plus the R changes in ADR-0006 and ADR-0011, and a cap on
user-supplied iteration counts.

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

## Step 3 — the authorizing proxy (ADR-0014, first half)

Build and ship the proxy **in front of the two existing apps, before any
portal exists** — it is independently valuable and de-risks the request path
early. Entitlements table seeded from today's `catalog.yaml`; host-based
routing on one listener rule; wake-on-request with a branded "starting up"
page; server-side idle detection; audit log; fail-closed.

Retires on arrival: the waker Lambdas, the ADR-0006 client-side heartbeat, the
~50-app target-group ceiling, and the catalog/tfvars dual-list sync problem.

Rough size: 2–3 weeks.

## Step 4 — the portal (ADR-0014, second half)

The self-service UI on the proxy's service: 4-step creation wizard (details →
upload → access → required expiry → review), SDK provisioning per ADR-0012
(ECR repo + task definition + service — nothing else, the proxy covers
routing), a CodeBuild pipeline turning an uploaded R bundle into an image from
the pinned rocker base, the expiry reaper (hourly; expire = scale to zero +
block at proxy; idempotent 7-day/1-day reminders; scheduler heartbeat on a
health page), release rollback, and status badges.

Rough size: 3–4 weeks after Step 3.

## Step 5 — before the first external client

| Task | Why |
|---|---|
| Federate the client's IdP into the Hub pool | Better than passwords you create and manage |
| Write the security questionnaire answers | The honest containment story: per-app IAM roles, no NAT egress, capped compute — not scanning. Do it before you're asked. |
| Client magic-link access mode | **Deferred until a contract needs it** (ADR-0014). The hardest piece of the assembled.work model; the `AccessMode` enum already reserves the value. |

## The thing to guard against

Unchanged: building the enterprise version before there is an enterprise
customer. Steps 1–2 are worth doing this week regardless. Steps 3–4 are the
product bet — sized at roughly 6–9 weeks end to end — and the trigger for
starting them is a real second client or real administrative load, not
enthusiasm.
