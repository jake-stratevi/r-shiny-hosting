# ADR-0004: Public subnets, no NAT Gateway

**Status:** Accepted
**Date:** 2026-09-01

## Context

The reflexive AWS pattern puts application tasks in private subnets behind a NAT
Gateway. NAT costs $0.045/hour plus $0.045/GB — about $32.85/month before any
traffic.

On a platform whose entire compute bill is projected at $30/month, that would
more than double the cost for no benefit we could articulate.

Fargate tasks need outbound access only to pull from ECR and write to CloudWatch
Logs.

## Decision

Tasks run in public subnets with `assign_public_ip = true`, in a security group
whose only ingress rule references the ALB's security group.

The tasks have public IPs but are not reachable from the internet — the security
group denies everything that isn't the load balancer.

## Alternatives considered

**NAT Gateway** — $32.85/month. Rejected on cost.

**VPC interface endpoints** for `ecr.api`, `ecr.dkr` and `logs`, plus the free
S3 gateway endpoint — $0.01/hour per AZ each, roughly $22/month. Cheaper than
NAT, still expensive relative to the total. This is the fallback if a security
review demands private subnets.

## Consequences

The single largest avoidable cost in this architecture is avoided.

"Public subnet" reads badly on a security questionnaire even though the security
group makes it equivalent. Expect to explain it, and have the interface-endpoint
migration ready as the answer.

Egress is unrestricted. A published app can make arbitrary outbound calls. This
is acceptable while only staff publish; it becomes a real concern under
ADR-0008's enterprise model.
