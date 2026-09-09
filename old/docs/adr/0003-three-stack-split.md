# ADR-0003: Three Terraform stacks with SSM as the contract

**Status:** Accepted
**Date:** 2026-09-01

## Context

The initial build was one stack covering both apps. The request was to package
each app as an independently deployable unit.

Splitting naively into two would duplicate the ALB, VPC and Cognito pool —
$16.43/month per extra load balancer, for nothing.

## Decision

Three stacks:

- **platform** — VPC, ALB, wildcard certificate, Cognito, ECS cluster, shared
  IAM roles. Deployed once.
- **dashboard** and **model** — byte-identical Terraform, different tfvars. Each
  owns its ECR repository, Cognito app client, target groups, listener rule,
  task definition, service, and its own waker and sleeper.

The platform publishes 20 values to SSM Parameter Store under
`/shiny/platform/*`. App stacks read them with `data "aws_ssm_parameter"`.

## Alternatives considered

`terraform_remote_state` is the idiomatic choice, but it requires every app
stack to have read access to the platform's state file. SSM lets someone deploy
an app without any visibility into the platform's internals.

## Consequences

Apps deploy independently, in any order, by different people. Adding a third app
is a copy of the directory and a new tfvars.

`project` must match exactly across all three tfvars — it is how app stacks
locate the SSM namespace. A mismatch surfaces as a parameter-not-found error
that doesn't point at the cause.

`listener_rule_priority` must be unique across every app. There is no
enforcement; the register lives in the platform README. Dashboard 100, model
200, ECS association rules at +700.

Teardown order is fixed: apps before platform.

Because the two app stacks are duplicated code, a bug fix has to be applied
twice. This happened four times during the initial build.
