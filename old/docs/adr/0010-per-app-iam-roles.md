# ADR-0010: Per-app IAM task roles

**Status:** Proposed
**Date:** 2026-09-08

## Context

The platform stack creates one `shiny-task` role shared by every application.
Every app runs with identical permissions, and any app can reach anything
another app can reach.

Hosting Shiny is categorically different from hosting static HTML. A static site
is sandboxed by the browser. A Shiny app executes arbitrary R on our
infrastructure, with the task role's credentials, the container's environment
variables, and unrestricted outbound network access (ADR-0004).

Anyone who can publish an app can run code.

## Decision

Move the task role from the platform stack into each app stack, so each app gets
`shiny-<app>-task` with only the permissions that app needs. The execution role
stays shared — it only pulls images and writes logs.

The deployment IAM policy already scopes role management to `role/shiny-*`, so
no policy change is required.

## Consequences

An app compromise or a careless dependency is contained to that app.

Data access becomes per-app when data moves from container images to S3 —
`shiny-dashboard-task` can read `s3://…/dashboard/*` and nothing else. That is
the shape a client security review expects to see.

Cheap now, with two apps. Genuinely tedious at fifteen, because it means
touching every app stack and re-deploying every task definition.

Does not by itself restrict egress. An app can still make arbitrary outbound
calls; that needs interface endpoints or an egress proxy.

**This should be done before any external party publishes an app**, and ideally
before the first external client's data is on the platform.
