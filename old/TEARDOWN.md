# Tearing down

Three levels, from "stop the bleeding" to "remove everything".

## Level 1 — stop compute, keep infrastructure

The emergency brake. Disables the sleeper schedules and scales every service to
zero. Fargate billing stops within a minute; the ALB keeps costing ~$16.43/month.

```powershell
.\teardown.ps1 -StopCompute
```

Reversible with an `aws events enable-rule` and a page load. This is the right
response to "why is this costing money", which almost never calls for a full
teardown.

## Level 2 — remove one app

```powershell
cd dashboard
terraform destroy
```

Two things will block it, both handled automatically by `teardown.ps1
-Destroy`. If destroying by hand, do them first:

```powershell
# 1. The sleeper will scale the service back up mid-destroy inside a warm window
aws events disable-rule --name shiny-dashboard-sleeper --region us-east-1

# 2. ECR is force_delete = false, so a repository holding images refuses to delete
aws ecr list-images --repository-name shiny-dashboard --region us-east-1 --query imageIds --output json > imgs.json
aws ecr batch-delete-image --repository-name shiny-dashboard --region us-east-1 --image-ids file://imgs.json
```

The platform is untouched and other apps keep working.

## Level 3 — remove everything

```powershell
.\teardown.ps1 -Check      # inventory first, destroys nothing
.\teardown.ps1 -Destroy    # prompts for the word DESTROY
```

The script disables scalers, drains tasks, purges ECR, then destroys app stacks
before the platform. **It refuses to touch the platform if any app stack fails**
— that's deliberate, because app listener rules attach to the platform's
listener and destroying it first orphans them.

Doing it by hand is the same order:

```
dashboard → model → platform
```

## What teardown deliberately leaves behind

| | Why | To remove |
|---|---|---|
| Route 53 hosted zone | Created by hand, not by Terraform | `-DeleteHostedZone -HostedZoneId Z07112442ZAIA7CFJKV72` |
| NS records on `stratevi.com` | Owned by IT | Ask IT |
| `ShinyPlatformDeploy` IAM policy | You need it to run the teardown | IAM console |
| Assembled Hub Cognito pool | Not ours — shared with other apps | Never |
| Local `.tfstate` files | Your only record of what existed | Delete manually |

## Confirm the money actually stopped

24 hours later: **Billing → Cost Explorer → filter Tag `Project = shiny`**.
Should be trending to zero. Anything still accruing means an orphaned resource
the destroy missed — most likely an ENI holding a security group, or a log group.

```powershell
.\teardown.ps1 -Check
```

will tell you what's left.
