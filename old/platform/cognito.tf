# ---------------------------------------------------------------------------
# One user pool serves every app. Each app stack creates its OWN app client,
# because the callback URL is per-hostname.
#
# Cost: 10,000 direct/social MAU free indefinitely. SAML/OIDC federated users
# are free for the first 50, then $0.015/MAU.
# ---------------------------------------------------------------------------

resource "aws_cognito_user_pool" "this" {
  name = "${local.name}-users"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  admin_create_user_config {
    allow_admin_create_user_only = var.admin_create_users_only
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 5
      max_length = 254
    }
  }

  # Handy for telling internal staff from external client users in logs.
  schema {
    name                = "org"
    attribute_data_type = "String"
    required            = false
    mutable             = true

    string_attribute_constraints {
      min_length = 0
      max_length = 128
    }
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = var.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_identity_provider" "oidc" {
  count = var.oidc_provider == null ? 0 : 1

  user_pool_id  = aws_cognito_user_pool.this.id
  provider_name = var.oidc_provider.name
  provider_type = "OIDC"

  provider_details = {
    client_id                 = var.oidc_provider.client_id
    client_secret             = var.oidc_provider.client_secret
    oidc_issuer               = var.oidc_provider.issuer
    authorize_scopes          = var.oidc_provider.scopes
    attributes_request_method = "GET"
  }

  attribute_mapping = {
    email    = "email"
    username = "sub"
  }
}

resource "aws_cognito_user_group" "internal" {
  name         = "internal"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "Staff accounts"
}

resource "aws_cognito_user_group" "client" {
  name         = "client"
  user_pool_id = aws_cognito_user_pool.this.id
  description  = "External client accounts"
}

