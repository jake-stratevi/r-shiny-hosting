# Architecture decision records

One file per decision. Numbered, immutable once accepted — a decision that
changes gets a new ADR that supersedes the old one, rather than an edit.

| # | Decision | Status |
|---|---|---|
| [0001](0001-ecs-fargate-over-managed-hosting.md) | ECS Fargate over shinyapps.io, Posit Connect or EC2 | Accepted |
| [0002](0002-scale-to-zero.md) | Scale to zero via waker and sleeper Lambdas | Accepted |
| [0003](0003-three-stack-split.md) | Three Terraform stacks with SSM as the contract | Accepted |
| [0004](0004-no-nat-gateway.md) | Public subnets, no NAT Gateway | Accepted |
| [0005](0005-subdomain-delegation.md) | Delegate `tools.stratevi.com` to Route 53 | Accepted |
| [0006](0006-heartbeat-idle-detection.md) | Client-side heartbeat for idle detection | Accepted, known fragile |
| [0007](0007-reuse-hub-cognito-pool.md) | Reuse the Assembled Hub Cognito pool | Accepted |
| [0008](0008-authorization-strategy.md) | Authorization: in-app allowlist now, proxy later | Accepted (allowlist live; proxy folded into ADR-0014) |
| [0009](0009-remote-state.md) | Terraform state in S3 | Accepted |
| [0010](0010-per-app-iam-roles.md) | Per-app IAM task roles | Accepted |
| [0011](0011-fargate-cpu-detection.md) | Worker count from environment, not `detectCores()` | Accepted |
| [0012](0012-terraform-scope.md) | Terraform for the platform, SDK for dynamic apps | Accepted (implemented by ADR-0014) |
| [0013](0013-lightweight-shiny-portal.md) | A lightweight Lambda portal at dashboards.tools.stratevi.com | **Retired** 2026-09-10 (replaced by the ADR-0014 portal) |
| [0014](0014-standalone-control-plane.md) | Standalone Stratevi control plane: proxy + portal as one service | Accepted — proxy live, portal P1 live |
| [0015](0015-dedicated-user-pool.md) | Dedicated user pool for the platform (supersedes 0007) | Accepted, being built |

**Accepted** means built and deployed. **Proposed** means decided but not yet
implemented — see [ROADMAP.md](../ROADMAP.md) for sequencing.

## Template

```markdown
# ADR-NNNN: Title

**Status:** Proposed | Accepted | Superseded by ADR-NNNN
**Date:** YYYY-MM-DD

## Context
What forced a decision. Constraints, costs, what we knew at the time.

## Decision
What we're doing, stated plainly.

## Alternatives considered
What else was on the table and why it lost.

## Consequences
What this makes easy, what it makes hard, and what we'll have to revisit.
```
