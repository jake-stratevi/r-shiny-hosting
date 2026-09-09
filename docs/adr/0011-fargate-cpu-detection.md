# ADR-0011: Worker count from the environment, not `detectCores()`

**Status:** Accepted
**Date:** 2026-09-01

## Context

The microsimulation calls
`makeCluster(getOption("cl.cores", max(1, parallel::detectCores() - 2)))`.

Inside a Fargate task, `parallel::detectCores()` reads the host's
`/proc/cpuinfo`, not the task's CPU limit. On a 4 vCPU task it can report 16, 32
or more depending on the underlying instance. The app would fork that many R
processes into a container that cannot schedule them, thrashing or being
OOM-killed — and the number would vary between task placements, making it
intermittent.

## Decision

The task definition sets `SHINY_CPU_WORKERS` to
`floor(cpu / 1024) - 1`, reserving one core for the Shiny process. Apps read it:

```r
n_workers <- as.integer(Sys.getenv("SHINY_CPU_WORKERS", unset = "1"))
cl <- makeCluster(max(1L, n_workers))
```

Two related environment variables follow the same pattern:
`DEBUG_RUNMODEL=FALSE`, because the model's debug path uses `<<-` global
assignments that are never collected and a scaled-to-zero task may serve several
sessions before draining; and `R_MAX_VSIZE` at 80% of task memory.

## Consequences

Worker count matches the task's actual allocation, and changing task size in
tfvars automatically changes it.

Requires an R code change in each app — Terraform cannot enforce this. An app
that ignores the variable will appear to work at small task sizes and fail
unpredictably at large ones.

The apps should also cap user-supplied iteration counts. Nothing currently stops
a user requesting a million patients and OOM-killing a 16 GB task.
