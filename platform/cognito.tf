# ---------------------------------------------------------------------------
# This platform owns its identity directory. It no longer rides the Assembled
# Hub pool (us-east-1_6vtAiYEpv) -- this SUPERSEDES ADR-0007; see ADR-0015.
#
# Why: the portal creates and manages users itself (docs/design/portal.md,
# P2.5). Writing users into a directory shared with the other Assembled
# products is a boundary Jake does not want to cross -- a service that can
# admin-create-user in the Hub pool is a service that can mint accounts for
# every other product on it. A pool owned by this stack, reachable only by
# this platform, removes the question entirely. Decided 2026-09-10.
#
# What that costs us, honestly: the Hub's Entra federation and all of the
# edge cases ADR-0007 listed as already-solved (synthetic `microsoft365_...`
# usernames, `email` missing on federated logins) do not come along. Staff
# sign in with email + password in this pool until Entra is federated into it
# as a later, optional step. See the oidc_provider_name note in ssm.tf.
#
# Unchanged: this stack owns the pool and its domain, and every app stack and
# the proxy create their OWN app client in it, because callback URLs are
# per-hostname.
# ---------------------------------------------------------------------------

variable "cognito_domain_prefix" {
  description = <<-EOT
    Hosted-UI domain PREFIX only -- the pool is served at
    https://<prefix>.auth.<region>.amazoncognito.com.

    Must be GLOBALLY unique across all AWS accounts, lowercase, and must not
    contain the substrings "aws", "amazon" or "cognito" (Cognito rejects
    those outright). Changing it after users exist changes every hosted-UI
    URL, so pick once.
  EOT
  type        = string
  default     = "stratevi-shinyplatform"
}

variable "cognito_staff_idp_name" {
  description = <<-EOT
    Value exported as the `oidc_provider_name` SSM parameter, which consumers
    put into a client's supported_identity_providers.

    "COGNITO" means "this pool's own native directory, no federation" -- the
    correct value today, because the new pool has no identity providers. If
    Entra is ever federated INTO this pool, set this to that provider's name
    (aws cognito-idp list-identity-providers --user-pool-id <id>) and
    re-apply; consumers pick the change up on their next apply. See ssm.tf.
  EOT
  type        = string
  default     = "COGNITO"
}

locals {
  # Sign-in front door quoted in the invite email. The ALB bounces users to
  # the hosted UI on their way in, so this is the human-facing address, not
  # the Cognito domain above.
  sign_in_host = "shinyplatform.${var.domain_name}"

  # Invite-only, so the directory starts with exactly the people who must not
  # be locked out by the switchover. Every user after these four is created by
  # the portal (docs/design/portal.md, P2.5) or by admin-create-user; do NOT
  # grow this list into the user database.
  staff_emails = [
    "jake@stratevi.com",
    "nick@stratevi.com",
    "yi@stratevi.com",
    "josh@stratevi.com",
  ]

  # Kept as a local so iam.tf and ssm.tf read one name. It is now simply the
  # pool this stack creates, not a string assembled from someone else's ID.
  cognito_user_pool_arn = aws_cognito_user_pool.this.arn
}

resource "aws_cognito_user_pool" "this" {
  name = "${local.name}-platform"

  # Email IS the username. No separate handle to reconcile with the portal's
  # allowlists, which are email-keyed everywhere (catalog.yaml, app tfvars,
  # shiny-proxy-apps rows).
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  username_configuration {
    case_sensitive = false
  }

  # Invite-only directory: admins and the portal create users, nobody
  # self-registers. This is the whole point of owning the pool.
  admin_create_user_config {
    allow_admin_create_user_only = true

    invite_message_template {
      email_subject = "Your Stratevi Shiny platform account"
      email_message = <<-EOT
        You have been given access to the Stratevi Shiny platform.

        Sign in at https://${local.sign_in_host} with:
          username: {username}
          temporary password: {####}

        You will be asked to choose a new password on first sign-in. The
        temporary password expires in 7 days -- ask an admin to re-send the
        invite if it lapses.
      EOT
      sms_message   = "Stratevi Shiny platform: username {username}, temporary password {####}"
    }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # COGNITO_DEFAULT: Cognito sends invite and reset mail from its own address,
  # capped at 50 messages/day and no SES setup. Ample for an invite-only staff
  # directory. The portal's SES work (portal.md, P3 reminders) is a separate
  # domain verification and does not change this.
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  mfa_configuration = "OFF"

  # Managed Login v2 (the domain below) is an Essentials-tier feature; Lite
  # pools only get the classic hosted UI. At four staff MAUs the tier costs
  # cents -- it is not a lever worth pulling for this platform.
  user_pool_tier = "ESSENTIALS"

  # This directory is the only way into every app. Losing it to a stray
  # destroy would lock everyone out and orphan every app client.
  deletion_protection = "ACTIVE"
}

# ---------------------------------------------------------------------------
# BRANDING IS NOT OPTIONAL IN A managed_login_version = 2 POOL.
#
# Every app client in this pool MUST have a managed-login branding
# association or its /login renders "Login pages unavailable. Please contact
# an administrator." It is not a permissions problem and not a callback-URL
# problem -- it cost an hour on the Hub pool already (docs/GOTCHAS.md, and the
# same banner in proxy/cognito.tf and portal/cognito.tf).
#
# Terraform (provider 5.100) has no aws_cognito_managed_login_branding
# resource, so the OPERATOR creates the association by CLI after each client
# is created or RECREATED -- once per client, including the proxy's shared
# client and every per-app client:
#
#   aws cognito-idp create-managed-login-branding `
#     --user-pool-id <this pool's id> --client-id <client id> `
#     --use-cognito-provided-values
#
# When the provider gains the resource, replace this with the real thing and
# import the associations.
# ---------------------------------------------------------------------------

resource "aws_cognito_user_pool_domain" "this" {
  domain                = var.cognito_domain_prefix
  user_pool_id          = aws_cognito_user_pool.this.id
  managed_login_version = 2
}

# ---------------------------------------------------------------------------
# Seed users.
#
# These four exist so the switchover off the Hub pool cannot lock anyone out:
# they are created with the pool, and each gets an invite email carrying a
# temporary password the moment this applies. FURTHER USERS ARE CREATED BY THE
# PORTAL (docs/design/portal.md, P2.5) via the SDK, not here -- do not turn
# this into the user table.
#
# Terraform owns existence only. A password change, a disable, or an MFA
# enrolment done outside Terraform is not reverted by an apply; deleting a
# staff member from local.staff_emails DOES delete the account.
# ---------------------------------------------------------------------------

resource "aws_cognito_user" "staff" {
  for_each = toset(local.staff_emails)

  user_pool_id = aws_cognito_user_pool.this.id
  username     = each.value

  attributes = {
    email          = each.value
    email_verified = "true"
  }

  # Sends the invite_message_template above with a generated temporary
  # password. Omitting this (or using message_action = "SUPPRESS") creates the
  # account silently and nobody can sign in.
  desired_delivery_mediums = ["EMAIL"]
}
