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

  callback_urls = distinct(concat(
    [for host in var.app_hosts : "https://${host}/oauth2/idpresponse"],
    [for host in local.portal_hosts : "https://${host}/oauth2/idpresponse"],
  ))

  logout_urls = distinct(concat(
    [for host in var.app_hosts : "https://${host}"],
    [for host in local.portal_hosts : "https://${host}"],
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
