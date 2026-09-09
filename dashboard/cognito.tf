# This app's own client on the shared user pool. Separate per app because the
# callback URL is per-hostname.
#
# Only the legacy request path needs it: it exists solely to feed the
# authenticate-cognito action on this app's own listener rule (alb.tf) and the
# COGNITO_CONFIG the waker/sleeper hand to their holding page. When proxied, the
# proxy authenticates on the shared catch-all rule with ONE shared client whose
# callback list gains this host at migration time -- docs/design/proxy.md,
# "Auth stays on the ALB".
#
# Rollback (proxied = false) recreates the client with a NEW id and secret; the
# listener rule is recreated in the same apply and points at it, so nothing else
# needs touching. Users are asked to sign in again.
resource "aws_cognito_user_pool_client" "this" {
  count = var.proxied ? 0 : 1

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
