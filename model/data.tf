# ---------------------------------------------------------------------------
# Everything shared comes from the platform stack's SSM parameters. No remote
# state access required, so this stack can be applied by someone who cannot
# read the platform state.
# ---------------------------------------------------------------------------

locals {
  ssm = "/${var.project}/platform"
}

data "aws_ssm_parameter" "task_security_group_id" { name = "${local.ssm}/task_security_group_id" }
data "aws_ssm_parameter" "subnet_ids" { name = "${local.ssm}/subnet_ids" }
data "aws_ssm_parameter" "alb_dns_name" { name = "${local.ssm}/alb_dns_name" }
data "aws_ssm_parameter" "alb_zone_id" { name = "${local.ssm}/alb_zone_id" }
data "aws_ssm_parameter" "alb_dimension" { name = "${local.ssm}/alb_dimension" }
data "aws_ssm_parameter" "https_listener_arn" { name = "${local.ssm}/https_listener_arn" }
data "aws_ssm_parameter" "vpc_id" { name = "${local.ssm}/vpc_id" }
data "aws_ssm_parameter" "ecs_cluster_name" { name = "${local.ssm}/ecs_cluster_name" }
data "aws_ssm_parameter" "ecs_cluster_arn" { name = "${local.ssm}/ecs_cluster_arn" }
data "aws_ssm_parameter" "task_execution_role_arn" { name = "${local.ssm}/task_execution_role_arn" }
data "aws_ssm_parameter" "task_role_arn" { name = "${local.ssm}/task_role_arn" }
data "aws_ssm_parameter" "scaler_role_arn" { name = "${local.ssm}/scaler_role_arn" }
data "aws_ssm_parameter" "cognito_user_pool_id" { name = "${local.ssm}/cognito_user_pool_id" }
data "aws_ssm_parameter" "cognito_user_pool_arn" { name = "${local.ssm}/cognito_user_pool_arn" }
data "aws_ssm_parameter" "cognito_domain" { name = "${local.ssm}/cognito_domain" }
data "aws_ssm_parameter" "oidc_provider_name" { name = "${local.ssm}/oidc_provider_name" }
data "aws_ssm_parameter" "route53_zone_id" { name = "${local.ssm}/route53_zone_id" }
data "aws_ssm_parameter" "domain_name" { name = "${local.ssm}/domain_name" }
data "aws_ssm_parameter" "log_retention_days" { name = "${local.ssm}/log_retention_days" }

locals {
  name = "${var.project}-${var.app_key}"
  fqdn = "${var.subdomain}.${data.aws_ssm_parameter.domain_name.value}"

  subnet_ids         = split(",", data.aws_ssm_parameter.subnet_ids.value)
  log_retention_days = tonumber(data.aws_ssm_parameter.log_retention_days.value)

  cognito_config = {
    user_pool_arn = data.aws_ssm_parameter.cognito_user_pool_arn.value
    client_id     = aws_cognito_user_pool_client.this.id
    domain        = data.aws_ssm_parameter.cognito_domain.value
  }

  # CloudWatch dimension is the ARN suffix, e.g. targetgroup/name/1234abcd
  ecs_tg_dimension = element(split(":", aws_lb_target_group.ecs.arn), 5)

  # Reserve one core for the Shiny process itself. 1024 CPU units = 1 vCPU.
  cpu_workers = max(1, floor(var.cpu / 1024) - 1)
}
