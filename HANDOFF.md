# Handoff — pick up here

Written at the end of a chat session that built and deployed this platform.
Everything below is pending work that has been **decided but not applied to the
local files**. The reference implementations exist in the downloaded bundles;
the working tree does not yet reflect them.

Work top to bottom. Task 0 first — it is a real hazard.

---

## Task 0 — consolidate the Terraform state directories

**This is a hazard, not a tidiness issue.**

The platform stack was applied from `C:\Users\JakePistotnik\Downloads\platform`.
The dashboard stack was applied from
`C:\Users\JakePistotnik\Desktop\r-shiny-hosting\dashboard`.

So `platform/terraform.tfstate` — the only record of the VPC, ALB, certificate,
Cognito pool, ECS cluster and 20 SSM parameters — is sitting in a Downloads
folder, separate from the rest of the project. If that gets cleaned up, those
resources keep running and billing with no way to destroy them cleanly.

Do this:

1. Confirm which directory actually holds state:
   ```powershell
   Get-ChildItem C:\Users\JakePistotnik\Downloads\platform\terraform.tfstate
   Get-ChildItem C:\Users\JakePistotnik\Desktop\r-shiny-hosting\platform\*.tfstate -ErrorAction SilentlyContinue
   ```
2. Move the whole `Downloads\platform` directory into
   `Desktop\r-shiny-hosting\platform`, including `.terraform/`,
   `terraform.tfstate`, `terraform.tfvars` and `.terraform.lock.hcl`.
3. If a `platform` directory already exists on the Desktop, compare carefully —
   the one with `terraform.tfstate` is authoritative. Do not overwrite it.
4. Verify:
   ```powershell
   cd C:\Users\JakePistotnik\Desktop\r-shiny-hosting\platform
   terraform state list | Where-Object { $_ -notlike "data.*" } | Measure-Object -Line
   ```
   Expect **53**. If you get an empty list, you moved the wrong directory.

Do not run `terraform apply` anywhere until this returns 53.

Related but lower priority: `docs/adr/0009-remote-state.md` moves state to S3,
which solves this permanently. Worth doing soon.

---

## Task 1 — verify current deployed state

Confirm reality matches `docs/STATUS.md` before changing anything.

```powershell
# Platform
cd platform
terraform state list | Where-Object { $_ -notlike "data.*" } | Measure-Object -Line   # 53

# SSM contract — note --max-items, the CLI paginates and lies without it
$p = aws ssm get-parameters-by-path --path "/shiny/platform" --max-items 100 --output json | ConvertFrom-Json
$p.Parameters.Count                                                                    # 20

# Dashboard
cd ..\dashboard
terraform state list | Where-Object { $_ -notlike "data.*" } | Measure-Object -Line     # 22
terraform output -raw url                                                               # https://dashboard.tools.stratevi.com

# Is the task actually running? The image was pushed but never confirmed to pull.
aws ecs describe-services --cluster shiny-cluster --services shiny-dashboard --query "services[0].[status,desiredCount,runningCount]" --output text
```

If `runningCount` is 0 with `desiredCount` 1, check why:

```powershell
aws ecs describe-services --cluster shiny-cluster --services shiny-dashboard --query "services[0].events[:3].message" --output text
aws logs tail /ecs/shiny/dashboard --since 15m
```

Report findings before proceeding.

---

## Task 2 — apply the Hub Cognito pool change

Implements `docs/adr/0007-reuse-hub-cognito-pool.md`. Full instructions with
exact code are in `CHANGES-hub-pool-and-allowlist.md`, steps 1–5.

Summary: stop creating our own Cognito pool, reference the existing Assembled
Hub pool `us-east-1_6vtAiYEpv` instead, and destroy the one this stack created.

**Time-sensitive.** Safe only because nobody has signed in to the new pool. If
`aws cognito-idp list-users --user-pool-id us-east-1_AZmmbFBy0` returns any
users, stop and ask — this becomes a migration rather than a swap.

Files: `platform/cognito.tf` (full replacement), `platform/ssm.tf` (four
exports), `platform/terraform.tfvars` (two additions), `dashboard/cognito.tf`
(drop the `"none"` sentinel).

