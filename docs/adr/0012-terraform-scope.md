# ADR-0012: Terraform for the platform, AWS SDK for dynamic apps

**Status:** Proposed
**Date:** 2026-09-08

## Context

Adding an app today means copying a directory, editing tfvars, picking an unused
listener-rule priority by hand, and running `terraform apply`. That is fine for
someone comfortable with Terraform and impossible for anyone else.

The eventual target is the existing Laravel preview portal — where a user
uploads an app, it is scanned, built, and published at a slug, with access
modes, expiry and audit already modelled.

## Decision

Terraform owns infrastructure that changes rarely and is edited by engineers:
VPC, ALB, cluster, Cognito, IAM, the proxy.

Anything a portal creates on demand — ECR repositories, task definitions,
services, listener rules, DNS records — is created through the AWS SDK from
application code, not Terraform.

## Alternatives considered

**Terraform workspaces per app, driven by CI.** Keeps one tool, but every app
creation becomes a git commit and a pipeline run, and the portal would need to
wait on it. State grows unboundedly.

## Consequences

The portal can create an app in seconds without a Terraform run, which is what
makes self-service possible.

Two systems provision AWS resources, and the boundary has to be respected or
they will fight. The boundary is: if a human edits it, Terraform owns it.

The portal already has this shape — its Cloudflare provisioning works exactly
this way.

Requires the ~50-app ALB ceiling to be lifted first, either by raising the
target-group quota or by the proxy in ADR-0008 collapsing every app onto one
listener rule. A shared waker target group that reads the `Host` header would
also roughly double the ceiling for very little work.
