# Current state

Last updated: 8 September 2026.

Read this before touching anything. It records what is actually deployed, what
is half-done, and what has never been tested.

## Deployed and verified

**Phase 1 — DNS delegation. Complete.**
`tools.stratevi.com` is delegated from Google Cloud DNS to Route 53 zone
`Z07112442ZAIA7CFJKV72` via four NS records added by IT. Proven working: the
ACM DNS validation completed in 46 seconds during the platform apply, which
only happens if the delegation chain resolves end to end.

**IAM. Complete.**
`ShinyPlatformDeploy` customer-managed policy attached to
`arn:aws:iam::652063276768:user/Stratevi_Testing`. All 17 preflight checks pass.

**Platform stack. Applied, 53 resources.**

| Resource | Identifier |
|---|---|
| VPC | `vpc-054c7e1c8535ba747` (10.40.0.0/16) |
| Public subnets | `subnet-03032650cb0d36d9c` (1a), `subnet-0d8046451b51463cc` (1b) |
| ALB | `shiny-alb`, suffix `app/shiny-alb/4c68c1633e2eebc5` |
| Wildcard cert | `*.tools.stratevi.com`, ACM `5d882983-…` |
| HTTPS listener | Default action: 404 fixed response |
| ECS cluster | `shiny-cluster`, FARGATE capacity provider |
| IAM roles | `shiny-task-execution`, `shiny-task`, `shiny-scaler` |
| SSM exports | 20 parameters under `/shiny/platform/` |
| Cognito pool | `us-east-1_AZmmbFBy0`, domain `stratevi-shiny-auth` |
| Budget | `shiny-monthly`, $75, alerts to jake@stratevi.com |

**Billing from this point: ~$20/month** for the ALB, regardless of app activity.

**Container image. Built and tested locally.**
`tarpeyo-dashboard`, ~1.3 GB, from `rocker/r-ver:4.4.1` with a pinned P3M
snapshot. Verified locally: Sankey renders, filters respond, heartbeat HEAD
request fires once per minute, container stays up unattended.

## Applied but unverified

**Dashboard stack.** The final `terraform apply` of 8 resources was prepared and
approved but its completion was never confirmed in the session. Verify before
assuming:

```powershell
cd dashboard
terraform state list | Measure-Object -Line     # expect 21
terraform output -raw url                        # expect https://dashboard.tools.stratevi.com
```

If the ECS service is missing, re-run `terraform plan` and apply.

## Not started

- **Image never pushed to ECR.** The repository `shiny-dashboard` exists and is
  empty.
- **No Cognito users exist.** Nobody has ever signed in.
- **The URL has never been visited.** The waker Lambda has never run in anger.
- **The sleep cycle has never been observed.** This is the single most important
  untested thing — it is what the entire cost model depends on. See
  [ROADMAP.md](ROADMAP.md) step 1.
- **Entra federation is not wired up.** `oidc_provider_name` in SSM is the
  sentinel value `none`.
- **The model stack has not been deployed.** It needs the full app directory
  (`ui.R`, `global.R`, `Rcode_Packages.R`, `Rcode_HelperFunctions.R`, `Images/`,
  `www/`) which has not been supplied, plus the three R code changes in
  [ADR-0006](adr/0006-heartbeat-idle-detection.md) and
  [ADR-0011](adr/0011-fargate-cpu-detection.md).

## Known-fragile

Ordered by how likely they are to cause an incident.

1. **Idle detection depends on client-side JavaScript.** If the heartbeat is
   removed from an app's UI, or a browser blocks it, the sleeper will scale a
   task to zero underneath an active user. See
   [ADR-0006](adr/0006-heartbeat-idle-detection.md).
2. **One shared task role across all apps.** Any app can assume the permissions
   of every other app. Must be fixed before any external client publishes.
   See [ADR-0010](adr/0010-per-app-iam-roles.md).
3. **Terraform state is local**, in `Downloads` and `Desktop` directories on one
   laptop. No locking, no backup. See [ADR-0009](adr/0009-remote-state.md).
4. **Authorization is binary.** Every authenticated user can reach every app.
   See [ADR-0008](adr/0008-authorization-strategy.md).
5. **Two Cognito pools now exist** — the one this stack created and the
   Assembled Hub pool. Only one should survive. See
   [ADR-0007](adr/0007-reuse-hub-cognito-pool.md).
