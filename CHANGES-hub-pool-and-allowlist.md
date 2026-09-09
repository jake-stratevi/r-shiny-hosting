# Changes: Hub Cognito pool + per-app allowlist

Implements [ADR-0007](docs/adr/0007-reuse-hub-cognito-pool.md) and the first
half of [ADR-0008](docs/adr/0008-authorization-strategy.md).

Apply in this order. Do it before creating a single Cognito user.

---

## 1. `platform/cognito.tf` — replace the whole file

```hcl
# ---------------------------------------------------------------------------
# We do NOT create a user pool. The Assembled Hub pool already exists, is
# already federated to Entra as Microsoft365, and already holds native accounts
# for external partners. A second pool would mean a second Entra app
# registration, a second copy of every partner account, and two places to
# revoke someone's access from.
#
# This stack only references it. Each app stack creates its own OAuth client in
# that pool, because callback URLs are per-hostname.
# ---------------------------------------------------------------------------

variable "cognito_user_pool_id" {
  description = "Existing Assembled Hub user pool."
  type        = string
}

variable "cognito_hosted_ui_domain" {
  description = "Hosted UI domain PREFIX only, not the full amazoncognito.com hostname."
  type        = string
}

variable "cognito_staff_idp_name" {
  description = "Name of the Entra federation provider inside the pool."
  type        = string
  default     = "Microsoft365"
}

locals {
  cognito_user_pool_arn = "arn:aws:cognito-idp:${var.region}:${data.aws_caller_identity.current.account_id}:userpool/${var.cognito_user_pool_id}"
}
```

Constructing the ARN rather than using a data source keeps this working across
provider versions and avoids needing read permission on a pool another team owns.

## 2. `platform/ssm.tf` — four exports change

```hcl
    cognito_user_pool_id    = var.cognito_user_pool_id
    cognito_user_pool_arn   = local.cognito_user_pool_arn
    cognito_domain          = var.cognito_hosted_ui_domain
    oidc_provider_name      = var.cognito_staff_idp_name
```

## 3. `platform/terraform.tfvars` — add

```hcl
cognito_user_pool_id     = "us-east-1_6vtAiYEpv"
cognito_hosted_ui_domain = "<prefix — see below>"
```

Get the prefix with:

```powershell
aws cognito-idp describe-user-pool --user-pool-id us-east-1_6vtAiYEpv --query "UserPool.Domain" --output text
aws cognito-idp list-identity-providers --user-pool-id us-east-1_6vtAiYEpv --query "Providers[].ProviderName" --output table
```

The second confirms the IdP is really called `Microsoft365`. If it isn't, set
`cognito_staff_idp_name` accordingly.

## 4. Apply the platform

```powershell
cd platform
terraform plan -no-color -out platform.tfplan | Tee-Object -FilePath plan.txt
Select-String -Path plan.txt -Pattern "^Plan:"
```

Expect roughly **4 to destroy** — the user pool, its domain, and the two user
groups — and 4 SSM parameters changing. Confirm nothing outside Cognito and SSM
is affected, then apply.

Destroying the pool is safe only because nobody has signed into it.

## 5. `dashboard/cognito.tf` — drop the sentinel

The Entra provider always exists now, so the `"none"` check is dead code:

```hcl
  supported_identity_providers = ["COGNITO", data.aws_ssm_parameter.oidc_provider_name.value]
```

## 6. `dashboard/variables.tf` — add

```hcl
variable "access_mode" {
  description = <<-EOT
    How the app decides who may use it.
      off     no check (local development only)
      emails  allow addresses listed in allowed_emails
      groups  allow members of the Cognito groups in allowed_groups
    Start on emails; move to groups once they are populated and synced.
  EOT
  type    = string
  default = "emails"
}

variable "allowed_emails" {
  description = "Addresses permitted to use THIS app. Per-app, not shared."
  type        = list(string)
  default     = []
}

variable "allowed_groups" {
  description = "Cognito groups permitted to use THIS app. Used when access_mode = groups."
  type        = list(string)
  default     = []
}

variable "access_contact" {
  description = "Who a refused user is told to contact."
  type        = string
  default     = "your Stratevi contact"
}
```

## 7. `dashboard/ecs.tf` — four more environment variables

In the `container_definitions` `environment` array:

```hcl
      { name = "ACCESS_MODE", value = var.access_mode },
      { name = "ALLOWED_EMAILS", value = join(",", var.allowed_emails) },
      { name = "ALLOWED_GROUPS", value = join(",", var.allowed_groups) },
      { name = "ACCESS_CONTACT", value = var.access_contact },
      { name = "APP_LABEL", value = var.app_label },
```

`APP_LABEL` may already exist further up as `APP_NAME` — keep both; the refusal
page uses the human-readable one.

## 8. `dashboard/terraform.tfvars` — add

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

Every app gets its own list. That is the point — the pool says who exists, this
says who may see *this* dashboard.

## 9. Rebuild, push, apply

```powershell
cd ..\dashboard-app
docker build --platform linux/amd64 -t 652063276768.dkr.ecr.us-east-1.amazonaws.com/shiny-dashboard:latest .
docker push 652063276768.dkr.ecr.us-east-1.amazonaws.com/shiny-dashboard:latest

cd ..\dashboard
terraform plan -no-color -out dashboard.tfplan | Tee-Object -FilePath plan.txt
terraform apply dashboard.tfplan
aws ecs update-service --cluster shiny-cluster --service shiny-dashboard --force-new-deployment
```

The plan will show a new task definition revision and a service update. That is
expected — environment variables live in the task definition.

---

## Testing it

Sign in as yourself: you should see the dashboard.

To prove the gate actually refuses, temporarily remove your address from
`allowed_emails`, apply, and reload. You should get the refusal page naming your
address. Put it back afterwards.

Local development is unaffected — `ACCESS_MODE` is unset outside ECS, so
`docker run` behaves exactly as before.

## Moving to groups later

Create a Cognito group, add people to it, then change two lines:

```hcl
access_mode    = "groups"
allowed_groups = ["shiny-dashboard-viewers"]
```

No R changes, no rebuild. That is the whole reason the check is written as a
mode switch rather than a hardcoded email comparison.

The sync from Entra groups into Cognito groups is not automatic — Cognito will
not do it for you. Until the volume justifies a scheduled Lambda calling Graph
and `admin-add-user-to-group`, assign membership manually.
