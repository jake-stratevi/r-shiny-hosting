# Stratevi R Shiny hosting platform

Two R Shiny apps hosted on AWS behind one authenticated ALB, on Fargate tasks
that sleep when idle. Target cost ~$35–45/month for both.

Full documentation is in `docs/`. Read `docs/STATUS.md` before changing
anything, and `docs/adr/` before questioning a design decision — most of them
have already been argued out and the alternatives are recorded.

## Account facts

| | |
|---|---|
| AWS account | `652063276768` |
| Region | `us-east-1` |
| Domain | `tools.stratevi.com`, Route 53 zone `Z07112442ZAIA7CFJKV72` |
| Terraform prefix | `shiny` (must match across all stacks) |
| Shell | PowerShell on Windows |

## Layout

```
platform/        Shared: VPC, ALB, cert, Cognito, ECS cluster, IAM. Deploy first.
dashboard/       Tarpeyo Sankey app stack. Deployed.
model/           Microsimulation app stack. Deployed, but its ECR repo is
                 empty -- the URL is a dead link until an image is pushed.
portal/          Landing page at dashboards.tools.stratevi.com. See ADR-0013.
dashboard-app/   Container source for the dashboard.
iam/             Deployment policy + preflight scripts.
docs/            ADRs, status, runbooks, gotchas.
catalog.yaml     What the portal shows and to whom. Keep in sync with each
                 app's own terraform.tfvars allowed_emails -- see ADR-0013.
teardown.ps1     Teardown automation.
```

`dashboard/` and `model/` are byte-identical Terraform with different tfvars.
**A fix in one must be applied to the other.** This has been missed four times.

## Rules

**Never run `terraform apply` without showing the plan first.** Always:

```powershell
terraform plan -no-color -out <stack>.tfplan | Tee-Object -FilePath plan.txt
Select-String -Path plan.txt -Pattern "^Plan:"
```

Then apply the saved plan file, so what runs is what was reviewed.

**Never remove these `lifecycle` blocks.** The waker and sleeper Lambdas mutate
both at runtime; Terraform owns their existence, the Lambdas own their state.
Removing either makes every apply fight the scaler.

- `aws_lb_listener_rule.this` → `ignore_changes = [action]`
- `aws_ecs_service.this` → `ignore_changes = [desired_count]`

**Never introduce `aws_nat_gateway`.** $32.85/month, deliberately avoided. See
`docs/adr/0004-no-nat-gateway.md`. Check every plan for it.

**`listener_rule_priority` must be unique** across all apps on the shared ALB
listener. Register: portal 50, dashboard 100, model 200, ECS association
rules at +700 (portal has no ECS association rule -- it's Lambda-only),
**proxy 5000** (the `*.tools.stratevi.com` catch-all -- must stay the highest
number so every explicit app rule wins until that app is migrated to the
proxy; see docs/design/proxy.md).

**Terraform escaping:** `$${` produces a literal `${`, not a literal `$`. Use
`format()` when you need a dollar sign in a string. This has caused two bugs.

**PowerShell:** `-out=file` splits on the equals sign — use `-out file`. Native
stderr redirected with `2>&1` becomes ErrorRecord objects, not strings — shell
it out to `cmd /c "… 2>&1"`. `aws ecr get-login-password | docker login` fails
with a 400 because of pipe encoding — assign to a variable first.

See `docs/GOTCHAS.md` for the rest. Every entry there cost an hour to find.

## Cost model

Fixed ~$20/month for the ALB, which never sleeps. Everything else is per
awake-hour: dashboard $0.0291 (0.5 vCPU / 2 GB), model $0.2330 (4 vCPU / 16 GB).

Fargate bills per second, so a larger task that finishes a CPU-bound run faster
costs about the same. Size up rather than down for the model.

## Things that will break if you forget them

Each app's UI must contain the heartbeat snippet. Idle detection reads
`RequestCountPerTarget`, and a Shiny websocket generates zero HTTP requests — so
without it the sleeper scales a task to zero underneath an active user.

Apps must read `SHINY_CPU_WORKERS` rather than calling
`parallel::detectCores()`, which reports host cores inside Fargate, not the task
limit.
