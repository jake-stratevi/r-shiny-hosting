# Tarpeyo dashboard — container image

Everything the `dashboard` Terraform stack expects to find in ECR.

| File | Notes |
|---|---|
| `Dockerfile` | rocker/r-ver:4.4.1 + 7 packages from a pinned P3M snapshot |
| `app.R` | Your app, with the required heartbeat block added to `fluidPage` |
| `Tx_sequence_after_2022_tarpeyo.xlsx` | Baked in; read at startup |

## The one change made to app.R

A `tags$head(tags$script(...))` block was inserted at the top of `fluidPage`.
It fires a HEAD request once a minute per open session.

This is not cosmetic. The sleeper Lambda decides whether anyone is using the
app by reading the ALB's `RequestCountPerTarget` metric. A Shiny session holds
an open websocket and generates **zero** HTTP requests while someone sits on
the page, so without the heartbeat an active user looks idle and the app gets
scaled to zero underneath them. It is harmless when you run the app locally.

Nothing else in `app.R` was touched.

## Test locally before you push

```bash
docker build --platform linux/amd64 -t tarpeyo-dashboard .
docker run --rm -p 3838:3838 tarpeyo-dashboard
# open http://localhost:3838
```

Confirm the Sankey renders and, in the browser devtools Network tab, that a
HEAD request fires about once a minute. If it doesn't, the deployment will
appear to work and then drop sessions after ~20 minutes.

`--platform linux/amd64` matters on an Apple Silicon Mac. The task definition
sets `cpu_architecture = "X86_64"`; an arm64 image will pull fine and then fail
to start with an exec format error.

## Push

The exact commands, with your account ID and repository URL filled in, come
from the Terraform stack:

```bash
cd ../dashboard      # the Terraform stack
terraform output docker_push_commands
```

It prints login, build, push, and the `force-new-deployment` that makes ECS
pick up the new image.

## Image size and cold start

This image lands around 1.2-1.5 GB, which is roughly 30-45 seconds of pull plus
R startup. With `warm_enabled = true` on weekdays, nobody sees that during
working hours; the first visitor outside the window will.
