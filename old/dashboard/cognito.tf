# This app's own client on the shared user pool. Separate per app because the
# callback URL is per-hostname.
resource "aws_cognito_user_pool_client" "this" {
  name         = local.name
  user_pool_id = data.aws_ssm_parameter.cognito_user_pool_id.value

  generate_secret = true # required by the ALB authenticate-cognito action

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

supported_identity_providers = compact([
    "COGNITO",
    data.aws_ssm_parameter.oidc_provider_name.value == "none" ? "" : data.aws_ssm_parameter.oidc_provider_name.value,
  ])

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
