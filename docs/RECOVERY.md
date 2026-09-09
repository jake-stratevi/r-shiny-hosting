# Recovery findings — r-shiny-hosting

Written 2026-09-08. This is a findings document, not a change log — nothing in
`platform/`, `dashboard/`, `model/`, or AWS was touched to produce it. Where a
step needed live AWS access this session couldn't reach, that's called out
explicitly rather than guessed at.

**Bottom line up front:** the state is not lost, and it's now backed by a live
AWS check, not just internal consistency. `old/platform/terraform.tfstate` and
`old/dashboard/terraform.tfstate` are current and match the resource counts
and identifiers `docs/STATUS.md` already documents. The live enumeration
(section 3–4, run 2026-09-08 21:41 UTC) found **zero resources in AWS that
aren't already accounted for in one of these two state files** — nothing
orphaned, nothing to import. It also caught the dashboard's ECS service
mid-recovery: after 18 failed task placements over roughly 45 minutes
(`CannotPullContainerError` — the image hadn't been pushed yet), pushing the
image resolved it, and the service is now healthy at `runningCount: 1`.
Recommendation is Option A — recover and consolidate the existing state — at
the bottom, with commands.

---

## 0. A hazard HANDOFF.md flagged that turned out to be already resolved

`HANDOFF.md` Task 0 warns that the platform state was applied from
`C:\Users\JakePistotnik\Downloads\platform`, separate from the project, and
that this is "a hazard, not a tidiness issue." Since that's a location outside
`r-shiny-hosting` and outside `old/`, it was checked directly: `Downloads\platform`
still exists, and still holds a full copy of the platform stack, including
`terraform.tfstate` (114,144 bytes) and `terraform.tfstate.backup` (108,675
bytes).

Both files are **byte-identical** (MD5-matched) to the copies now sitting in
`old\platform\`. So the consolidation Task 0 asked for did happen at some
point — the state is not stranded — but nobody deleted the Downloads copy
afterward, so two live copies of the source of truth still exist. That's the
same hazard in miniature: if someone edits one copy later without the other,
you get a state fork. See the cleanup step in the recommendation.

`Downloads\dashboard` also still exists, but holds no `.tfstate` at all — just
an earlier, pre-bugfix copy of the dashboard Terraform (confirmed below, item
2). It was never applied from there and carries no state risk, only clutter.

---

## 1. Salvage inventory of `old/`

| Path | Size | Last modified (UTC) | Terraform serial | Managed resources |
|---|---|---|---|---|
| `old/platform/terraform.tfstate` | 114,144 B | 2026-09-08 18:29:15 | 57 | **53** |
| `old/platform/terraform.tfstate.backup` | 108,675 B | 2026-09-08 18:29:15 | 55 | 52 |
| `old/platform/terraform.tfvars` | 655 B | 2026-09-04 20:34:59 | — | — |
| `old/dashboard/terraform.tfstate` | 86,333 B | 2026-09-08 19:18:28 | 25 | **22** |
| `old/dashboard/terraform.tfstate.backup` | 54,020 B | 2026-09-08 19:18:13 | 16 | 14 |
| `old/dashboard/terraform.tfvars` | 705 B | 2026-09-01 20:41:19 | — | — |

No `old/model` directory exists. That's consistent, not a gap: `CLAUDE.md` and
`docs/STATUS.md` both say the model stack has never been deployed, so there is
no model state to salvage — there was never one to lose.

Both current state files land exactly on the resource counts `docs/HANDOFF.md`
and `docs/STATUS.md` say to expect (platform 53, dashboard "expect 21/22" —
current dashboard state has exactly 22). Both backups are one apply behind by
precisely the delta the docs describe: dashboard's backup has 14 resources,
current has 22 — an 8-resource gain, matching `docs/STATUS.md`'s note that
"the final `terraform apply` of 8 resources was prepared and approved but its
completion was never confirmed in the session." **It completed.** Platform's
backup (52) to current (53) is a 1-resource gain, consistent with a small
follow-up fix rather than a large pending change.

Both current files carry `terraform_version = "1.15.8"` and internally
consistent `lineage` IDs matched to their own backups (`platform`:
`07138530-2f2f-ed25-5816-311e95742980`; `dashboard`:
`cac00693-fd45-dee7-ad2b-9971957330e2`) — no evidence of a state fork or a
mixed-up backup from a different stack.

The stray `Downloads\platform` copy (item 0) duplicates the platform
tfstate/backup/tfvars exactly and is not counted twice above.

---

## 2. Config diff — `old/<stack>/` vs `<stack>/`

### platform

| File | Verdict |
|---|---|
| `alb.tf`, `ecs.tf`, `iam.tf`, `locals.tf`, `monitoring.tf`, `network.tf`, `versions.tf` | Byte-identical |
| `cognito.tf` | **Rewritten** — old creates its own `aws_cognito_user_pool` + domain + identity provider + 2 groups; new deletes all of that and instead declares `cognito_user_pool_id` / `cognito_hosted_ui_domain` / `cognito_staff_idp_name` variables and constructs the pool ARN from them. Matches `CHANGES-hub-pool-and-allowlist.md` step 1 exactly. |
| `outputs.tf`, `ssm.tf` | Updated to read from the new variables instead of the now-deleted pool resource. Matches steps 2–3. |
| `variables.tf` | Old's `cognito_domain_prefix`, `admin_create_users_only`, `oidc_provider` variables removed (no longer needed once the pool isn't created here). |
| `terraform.tfvars.example` | Updated to show the two new Cognito variables with the `aws cognito-idp describe-user-pool` / `list-identity-providers` commands to source real values, exactly as `CHANGES-hub-pool-and-allowlist.md` step 3 instructs. |

**Confirmed: the Cognito Hub-pool swap (ADR-0007) is fully present in the new
copy**, matching the change doc line for line.

### dashboard

| File | Verdict |
|---|---|
| `data.tf`, `dns.tf`, `ecr.tf`, `lambda.tf`, `monitoring.tf` | Byte-identical |
| `alb.tf` | Comment rewording only (the `ecs_association` no-op listener rule explanation) plus a trailing-newline fix. No logic change. |
| `cognito.tf` | Old uses a `compact([...])` + `"none"`-sentinel check for the OIDC provider; new drops the sentinel entirely (`["COGNITO", data.aws_ssm_parameter.oidc_provider_name.value]`). Matches step 5 — this only makes sense *given* the platform swap above, since the sentinel exists only because the old platform could export `"none"`. |
| `ecs.tf` | **Added**: `ACCESS_MODE`, `ALLOWED_EMAILS`, `ALLOWED_GROUPS`, `ACCESS_CONTACT`, `APP_LABEL` environment variables in the task definition. Matches step 7. |
| `variables.tf` | **Added**: `access_mode` (with validation), `allowed_emails`, `allowed_groups`, `access_contact`. Matches step 6. |
| `outputs.tf` | Whitespace/alignment only. |
| `terraform.tfvars` (new tree) | **Already includes** `access_mode = "emails"` and the 4-address allowlist — this file in the new tree is *ahead* of `old/dashboard/terraform.tfvars`, not behind it. See the migration note in section 6. |

**Confirmed: the per-app allowlist (ADR-0008, first half) is fully present in
the new copy.**

### model

There is no `old/model` to diff against, so `model/*.tf` was instead diffed
against the new `dashboard/*.tf` (the two are supposed to be "byte-identical
Terraform with different tfvars" per `CLAUDE.md` and ADR-0003). Result:
**every `.tf` file is byte-identical** except `versions.tf`, where the only
difference is a comment naming the future remote-state key
(`shiny/model.tfstate` vs `shiny/dashboard.tfstate`) — inert, not live config.

**Confirmed: model has every fix dashboard has**, including the allowlist
plumbing and the ECS-association fix, because it's a literal copy of the
already-fixed file. `model/terraform.tfvars` also already carries its own
`access_mode`/`allowed_emails` block (Jake-only, as expected for a
not-yet-deployed app).

### The four Terraform escaping/dependency bugs from `docs/GOTCHAS.md`

Checked all four explicitly. All four are present and correct in **both**
`old/` and the new copy — they predate the redownload rather than being part
of the old-vs-new diff, which makes sense: `docs/GOTCHAS.md` says each "cost
real time during the initial build," i.e., they were fixed *during* the
session that produced `old/`, not afterward.

1. Budget cost filter `$${` → both use `format("user:Project$%s", var.project)` correctly.
2. CloudWatch dashboard title `$${format(...)}` → both use `format(...)` correctly in `dashboard/monitoring.tf` (and by inheritance, `model/monitoring.tf`). The **stray `Downloads\dashboard\monitoring.tf`** still has the broken `"...$${format(...)}..."` form — direct proof that folder is the pre-fix scaffold, corroborating item 0.
3. ECS "no associated load balancer" fix (`aws_lb_listener_rule.ecs_association`) → present in both `old/dashboard/alb.tf` and new.
4. Stranded-dependents fix (`depends_on = [aws_lb_listener_rule.ecs_association]` on the ECS service) → present in both.

Nothing found in `old/` that's missing from the new copy and looks deliberate
rather than stale — the only content removed going from old to new is the
self-hosted Cognito pool resources, and that removal is the documented,
intentional point of ADR-0007.

---

## 3–4. AWS ground truth and reconciliation

Confirmed live against account `652063276768`, `us-east-1`, at
2026-09-08T21:41:49Z via `scripts/aws-ground-truth.ps1` run from your machine
(IAM identity `arn:aws:iam::652063276768:user/Stratevi_Testing`). Table is
every category item 3 asked for, cross-checked against the state inventory in
section 1.

| Category | Live AWS | State says | Verdict |
|---|---|---|---|
| ALB | 1 — `shiny-alb`, `app/shiny-alb/4c68c1633e2eebc5`, `active`, created 2026-09-08T18:15:20Z | Same ARN | Match |
| VPC | 1 — `vpc-054c7e1c8535ba747`, `10.40.0.0/16`, tagged `Project=shiny` | Same ID | Match |
| Subnets | 2 — both public, `MapPublicIpOnLaunch: true`, no NAT gateway anywhere in the account for this VPC | Same 2 IDs | Match — confirms ADR-0004 (no NAT gateway) is holding in practice, not just in config |
| Target groups | 2 — `shiny-dashboard-ecs` (ip, :3838) and `shiny-dashboard-wake` (lambda) | Same 2 | Match |
| ECS cluster | `shiny-cluster`, `ACTIVE`, 1 running task, 1 active service | Same | Match |
| ECS service | `shiny-dashboard`: `desiredCount 1`, `runningCount 1`, `pendingCount 0`, rollout `COMPLETED` | Managed, same task def | Match, and now healthy (see below) |
| ECR | 1 repo (`shiny-dashboard`), 1 tagged image (`latest`, pushed 2026-09-08T13:09:50-07:00; the other two image entries are the same push's attestation/config sub-manifests, not separate images) | Repo managed, image not tracked by Terraform (expected — images aren't TF resources) | Match, and **`docs/STATUS.md`'s "Image never pushed to ECR" item is now resolved** |
| Cognito pools in account | 3 total: `us-east-1_AZmmbFBy0` ("shiny-users", **0 users**), `us-east-1_6vtAiYEpv` (the Hub pool, **15 users**), `us-east-1_VYjjTJCRW` (unrelated — a separate "Assembled Auth" CloudFormation stack, 0 users) | Only `us-east-1_AZmmbFBy0` is Terraform-managed here | The two others are correctly out of scope: the Hub pool is meant to be referenced, not owned, and `VYjjTJCRW` belongs to a different project entirely |
| Cognito app clients | `us-east-1_AZmmbFBy0` has one client, `shiny-dashboard` (`673i1ap0gqc9kef07r1iliuri6`). The Hub pool (`6vtAiYEpv`) has two clients, neither named `shiny-*` | Client `673i1ap0gqc9kef07r1iliuri6` matches state | See the sequencing note below — this is the one place the swap has a real, visible-to-users consequence |
| Lambdas in account | 5 total; only 2 are this project's (`shiny-dashboard-waker`, `shiny-dashboard-sleeper`) | Both managed, ARNs match | Match. The other 3 (`assembled-auth-api-dev-*`, `aws-controltower-NotificationForwarder`) belong to unrelated stacks |
| SSM under `/shiny/platform` | Exactly 20 parameters | 20 `aws_ssm_parameter.export[...]` + 1 `subnet_ids` in state | Match — and `oidc_provider_name` still reads `"none"`, confirming the platform swap hasn't been applied to real infrastructure yet |
| Route 53 (`tools.stratevi.com`) | 4 records: NS, SOA, the ACM validation CNAME, and `dashboard.tools.stratevi.com` A-alias to the ALB | 2 managed (`cert_validation`, `this`) + the zone's own NS/SOA | Match, no `model` subdomain (expected, not deployed) |

**Reconciliation verdict: no orphans.** Every real resource this scan can see
in the account maps to a state entry or is an already-understood
out-of-project resource (the Hub pool and the unrelated Assembled Auth
stack). Nothing found in AWS that state doesn't know about, and nothing in
state that AWS doesn't have. That's the strongest evidence yet for section 5's
recommendation — there's nothing to reconstruct via import, because nothing
is missing.

**The ECS service's rocky start, from its event history:** the service was
created at 12:18:16-07:00 today and immediately started failing —
`CannotPullContainerError: ... shiny-dashboard:latest: not found` — retrying
roughly every 5 minutes for about 45 minutes (18 failed placements visible in
the event log) because the image genuinely hadn't been pushed yet. The moment
the image landed (13:09:49-07:00, from the `docker push` further up this
conversation), the next placement attempt succeeded: task started 13:11:01,
registered against the target group 13:11:38, steady state reached 13:12:07.
No data was lost and nothing needs cleanup from the failed attempts — ECS
doesn't leave debris from placement failures.

**Sequencing note for the Cognito swap (relevant to Task 2, not a blocker to
recovery):** the dashboard's Cognito app client (`673i1ap0gqc9kef07r1iliuri6`)
belongs to the *old* pool and doesn't exist in the Hub pool. `dashboard/cognito.tf`
creates that client from `data.aws_ssm_parameter.cognito_user_pool_id`, which
platform's `terraform apply` (Task 2) will repoint at the Hub pool — but that
change only reaches the *dashboard* stack's resources (the app client, and the
ALB's `authenticate-cognito` action in `dashboard/alb.tf`, and the two
Lambdas' `COGNITO_CONFIG` env var, all of which read the same SSM values) on
dashboard's *own* next `terraform plan`/`apply`, not automatically when
platform applies. Practically: plan and apply platform first (the ~4-destroy
change from section 2), then plan and apply dashboard as a deliberate second
step — and expect that second apply to force-replace the Cognito app client
and rotate the login flow onto the Hub pool. Anyone with the dashboard open
during that second apply will need to sign in again. Since the Hub pool
already has 15 real users from unrelated work, this is exactly the kind of
change worth doing at a quiet moment, not folded silently into "just re-apply
everything."

---

## 5. Recommendation

**Recover and consolidate the existing state. Do not re-import, and do not
delete and start clean.**

The case for starting over — "nobody has signed in, no client data, two days
old" — is real and was worth taking seriously, but it doesn't hold up against
what's actually in `old/`: both state files are *current* (not stale forks),
land on exactly the resource counts the documentation independently expects,
and required zero reconstruction to read. Recovering them is copying six
files and editing two lines of a tfvars file. Re-importing ~75 resources
means hand-writing `terraform import` blocks for every VPC, subnet, security
group rule, IAM policy, SSM parameter, ECS service, Lambda, and listener rule
in both stacks, individually verifying each one's import address against
whatever `terraform plan` says is drifted afterward — hours of fiddly, error-
prone work to reconstruct information that already exists intact. Deleting
everything and reapplying clean re-signs up for the ALB creation time, ACM
DNS validation, and Cognito/ECS bring-up that already happened once, and
reintroduces exactly the failure modes `docs/GOTCHAS.md` catalogs (the target-
group association trap, the escaping bugs) as risks to hit *again*, on config
that currently plans clean.

The one thing that make re-import or restart *tempting* — the config has
moved on from what's in state (the Cognito swap isn't applied yet) — is not a
state problem. It is pending work `HANDOFF.md` already scoped as Task 2, and
it's exactly as safe to do after consolidating state as before.

Trade-off worth naming honestly: recovering state means inheriting the two
live duplicate copies (`old/` and `Downloads/`) as a process discipline
problem rather than fixing it structurally. `docs/adr/0009-remote-state.md`
(state to S3) is the actual fix and is already proposed — this recovery
doesn't replace doing that, it just isn't blocked on it.

### Exact commands

These consolidate and verify only. **Per your instruction, nothing here runs
`terraform apply` or `terraform destroy`.** The last step before you'd
actually apply anything (the Cognito swap, HANDOFF.md Task 2) is a decision
worth making with the AWS ground-truth script's Cognito user check in hand
first, not folded into a recovery step.

```powershell
cd C:\Users\JakePistotnik\Desktop\r-shiny-hosting

# --- platform: bring the real state and tfvars into the new tree ---
Copy-Item old\platform\terraform.tfstate         platform\terraform.tfstate
Copy-Item old\platform\terraform.tfstate.backup  platform\terraform.tfstate.backup
Copy-Item old\platform\terraform.tfvars          platform\terraform.tfvars
Copy-Item old\platform\.terraform.lock.hcl       platform\.terraform.lock.hcl

# Add the two new Cognito variables -- get REAL values first, don't guess:
aws cognito-idp describe-user-pool --user-pool-id us-east-1_6vtAiYEpv --query "UserPool.Domain" --output text
aws cognito-idp list-identity-providers --user-pool-id us-east-1_6vtAiYEpv --query "Providers[].ProviderName" --output table
# Then append to platform\terraform.tfvars:
#   cognito_user_pool_id     = "us-east-1_6vtAiYEpv"
#   cognito_hosted_ui_domain = "<prefix from the describe-user-pool call above>"

cd platform
terraform init
terraform state list | Measure-Object -Line          # expect 53
terraform plan -no-color -out platform.tfplan | Tee-Object -FilePath plan.txt
Select-String -Path plan.txt -Pattern "^Plan:"       # expect ~4 to destroy, 4 SSM params changing, nothing else
cd ..

# --- dashboard: bring the state only -- the new tree's tfvars is already ahead (has the allowlist) ---
Copy-Item old\dashboard\terraform.tfstate         dashboard\terraform.tfstate
Copy-Item old\dashboard\terraform.tfstate.backup  dashboard\terraform.tfstate.backup
Copy-Item old\dashboard\.terraform.lock.hcl       dashboard\.terraform.lock.hcl

cd dashboard
terraform init
terraform state list | Measure-Object -Line          # expect 22
terraform plan -no-color -out dashboard.tfplan | Tee-Object -FilePath plan.txt
Select-String -Path plan.txt -Pattern "^Plan:"       # expect a new task definition revision (allowlist env vars), nothing else
cd ..

# --- once both plans look exactly as expected, clean up the duplicate copies ---
# (do this only after the above is verified -- these are the only other copies of the real state)
Remove-Item -Recurse -Force C:\Users\JakePistotnik\Downloads\platform
Remove-Item -Recurse -Force C:\Users\JakePistotnik\Downloads\dashboard
```

If either `terraform plan` shows anything beyond what's noted above —
especially anything touching the VPC, ALB, or ECS cluster in `platform`, or
anything besides the task definition in `dashboard` — stop and don't apply;
that's the "ask for help" trigger `HANDOFF.md` already names.

---

## 6. Cost Explorer spend

Queried month-to-date (2026-09-01 through 2026-09-08), filtered on tag
`Project = shiny`:

```json
"Total": { "UnblendedCost": { "Amount": "0", "Unit": "USD" } },
"Estimated": true
```

**Take this as "not yet reporting," not "genuinely free."** Two things
explain a $0 read here even though the ALB alone should be well into its
~$20/month fixed cost by day 8: Cost Explorer's own data typically lags actual
usage by 24–48 hours, and AWS tag-based cost filtering only works for tags
that have been explicitly **activated as a cost allocation tag** in the
Billing console (Billing → Cost Allocation Tags) — activation doesn't
backfill, so a tag applied to resources before it's activated shows nothing
for that whole period even after activation. Given every resource here was
created within the last day or two, either explanation (or both) fits.

To get a real number now instead of waiting: check **Billing → Cost
Allocation Tags** and confirm `Project` is activated (activate it if not —
it can take up to 24 hours to start populating), or in the meantime pull an
unfiltered total by service for the account instead of by tag, which won't
have the activation-lag problem:

```powershell
aws ce get-cost-and-usage --time-period Start=$start,End=$end --granularity MONTHLY --metrics "UnblendedCost" --group-by Type=DIMENSION,Key=SERVICE --output json
```

and look for `Amazon Elastic Load Balancing`, `Amazon Elastic Container
Service`, and `AWS Key Management Service` line items — those are what's
actually running for this project right now. Worth re-running the
tag-filtered query again in a day or two once the tag is confirmed active.

---

## Appendix: `scripts/aws-ground-truth.ps1`

Read-only. Nothing here creates, modifies, or deletes anything — it only
calls `describe-*`/`list-*`/`get-*` AWS APIs and writes the results to
`docs/aws-ground-truth.json`. This is the version that actually ran
successfully to produce section 3–4 above (the Cost Explorer `--filter`
quoting was fixed after the first run hit a Windows PowerShell-to-native-exe
quote-stripping bug — see the `file://` workaround in the Cost Explorer
block).

```powershell
$ErrorActionPreference = "Continue"
$result = [ordered]@{}
$result.generated_at = (Get-Date).ToUniversalTime().ToString("o")

function Try-Aws($block, $key) {
    try { $result[$key] = & $block }
    catch { $result[$key] = @{ error = $_.Exception.Message } }
}

Try-Aws { aws sts get-caller-identity --output json | ConvertFrom-Json } "caller_identity"

# ALB / VPC / ECS
Try-Aws { aws elbv2 describe-load-balancers --output json | ConvertFrom-Json } "load_balancers"
Try-Aws { aws elbv2 describe-target-groups --output json | ConvertFrom-Json } "target_groups"
Try-Aws { aws ec2 describe-vpcs --filters "Name=tag:Project,Values=shiny" --output json | ConvertFrom-Json } "vpcs"
Try-Aws { aws ec2 describe-subnets --filters "Name=tag:Project,Values=shiny" --output json | ConvertFrom-Json } "subnets"
Try-Aws { aws ecs describe-clusters --clusters shiny-cluster --include TAGS --output json | ConvertFrom-Json } "ecs_cluster"
Try-Aws {
    $arns = (aws ecs list-services --cluster shiny-cluster --output json | ConvertFrom-Json).serviceArns
    if ($arns) { aws ecs describe-services --cluster shiny-cluster --services $arns --output json | ConvertFrom-Json }
    else { @{ note = "no services" } }
} "ecs_services"

# ECR
Try-Aws { aws ecr describe-repositories --output json | ConvertFrom-Json } "ecr_repositories"
$result.ecr_images = @{}
if ($result.ecr_repositories -and $result.ecr_repositories.repositories) {
    foreach ($repo in $result.ecr_repositories.repositories) {
        try { $result.ecr_images[$repo.repositoryName] = aws ecr describe-images --repository-name $repo.repositoryName --output json | ConvertFrom-Json }
        catch { $result.ecr_images[$repo.repositoryName] = @{ error = $_.Exception.Message } }
    }
}

# Cognito -- includes the safety check CHANGES-hub-pool-and-allowlist.md requires before any swap
Try-Aws { aws cognito-idp list-user-pools --max-results 60 --output json | ConvertFrom-Json } "cognito_pools"
$result.cognito_pool_details = @{}
$result.cognito_app_clients  = @{}
$result.cognito_user_counts  = @{}
if ($result.cognito_pools -and $result.cognito_pools.UserPools) {
    foreach ($p in $result.cognito_pools.UserPools) {
        try { $result.cognito_pool_details[$p.Id] = aws cognito-idp describe-user-pool --user-pool-id $p.Id --output json | ConvertFrom-Json } catch {}
        try { $result.cognito_app_clients[$p.Id]  = aws cognito-idp list-user-pool-clients --user-pool-id $p.Id --output json | ConvertFrom-Json } catch {}
        try { $result.cognito_user_counts[$p.Id]  = (aws cognito-idp list-users --user-pool-id $p.Id --output json | ConvertFrom-Json).Users.Count } catch {}
    }
}

# Lambda / SSM / Route 53
Try-Aws { aws lambda list-functions --output json | ConvertFrom-Json } "lambdas"
Try-Aws { aws ssm get-parameters-by-path --path "/shiny/platform" --max-items 100 --output json | ConvertFrom-Json } "ssm_parameters"
Try-Aws { aws route53 list-resource-record-sets --hosted-zone-id Z07112442ZAIA7CFJKV72 --output json | ConvertFrom-Json } "route53_records"

# Cost Explorer -- month to date, tag Project=shiny
# (--filter takes a JSON string; passing that inline to a native exe strips
# the double quotes on Windows, so it's written to a file and passed as
# file://... instead -- same class of quoting bug docs/GOTCHAS.md catalogs.)
$start = (Get-Date -Day 1).ToString("yyyy-MM-dd")
$end   = (Get-Date).ToString("yyyy-MM-dd")
Try-Aws {
    $filterPath = Join-Path $env:TEMP "shiny-ce-filter.json"
    '{"Tags":{"Key":"Project","Values":["shiny"]}}' | Set-Content -Path $filterPath -Encoding ascii -NoNewline
    aws ce get-cost-and-usage `
        --time-period Start=$start,End=$end `
        --granularity MONTHLY `
        --metrics "UnblendedCost" `
        --filter "file://$filterPath" `
        --output json | ConvertFrom-Json
} "cost_explorer_mtd"

$outDir = "docs"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$result | ConvertTo-Json -Depth 12 | Out-File -Encoding utf8 (Join-Path $outDir "aws-ground-truth.json")
Write-Host "Wrote docs\aws-ground-truth.json"
```

---

## Proposed (not executed): moving the salvaged `terraform.tfvars` into the new stacks

This is the same file-move already folded into section 5's commands, laid out
on its own since you asked for it separately and flagged the two-new-variable
wrinkle.

- **`platform/terraform.tfvars`** does not exist yet in the new tree (only
  `terraform.tfvars.example` does). Bring `old/platform/terraform.tfvars`
  over as-is, then add the two variables `CHANGES-hub-pool-and-allowlist.md`
  introduces — `cognito_user_pool_id` and `cognito_hosted_ui_domain` — using
  real values from the `aws cognito-idp` calls in section 5, not placeholders.
  Nothing else in that file needs to change; `region`, `project`,
  `route53_zone_id`, `domain_name`, `budget_alert_emails`,
  `monthly_budget_usd`, and `alb_idle_timeout` all carry over unchanged.
- **`dashboard/terraform.tfvars`** — do **not** copy `old/dashboard/terraform.tfvars`
  over the new tree's copy. The new tree's version is already ahead: it
  already has `access_mode`, `allowed_emails`, and `access_contact` filled in
  (`old`'s does not — it predates the allowlist work). Overwriting it with
  `old`'s copy would be a regression. Only the *state* needs to move for
  dashboard, not the tfvars.
- **`model/terraform.tfvars`** — nothing to move. It's new-tree-only (the
  model stack has never been deployed, so `old/model` never existed), and it
  already carries its own allowlist block.
