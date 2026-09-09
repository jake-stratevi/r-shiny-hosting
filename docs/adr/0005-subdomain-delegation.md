# ADR-0005: Delegate `tools.stratevi.com` to Route 53

**Status:** Accepted
**Date:** 2026-09-04

## Context

ALB's `authenticate-cognito` action only works on an HTTPS listener, and ACM
will not issue a certificate for the ALB's own `*.elb.amazonaws.com` hostname.
So the platform needs a domain we control with a valid certificate.

`stratevi.com` is on Google Cloud DNS, managed by IT.

## Decision

Create a Route 53 hosted zone for `tools.stratevi.com` and have IT add one NS
record set on `stratevi.com` delegating the `tools` label to its four
nameservers.

The platform issues one wildcard certificate for `*.tools.stratevi.com`, so app
stacks never touch ACM and adding an app never requires a DNS ticket.

## Alternatives considered

**Two CNAMEs, no delegation** — one wildcard CNAME to the ALB and one permanent
ACM validation record. Two records instead of four, transfers no authority, and
would have avoided the hosted zone entirely. Genuinely the smaller ask.
Rejected because the delegation was approved quickly and delegation makes
certificate renewal and future apps fully automatic. Worth revisiting if the
delegation is ever withdrawn.

**A throwaway registered domain** — ~$12/year, no IT involvement. Rejected
because `stratevi-tools-prod.net` reads badly to a client.

## Consequences

Certificate issuance and renewal are automatic. Adding an app is a Route 53
alias record Terraform creates itself.

Route 53 alias records are free and follow the ALB automatically.

Cost: $0.50/month for the zone.

The delegation is a dependency on IT. If those NS records are ever removed, the
platform loses TLS at the next certificate renewal, not immediately — which
makes it a failure that surfaces long after the cause.
