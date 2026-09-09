# ---------------------------------------------------------------------------
# Same contract every app stack reads: platform exports under /shiny/platform,
# no remote state access needed. See platform/ssm.tf and dashboard/data.tf.
#
# This stack deliberately does NOT read task_security_group_id -- that shared
# SG only opens port 3838 (the Shiny apps' port) from the ALB SG. The proxy
# listens on 8080 and needs its own SG: aws_security_group.proxy, in ecs.tf.
# ---------------------------------------------------------------------------

locals {
  ssm = "/${var.project}/platform"
}

data "aws_ssm_parameter" "vpc_id" { name = "${local.ssm}/vpc_id" }
data "aws_ssm_parameter" "subnet_ids" { name = "${local.ssm}/subnet_ids" }
data "aws_ssm_parameter" "alb_dns_name" { name = "${local.ssm}/alb_dns_name" }
data "aws_ssm_parameter" "alb_zone_id" { name = "${local.ssm}/alb_zone_id" }
data "aws_ssm_parameter" "alb_dimension" { name = "${local.ssm}/alb_dimension" }
data "aws_ssm_parameter" "https_listener_arn" { name = "${local.ssm}/https_listener_arn" }
data "aws_ssm_parameter" "ecs_cluster_name" { name = "${local.ssm}/ecs_cluster_name" }
data "aws_ssm_parameter" "ecs_cluster_arn" { name = "${local.ssm}/ecs_cluster_arn" }
data "aws_ssm_parameter" "task_execution_role_arn" { name = "${local.ssm}/task_execution_role_arn" }
data "aws_ssm_parameter" "cognito_user_pool_id" { name = "${local.ssm}/cognito_user_pool_id" }
data "aws_ssm_parameter" "cognito_user_pool_arn" { name = "${local.ssm}/cognito_user_pool_arn" }
data "aws_ssm_parameter" "cognito_domain" { name = "${local.ssm}/cognito_domain" }
data "aws_ssm_parameter" "oidc_provider_name" { name = "${local.ssm}/oidc_provider_name" }
data "aws_ssm_parameter" "route53_zone_id" { name = "${local.ssm}/route53_zone_id" }
data "aws_ssm_parameter" "domain_name" { name = "${local.ssm}/domain_name" }
data "aws_ssm_parameter" "log_retention_days" { name = "${local.ssm}/log_retention_days" }

# ---------------------------------------------------------------------------
# CONTRACT GAP: platform/ssm.tf does not export the ALB's security group id
# (only task_security_group_id, which is the wrong SG -- that one is scoped to
# port 3838). Looked it up by name instead of by SSM/remote state so this
# stack still needs no access to the platform stack's state. The name is
# "${var.project}-alb" per platform/network.tf's aws_security_group.alb
# ("${local.name}-alb" where local.name = var.project).
#
# This should become a proper `/shiny/platform/alb_security_group_id` SSM
# export in platform/ssm.tf -- flagged in the build report, NOT fixed here,
# because this task is scoped to proxy/ only.
# ---------------------------------------------------------------------------
data "aws_security_group" "alb" {
  vpc_id = data.aws_ssm_parameter.vpc_id.value

  filter {
    name   = "tag:Name"
    values = ["${var.project}-alb"]
  }
}

locals {
  name          = "${var.project}-proxy"
  proxy_fqdn    = "${var.proxy_subdomain}.${data.aws_ssm_parameter.domain_name.value}"
  wildcard_fqdn = "*.${data.aws_ssm_parameter.domain_name.value}"

  subnet_ids         = split(",", data.aws_ssm_parameter.subnet_ids.value)
  log_retention_days = tonumber(data.aws_ssm_parameter.log_retention_days.value)

  cognito_config = {
    user_pool_arn = data.aws_ssm_parameter.cognito_user_pool_arn.value
    client_id     = aws_cognito_user_pool_client.this.id
    domain        = data.aws_ssm_parameter.cognito_domain.value
  }

  # CloudWatch dimension is the ARN suffix, e.g. targetgroup/name/1234abcd
  ecs_tg_dimension = element(split(":", aws_lb_target_group.proxy.arn), 5)
}
