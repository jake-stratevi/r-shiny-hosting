# Treatment Pathway Dashboard — app stack

Self-contained deployment for one Shiny app. Requires the **platform** stack to
be applied first; everything shared is read from SSM at
`/<project>/platform/*`, so this stack needs no access to the platform's
Terraform state.

```
Route 53 ─► shared ALB ─► authenticate-cognito ─► forward
                                                    │
                           ┌────────────────────────┴────────────────────────┐
                           │                                                 │
                    waker Lambda TG                                   ECS Fargate TG
                 (while scaled to zero)                           (while a task is awake)
                           │                                                 │
                 starts the ECS service                              the actual Shiny app
                 and flips the rule ──────────────────────────────────────►  here
```

A sleeper Lambda runs every 5 minutes, scales the app back to zero when idle,
and flips the rule back to the waker.

## What's in each file

| File | Contents |
|---|---|
| `data.tf` | Reads shared wiring from the platform's SSM parameters |
| `ecr.tf` | This app's image repository |
| `cognito.tf` | This app's client on the shared user pool |
| `alb.tf` | Two target groups and the authenticated listener rule |
| `ecs.tf` | Log group, task definition, service at `desired_count = 0` |
| `lambda.tf` | Waker, sleeper, EventBridge schedule |
| `dns.tf` | Route 53 alias record |
| `monitoring.tf` | Stuck-task alarm and runtime dashboard |
| `lambda/waker/index.py` | Holding page; starts the task, flips the rule |
| `lambda/sleeper/index.py` | Idle scale-down and warm window |

## Cost

Task size **0.5 vCPU / 2 GB** = **$0.0291 per awake hour**.

| Scenario | Monthly |
|---|---|
| If it never slept (730 hrs) | $21.27 |
| Expected with these settings | ~$7.70 (weekday warm window, ~264 hrs) |

Plus a share of the platform's ~$20/month fixed floor, which you pay once
across all apps.

`terraform output cost_per_awake_hour_usd` prints the rate for whatever sizing
you set. The CloudWatch dashboard shows awake hours; multiply.

This app is cheap enough that the warm window is worth it: keeping it up on
weekdays costs about $6 more per month than pure scale-to-zero and removes the
60-90 second cold start from every first visit of the day. That matters if it
is ever opened in front of a client. Set `warm_enabled = false` to trade the
convenience back for the money.

## Required app changes

Terraform can't do these for you. All three are load-bearing.

### 1. Heartbeat — required, or the sleeper will kill users mid-session

Idle detection reads `RequestCountPerTarget`. A Shiny session with an open
websocket generates **zero** HTTP requests, so without a heartbeat an active
user looks idle. Add to the app's UI:

```r
tags$head(tags$script(HTML("
  setInterval(function () {
    fetch(window.location.pathname + '?heartbeat=' + Date.now(),
          { method: 'HEAD', cache: 'no-store' });
  }, 60000);
")))
```

One request per minute per open session. Negligible LCU cost, and it turns
"is anyone actually using this" into a metric you can read.

### 2. Worker count — required, or the task will thrash

`parallel::detectCores()` reads the host's `/proc/cpuinfo`, not the Fargate
task limit. On a 4 vCPU task it can report 16, 32 or more, and
`makeCluster(max(1, detectCores() - 2))` will spawn that many R processes
inside a container that cannot feed them. Use the env var the task definition
sets:

```r
n_workers <- as.integer(Sys.getenv("SHINY_CPU_WORKERS", unset = "1"))
cl <- makeCluster(max(1L, n_workers))
```

### 3. Debug flag

`debug_runmodel <- TRUE` prints inputs and does `<<-` global assignments that
are never collected. With scale-to-zero one task may serve several sessions
before draining. Read it from the environment:

```r
debug_runmodel <- toupper(Sys.getenv("DEBUG_RUNMODEL", "FALSE")) == "TRUE"
```

## Deploy

```bash
terraform init
terraform apply                    # service comes up at 0 tasks; no compute billing yet
terraform output docker_push_commands   # build, push, force a new deployment
terraform output url
```

First visit shows the holding page for 60-90 seconds, then the app.

## Things worth knowing

**Terraform and the Lambdas co-own two attributes.** The listener rule's
`action` and the ECS service's `desired_count` both carry
`ignore_changes`, because the waker and sleeper mutate them at runtime.
Terraform owns whether the resources exist and how they're configured; the
Lambdas own their current state. Remove those lifecycle blocks and every
`apply` fights the scaler.

**Cold start is your image size.** A `rocker/r-ver` image with renv-pinned
packages lands around 2-3 GB, which is roughly 60-90 seconds of pull plus R
startup. Prune build dependencies with a multi-stage Dockerfile, or look at
Fargate SOCI lazy loading.

**Auth sits in front of the waker on purpose.** An unauthenticated visitor
can't trigger a task to start, which closes the obvious cost-abuse hole.

**`listener_rule_priority` must be unique** across every app on the shared
ALB. The register lives in the platform stack's README.