First get the real values — do not guess:

```powershell
aws cognito-idp describe-user-pool --user-pool-id us-east-1_6vtAiYEpv --query "UserPool.Domain" --output text
aws cognito-idp list-identity-providers --user-pool-id us-east-1_6vtAiYEpv --query "Providers[].ProviderName" --output table
```

Expected plan: **4 to destroy** (pool, domain, two groups) plus 4 SSM parameters
changing. Anything outside Cognito and SSM in that plan means stop.

---

## Task 3 — apply the per-app allowlist

Implements the first half of `docs/adr/0008-authorization-strategy.md`.
`CHANGES-hub-pool-and-allowlist.md` steps 6–9.

The app code is already written in the `dashboard-app` bundle — `access.R` is
new, and `app.R` has four small edits. Copy both in rather than re-deriving
them, then confirm the edits are present:

```powershell
Select-String -Path dashboard-app\app.R -Pattern "source\(.access.R.\)|access_gate\(main_ui\)|library\(jsonlite\)"
Select-String -Path dashboard-app\Dockerfile -Pattern "access.R|jsonlite"
```

Terraform side, all in `dashboard/`: four new variables, five new environment
variables in the task definition, and the allowlist in tfvars:

```hcl
access_mode = "emails"
allowed_emails = [
  "jake@stratevi.com",
  "nick@stratevi.com",
  "yi@stratevi.com",
  "josh@stratevi.com",
]
access_contact = "jake@stratevi.com"
```

Then rebuild, push, apply, and force a new deployment. The plan will show a new
task definition revision — environment variables live there.

**Mirror every Terraform change into `model/`.** It is the same code and will
need the same variables.

---

## Task 4 — first sign-in and the sleep test

```powershell
cd platform
$poolId = terraform output -raw cognito_user_pool_id
aws cognito-idp admin-create-user --user-pool-id $poolId --username jake@stratevi.com --user-attributes Name=email,Value=jake@stratevi.com Name=email_verified,Value=true
```

Then visit `https://dashboard.tools.stratevi.com`. Expected: Cognito login →
holding page → ~45 seconds → the Sankey.

**Then the test that actually matters.** The scale-to-zero cycle has never been
observed, and the entire cost model rests on it.

The dashboard's warm window is weekdays 12:00–01:00 UTC, so it will not sleep
during US working hours. Either test after 8pm Eastern, or temporarily set
`warm_enabled = false` and re-apply.

Watch `HealthyHostCount` on the `shiny-dashboard-runtime` CloudWatch dashboard
fall to 0 about 20 minutes after closing the browser, then wake on the next
visit.

```powershell
aws logs tail /aws/lambda/shiny-dashboard-sleeper --since 1h
```

Each run logs one of `warm`, `already-asleep`, `too-young`, `active`, `slept`.
`active` with nobody using the app means something is polling the URL.

---

## Task 5 — foundations

From `docs/ROADMAP.md` step 2. Cheap now, tedious at fifteen apps.

- Terraform state to S3 — `docs/adr/0009-remote-state.md`
- Per-app IAM task roles — `docs/adr/0010-per-app-iam-roles.md`. Currently one
  shared `shiny-task` role; any app can do what every other app can do.
- ALB access logs to S3

---

## Not yet started

The **model stack** needs the full app directory — `ui.R`, `global.R`,
`Rcode_Packages.R`, `Rcode_HelperFunctions.R`, `Images/`, `www/` — which has not
been supplied. It also needs three R changes before it will work on Fargate:
`SHINY_CPU_WORKERS` instead of `detectCores()`, `DEBUG_RUNMODEL` from the
environment instead of hardcoded `TRUE`, and a cap on the user-supplied patient
count so nobody can OOM a 16 GB task.

---

## How to ask for help

Two things worth flagging back rather than guessing at:

- Any plan that destroys something not explicitly expected
- Any Cognito change once users exist in a pool

Everything else in this document has already been argued through and recorded in
`docs/adr/`.
