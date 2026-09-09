# ---------------------------------------------------------------------------
# We do NOT create a user pool. The Assembled Hub pool already exists, is
# already federated to Entra, and already holds native accounts for external
# partners. A second pool would mean a second Entra app registration, a second
# copy of every partner account, and two places to revoke access from.
#
# See docs/adr/0007-reuse-hub-cognito-pool.md.
#
# This stack only references the pool. Each app stack creates its own OAuth
# client inside it, because callback URLs are per-hostname.
# ---------------------------------------------------------------------------

variable "cognito_user_pool_id" {
  description = "Existing Assembled Hub user pool, e.g. us-east-1_6vtAiYEpv"
  type        = string
}

variable "cognito_hosted_ui_domain" {
  description = <<-EOT
    Hosted UI domain PREFIX only -- not the full amazoncognito.com hostname.
    Find it with:
      aws cognito-idp describe-user-pool --user-pool-id <id> --query "UserPool.Domain" --output text
  EOT
  type        = string
}

variable "cognito_staff_idp_name" {
  description = <<-EOT
    Name of the Entra federation provider inside the pool. Confirm with:
      aws cognito-idp list-identity-providers --user-pool-id <id> --query "Providers[].ProviderName"
  EOT
  type        = string
  default     = "Microsoft365"
}

locals {
  # Constructed rather than read via a data source, so this works across
  # provider versions and needs no read permission on a pool another team owns.
  cognito_user_pool_arn = "arn:aws:cognito-idp:${var.region}:${data.aws_caller_identity.current.account_id}:userpool/${var.cognito_user_pool_id}"
}
