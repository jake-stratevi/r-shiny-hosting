# ADR-0009: Terraform state in S3

**Status:** Proposed
**Date:** 2026-09-08

## Context

State currently lives in `.tfstate` files on one laptop, across two directories.
No locking, no versioning, no backup. If that machine dies, the record of what
exists in AWS dies with it — the resources keep running and billing, and nobody
can destroy them cleanly.

Two concurrent applies would silently corrupt state.

## Decision

An S3 bucket with versioning enabled, one key per stack, using S3 native
locking (`use_lockfile = true`, Terraform 1.10+). The backend blocks are already
written and commented out in each stack's `versions.tf`.

The deployment IAM policy already scopes S3 access to `stratevi-tf-state-*`.

## Consequences

State survives the laptop. Versioning means a corrupted state can be rolled back.
Locking makes concurrent applies safe, which matters as soon as a second person
is involved.

Migration is `terraform init -migrate-state` per stack — straightforward, but do
it before the state gets more valuable.

`use_lockfile` requires Terraform 1.10 or later. On older versions, use a
DynamoDB lock table instead.

This is a prerequisite for CI-driven applies, which is in turn a prerequisite
for the release governance an enterprise client will ask about.
