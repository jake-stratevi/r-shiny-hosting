# This stack's own client on the shared Hub pool. Separate from every app's
# client because the callback URL is per-hostname (ADR-0007).
resource "aws_cognito_user_pool_client" "this" {
  name         = local.name
  user_pool_id = data.aws_ssm_parameter.cognito_user_pool_id.value

  generate_secret = true # required by the ALB authenticate-cognito action

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = ["COGNITO", data.aws_ssm_parameter.oidc_provider_name.value]

  callback_urls = ["https://${local.fqdn}/oauth2/idpresponse"]
  logout_urls   = ["https://${local.fqdn}"]

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
# Managed Login requires an explicit branding record per client -- without it
# the hosted UI shows "Login pages unavailable" (found the hard way getting
# dashboard working today). aws_cognito_managed_login_branding exists in the
# AWS provider only from v6.12.0 -- not worth a major-version jump on a brand
# new stack for one resource. Instead, run this once after applying:
#
#   aws cognito-idp create-managed-login-branding \
#     --user-pool-id <cognito_user_pool_id from platform's SSM export> \
#     --client-id <output of `terraform output` / `terraform state show
#       aws_cognito_user_pool_client.this` after this stack's first apply> \
#     --use-cognito-provided-values
#
# See docs/adr/0013-lightweight-shiny-portal.md.
# ---------------------------------------------------------------------------
