# Stratevi R Shiny Hosting Platform — documentation

Everything about how the platform is built, why it's built that way, what state
it's in, and how to stand it up or tear it down.

| Document | Read it when |
|---|---|
| [STATUS.md](STATUS.md) | You want to know what actually exists right now |
| [SPINUP.md](SPINUP.md) | Building the infrastructure from nothing |
| [TEARDOWN.md](TEARDOWN.md) | Removing it, in whole or in part |
| [GOTCHAS.md](GOTCHAS.md) | Something failed and you want to know if we've seen it |
| [ROADMAP.md](ROADMAP.md) | Deciding what to work on next |
| [adr/](adr/README.md) | You want to know *why* a decision was made |

## What this is

Two R Shiny applications — a Plotly treatment-pathway dashboard and a
patient-level microsimulation model — hosted on AWS behind a single
authenticated load balancer, with per-app Fargate tasks that sleep when nobody
is using them.

The target cost is roughly **$35–45/month for both apps**, against ~$210/month
for equivalent always-on EC2 instances and ~$599/month for shinyapps.io
Professional.

## Account facts

| | |
|---|---|
| AWS account | `652063276768` |
| Region | `us-east-1` |
| Domain | `tools.stratevi.com` (Route 53 zone `Z07112442ZAIA7CFJKV72`) |
| Parent DNS | `stratevi.com`, Google Cloud DNS, managed by IT |
| Terraform project prefix | `shiny` |

## Repository layout

```
platform/        Shared infrastructure. Deploy once, first.
dashboard/       Tarpeyo Sankey dashboard app stack.
model/           Microsimulation app stack. Not yet deployed.
dashboard-app/   Container source: Dockerfile, app.R, data.
iam/             Deployment IAM policy and preflight checks.
docs/            This directory.
teardown.ps1     Teardown automation.
```
