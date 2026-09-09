# Spinning up

High-level sequence. Detailed step-by-step with expected output lives in
`RUNBOOK.md` at the repository root.

## Order matters

```
DNS delegation  →  platform  →  [dashboard]  →  [model]
   (IT ticket)      (once)        (per app, any order)
```

The platform owns the ALB listener that every app stack attaches a rule to.
App stacks read everything shared from SSM at `/shiny/platform/*`, so they need
no access to the platform's Terraform state and can be applied by different
people.

## Prerequisites

- Terraform ≥ 1.6 (≥ 1.10 if using the S3 backend), AWS CLI v2, Docker Desktop
- `ShinyPlatformDeploy` IAM policy attached. Verify with
  `powershell -ExecutionPolicy Bypass -File iam/preflight.ps1` — 17/17 or stop
- A Route 53 public hosted zone, delegated from the parent domain

## 1. DNS delegation — once per domain, days of lead time

Create the hosted zone, get its four nameservers, send them to IT. The ask is
one NS record set on `stratevi.com` for the name `tools`. Cloud DNS models this
as one record set with four values, not four records — saying "four NS records"
causes confusion.

Do not proceed until this returns the AWS nameservers:

```powershell
nslookup -type=NS tools.stratevi.com
```

The certificate step will hang for 15 minutes and then fail if you skip this.

## 2. Platform — once

```powershell
cd platform
cp terraform.tfvars.example terraform.tfvars   # edit: zone id, domain, cognito prefix, email
terraform init
terraform plan -no-color -out platform.tfplan | Tee-Object -FilePath plan.txt
```

Before applying, three checks:

```powershell
Select-String -Path plan.txt -Pattern "nat_gateway|aws_instance|aws_eip|aws_db_instance"
Select-String -Path plan.txt -Pattern "^Plan:"
Select-String -Path plan.txt -Pattern "# aws_lb\."
```

First must be empty — `aws_nat_gateway` is $32.85/month and the one thing that
would quietly wreck the cost model. Second should show `0 to destroy`. Third
should show exactly one load balancer.

```powershell
terraform apply platform.tfplan
```

Expect a 2–5 minute pause on `aws_acm_certificate_validation` and about three
minutes on the ALB. Then confirm the SSM contract is complete:

```powershell
$p = aws ssm get-parameters-by-path --path "/shiny/platform" --max-items 100 --output json | ConvertFrom-Json
$p.Parameters.Count      # expect 20
```

`--max-items` matters. Without it the CLI paginates and `--query length()`
returns one count per page. See [GOTCHAS.md](GOTCHAS.md).

Smoke test — a clean 404 proves TLS, DNS and the ALB all work:

```powershell
curl.exe -sI https://dashboard.tools.stratevi.com
```

## 3. Build and test the image locally — before any push

```powershell
cd dashboard-app
docker build --platform linux/amd64 -t tarpeyo-dashboard .
docker run --rm -p 3838:3838 tarpeyo-dashboard
```

`--platform linux/amd64` is mandatory on Apple Silicon; the task definition
specifies X86_64 and an arm64 image fails at runtime with `exec format error`.

Open **http://localhost:3838** — not `0.0.0.0`, which browsers reject. Verify
three things: the app renders, devtools Network shows a `HEAD` request to
`/?heartbeat=…` roughly once a minute, and the container stays up unattended.

The heartbeat check is the one to be fussy about. Without it the deployed app
works perfectly and then drops sessions after 20 minutes of quiet use.

## 4. App stack — per app

```powershell
cd dashboard
terraform init
terraform plan -no-color -out dashboard.tfplan | Tee-Object -FilePath plan.txt
Select-String -Path plan.txt -Pattern "^Plan:"
terraform apply dashboard.tfplan
```

Costs nothing to apply — the service starts at zero tasks and the ECR
repository is empty.

`listener_rule_priority` must be unique across every app on the shared
listener. Register: dashboard 100, model 200, and each app's ECS association
rule at +700.

## 5. Push the image

```powershell
terraform output -raw docker_push_commands
```

Run the four printed commands from the app's source directory. ~1.3 GB, a few
minutes.

## 6. First user and first login

```powershell
cd ..\platform
$poolId = terraform output -raw cognito_user_pool_id
aws cognito-idp admin-create-user --user-pool-id $poolId --username you@stratevi.com --user-attributes Name=email,Value=you@stratevi.com Name=email_verified,Value=true
```

Visit the URL. Expected: Cognito login → forced password change → a "Starting…"
holding page → 45 seconds → the app. That holding page is the waker Lambda
working.

## 7. Verify the sleep cycle — do not skip

This is what the cost model rests on and it has never been observed. Set aside
45 minutes outside the app's warm window, or temporarily set
`warm_enabled = false`, and watch the `shiny-dashboard-runtime` CloudWatch
dashboard. You want `HealthyHostCount` to fall to 0 about 20 minutes after you
close the browser, and the waker to bring it back on the next visit.

```powershell
aws logs tail /aws/lambda/shiny-dashboard-sleeper --since 1h
```

Each run logs one of `warm`, `already-asleep`, `too-young`, `active`, `slept`.
Seeing `active` when nobody is using the app means something is polling the URL.
