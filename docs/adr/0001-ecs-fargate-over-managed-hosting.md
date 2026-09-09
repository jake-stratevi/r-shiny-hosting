# ADR-0001: ECS Fargate over managed Shiny hosting

**Status:** Accepted
**Date:** 2026-09-01

## Context

Two R Shiny applications need to be reachable by staff and by external clients.
They have very different resource profiles: the dashboard is a single file with
seven packages reading a 20 KB spreadsheet, while the microsimulation pins 74
packages, runs `parLapply` across multiple cores, and held 560 MiB of R objects
in a single observed session.

Shiny is stateful over WebSockets, which rules out anything request-scoped.

## Decision

Containerise both apps and run them as ECS Fargate tasks behind a shared
Application Load Balancer, with authentication at the load balancer.

## Alternatives considered

**shinyapps.io** — fastest path, $119/month for the tier with authentication.
Rejected on two grounds. Posit states the service is not HIPAA compliant and
does not encrypt data at rest, which fails a pharma client security review. And
it caps at 2 CPU, which degrades the microsimulation's parallelisation to a
single worker.

**Posit Connect Cloud** — $249/month, RBAC, email auth for external viewers,
16 GB / 4 CPU per deployment. Genuinely the best no-infrastructure option and
the right answer if engineering time is the scarce resource. Rejected because
the self-managed path is the one that survives a client security review and
supports the eventual multi-app product.

**Posit Connect self-hosted** — five-figure annual licence. Still the buy option
if procurement ever blocks on vendor support.

**EC2 with Shiny Server or ShinyProxy** — cheapest at low scale, but always-on
compute and manual patching. ShinyProxy remains the right answer at higher app
counts; see ADR-0008.

**AWS App Runner** — scales to zero natively, but does not support WebSockets.
Disqualifying.

**Lambda** — stateless and request-scoped. Not applicable.

## Consequences

Full control over isolation, encryption, audit and identity — the things a
pharma security questionnaire asks about.

Significantly more engineering than a managed service, as the build history
shows. We own the cold-start problem, the idle-detection problem, and the
per-app provisioning problem, none of which exist on Connect.

Because Fargate bills per second, a larger task that finishes a CPU-bound run
faster costs roughly the same as a smaller one that takes longer. For the
microsimulation, size up rather than down.
