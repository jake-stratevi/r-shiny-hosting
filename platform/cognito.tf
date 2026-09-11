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
# NO USERS ARE DECLARED HERE, DELIBERATELY. Do not add any (ADR-0015).
#
# Two populations, neither of which Terraform should own:
#
#   Staff (@stratevi.com -- the @assembledintelligence.co.uk aliases were
#   removed 2026-09-11, and proxy/portal.tf's staff_domains now names one
#   domain) sign in through the Microsoft365 federation. Cognito auto-provisions them as
#   `microsoft365_<sub>` EXTERNAL_PROVIDER users on their FIRST sign-in --
#   they do not exist in the pool before that, which the portal's user
#   picker has to account for (docs/design/portal.md, P2.5).
#
#   External clients are created by an admin (or by the portal, P2.5) with
#   admin-create-user; the pool is invite-only so nobody self-registers.
#
# This file DID seed the four staff as native users, so the switchover off
# the Hub pool could not lock anyone out. That worked, federation then went
# live, and the accounts were deleted on 2026-09-10 -- because the pool uses
# email as the username, a native jake@stratevi.com and a federated identity
# claiming the same address collide, and Cognito refuses the second one with
# AliasExistsException. Re-adding an aws_cognito_user for any address Entra
# emits reintroduces exactly that failure, at apply time, halfway through.
#
# Break-glass: there is intentionally no standing native admin. If federation
# breaks, create a temporary user in the console at an address Entra will
# never emit, add it to admin_emails in proxy/portal.tf's __config__ row (or
# straight into DynamoDB for speed), and delete both afterwards. RUNBOOK.md
# "User administration" has the steps.
# ---------------------------------------------------------------------------
