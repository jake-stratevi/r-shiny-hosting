# Stratevi R Shiny hosting platform

R Shiny dashboards and models hosted on AWS, each in its own container, behind
one authenticated ALB, on Fargate tasks that sleep when idle. An always-on
authorizing proxy (ADR-0014) owns routing, entitlement and wake/sleep for
every migrated app, and serves the self-service portal at
`shinyplatform.tools.stratevi.com`. Fixed cost ~$29/month; everything else is
per awake-hour.

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
platform/        Shared: VPC, ALB, cert, Cognito pool, ECS cluster, IAM.
                 Deploy first.
proxy/           Authorizing proxy + portal stack: catch-all listener rule,
                 DynamoDB tables, shared Cognito client. See ADR-0014.
proxy-app/       Container source for the proxy AND the portal API.
portal-ui/       React SPA. Built, then copied into proxy-app/portal-dist
                 at image-build time -- no Node in the runtime image.
dashboard/       Tarpeyo Sankey app stack. Deployed, `proxied = true`.
                 The LAST Terraform-managed app -- everything since is
                 created through the portal, which writes no Terraform.
dashboard-app/   Container source for the dashboard.
state-backend/   Bootstraps the S3 state bucket. Local state, by necessity.
iam/             Deployment policy + preflight scripts.
docs/            ADRs, design specs, status, runbooks, gotchas.
teardown.ps1     Teardown automation.
```

The old "`dashboard/` and `model/` are byte-identical, fix both" rule is
retired: `model/` was destroyed on 2026-09-11 (that app now lives in the
portal as `microsimulation-model`), so there is no twin left to keep in sync.

## Rules

**Never run `terraform apply` without showing the plan first.** Always:

```powershell
terraform plan -no-color -out <stack>.tfplan | Tee-Object -FilePath plan.txt
Select-String -Path plan.txt -Pattern "^Plan:"
```

Then apply the saved plan file, so what runs is what was reviewed.

**Never remove these `lifecycle` blocks.** Something mutates each of these at
runtime; Terraform owns their existence, the runtime owns their state.
Removing one makes every apply fight the thing that owns it — silently, in
two of the three cases.

- `aws_lb_listener_rule.this` → `ignore_changes = [action]` (app stacks; the
  waker/sleeper Lambdas swap the target group)
- `aws_ecs_service.this` → `ignore_changes = [desired_count]` (app stacks;
  the scaler)
- `proxy/cognito.tf`'s `aws_cognito_user_pool_client.this` →
  `ignore_changes = [callback_urls, logout_urls, supported_identity_providers]`
  (**the portal adds a callback per created app**, and the identity-provider
  list is built from an SSM value another stack owns; without these an apply
  wipes every wizard-created app's sign-in, or drops the Microsoft button
  from the login page, and reports nothing wrong either way)

The proxy's own listener rule and service are the exception — nothing mutates
them, so they deliberately have NO ignore blocks. See the comments there.

**Never introduce `aws_nat_gateway`.** $32.85/month, deliberately avoided. See
`docs/adr/0004-no-nat-gateway.md`. Check every plan for it.

**`listener_rule_priority` must be unique** across all apps on the shared ALB
listener. A **proxied app has no listener rule at all** — the catch-all
serves it — so this register only matters for apps still on the legacy path.

**There are currently NO per-app rules at all.** Every app is proxied, so
the listener holds exactly two: **4900 the signed-out page** (the only rule
with no authenticate action — read the banner in `proxy/alb.tf` before
touching it) and **proxy 5000**, the `*.tools.stratevi.com` catch-all, which
must stay the highest number so any explicit rule added later still wins.

Free: 50 (the retired ADR-0013 Lambda portal), 100 (dashboard, now proxied),
200 and 900 (model, destroyed 2026-09-11). A legacy app stack, if one is ever
resurrected with `proxied = false`, puts its ECS association rule at
priority + 700.

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

**The heartbeat snippet is dead — do not add it to a new app.** It existed
because the legacy sleeper read `RequestCountPerTarget` and a Shiny websocket
generates zero HTTP requests. The proxy counts real requests and open
websockets server-side, so ADR-0006 is retired outright: as of 2026-09-11
there are no `proxied = false` apps and no waker/sleeper Lambdas anywhere.
`dashboard-app/`'s image still carries the snippet harmlessly; it goes at the
next rebuild.

**A new Cognito app client renders "Login pages unavailable"** until it gets
a managed-login branding association, which Terraform cannot create. Run
`aws cognito-idp create-managed-login-branding --user-pool-id <pool>
--client-id <client> --use-cognito-provided-values` after creating one.

**Never create a native Cognito user for an address Entra emits** — email is
the pool's username, so it collides with that person's federated identity
(`AliasExistsException`). Staff come from federation; only external clients
get hand-made accounts. See ADR-0015 and RUNBOOK.md "User administration".

Apps must read `SHINY_CPU_WORKERS` rather than calling
`parallel::detectCores()`, which reports host cores inside Fargate, not the task
limit.
