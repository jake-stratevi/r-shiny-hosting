# ---------------------------------------------------------------------------
# Same contract as every other app stack: read the platform's SSM exports,
# no access to platform's Terraform state required. See dashboard/data.tf.
# ---------------------------------------------------------------------------

locals {
  ssm = "/${var.project}/platform"
}

data "aws_ssm_parameter" "alb_dns_name" { name = "${local.ssm}/alb_dns_name" }
data "aws_ssm_parameter" "alb_zone_id" { name = "${local.ssm}/alb_zone_id" }
data "aws_ssm_parameter" "https_listener_arn" { name = "${local.ssm}/https_listener_arn" }
data "aws_ssm_parameter" "cognito_user_pool_id" { name = "${local.ssm}/cognito_user_pool_id" }
data "aws_ssm_parameter" "cognito_user_pool_arn" { name = "${local.ssm}/cognito_user_pool_arn" }
data "aws_ssm_parameter" "cognito_domain" { name = "${local.ssm}/cognito_domain" }
data "aws_ssm_parameter" "oidc_provider_name" { name = "${local.ssm}/oidc_provider_name" }
data "aws_ssm_parameter" "route53_zone_id" { name = "${local.ssm}/route53_zone_id" }
data "aws_ssm_parameter" "domain_name" { name = "${local.ssm}/domain_name" }
data "aws_ssm_parameter" "log_retention_days" { name = "${local.ssm}/log_retention_days" }

locals {
  name                = "${var.project}-${var.app_key}"
  fqdn                = "${var.subdomain}.${data.aws_ssm_parameter.domain_name.value}"
  log_retention_days  = tonumber(data.aws_ssm_parameter.log_retention_days.value)

  # The shared catalog -- see catalog.yaml at the repo root and
  # docs/adr/0013-lightweight-shiny-portal.md. One file, read here and baked
  # into the Lambda package by lambda.tf, instead of a second hand-kept copy.
  catalog = yamldecode(file("${path.module}/../catalog.yaml"))
}
