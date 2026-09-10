# ---------------------------------------------------------------------------
# ONE shared client for every app behind the proxy, unlike the app stacks
# (one client each, because their callback URL is per-hostname). The proxy's
# listener rule is a single catch-all, so it needs a single client whose
# callback list grows by one URL per migrated/created app host.
#
# Cognito app clients accept up to ~100 callback URLs (docs/design/proxy.md).
# The portal phase adds hosts to this list via the SDK as apps are created;
# for now it's driven by var.app_hosts (a plain list, kept in tfvars until the
# portal exists to call CreateUserPoolClient/UpdateUserPoolClient itself).
# ---------------------------------------------------------------------------

resource "aws_cognito_user_pool_client" "this" {
  name         = local.name
  user_pool_id = data.aws_ssm_parameter.cognito_user_pool_id.value

  generate_secret = true # required by the ALB authenticate-cognito action

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = ["COGNITO", data.aws_ssm_parameter.oidc_provider_name.value]

  # The retired menu host is included on purpose -- see the comment on
  # local.retired_portal_fqdn in data.tf. It buys a branded 404 instead of a
  # Cognito error for anyone with an old bookmark; it grants no access.
  callback_urls = distinct(concat(
    [for host in var.app_hosts : "https://${host}/oauth2/idpresponse"],
    [for host in local.portal_hosts : "https://${host}/oauth2/idpresponse"],
    ["https://${local.retired_portal_fqdn}/oauth2/idpresponse"],
  ))

  # The signed-out page is a logout target, so Cognito must accept it as a
  # logout_uri -- it matches these EXACTLY, so the path matters.
  #
  # NOTE: logout_urls is in ignore_changes below, so editing this list does
  # NOT push the change. It is here so a from-scratch rebuild is correct;
  # the live client is updated by hand (RUNBOOK, "User administration").
  logout_urls = distinct(concat(
    [for host in var.app_hosts : "https://${host}"],
    [for host in local.portal_hosts : "https://${host}"],
    [for host in local.portal_hosts : "https://${host}/__proxy/signed-out"],
    ["https://${local.retired_portal_fqdn}"],
  ))

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 12

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "hours"
  }

  # ---------------------------------------------------------------------
  # NEVER REMOVE THIS. Same law as the app stacks' listener-rule and
  # desired_count ignores (CLAUDE.md): Terraform owns this client's
  # EXISTENCE and settings, the runtime owns these two lists.
  #
  # From P2a on, the portal adds a callback and logout URL to this client
  # every time someone creates an app (docs/design/portal-p2a.md,
  # provisioning step 5 -- it is a read-modify-write against
  # UpdateUserPoolClient). Terraform only knows about the hosts in
  # var.app_hosts and the portal hosts, so without this block the next
  # `terraform apply` quietly resets the lists and every app created
  # through the wizard loses its sign-in -- with no error, and a diff
  # nobody reads twice.
  #
  # The cost of the ignore is that adding a host to var.app_hosts no
  # longer does anything on its own; for a proxied app the portal is the
  # thing that registers it. That is the correct division: apps are
  # runtime data now, not infrastructure.
  # ---------------------------------------------------------------------
  lifecycle {
    ignore_changes = [
      callback_urls,
      logout_urls,

      # Belt AND braces on the login page's identity providers.
      #
      # The list above is built from an SSM value another stack exports. On
      # 2026-09-10 that value was still the placeholder "COGNITO" while the
      # live client carried ["COGNITO", "Microsoft365"] -- so the next apply
      # of this stack would have quietly removed the Microsoft button and
      # locked every federated user out of a platform they had been using
      # all day. The plan line would have read like a one-word tidy-up.
      #
      # The tfvar is now correct (platform/terraform.tfvars names
      # Microsoft365), so a from-scratch rebuild produces the right list --
      # that file stays the record of intent. This ignore is the insurance:
      # a provider added or renamed in the console can never be stripped by
      # an apply that simply had stale inputs.
      #
      # Adding an IdP: create it in the pool, add it here in tfvars for the
      # record, and attach it to the client in the console. Terraform will
      # not fight you either way.
      supported_identity_providers,
    ]
  }
}

# ---------------------------------------------------------------------------
# MANUAL STEP THAT TERRAFORM CANNOT DO YET (provider 5.100 lacks
# aws_cognito_managed_login_branding; it arrives in a later major).
#
# The Hub pool uses Managed Login (the branded hosted UI). Every client MUST
# have a branding-style association or its /login renders "Login pages
# unavailable. Please contact an administrator." -- which is exactly how this
# client failed on first use. The per-app clients carry a defaults-only
# association; this client needs the same. After creating (or ever
# RECREATING) this client, run:
#
#   aws cognito-idp create-managed-login-branding `
#     --user-pool-id <pool id> --client-id <this client's id> `
#     --use-cognito-provided-values
#
# When the provider is upgraded past the resource's introduction, replace
# this comment with the real resource and import the association.
# ---------------------------------------------------------------------------
