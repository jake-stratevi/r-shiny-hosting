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

# The ALB SG (for our own ingress rule) and the shared tasks SG (so we can
# open the app port FROM the proxy -- see apps_from_proxy in ecs.tf). Both
# proper SSM exports as of the same change that added this stack.
data "aws_ssm_parameter" "alb_security_group_id" { name = "${local.ssm}/alb_security_group_id" }
data "aws_ssm_parameter" "task_security_group_id" { name = "${local.ssm}/task_security_group_id" }

locals {
  name          = "${var.project}-proxy"
  proxy_fqdn    = "${var.proxy_subdomain}.${data.aws_ssm_parameter.domain_name.value}"
  wildcard_fqdn = "*.${data.aws_ssm_parameter.domain_name.value}"

  # THE portal address, and the only one. Hosts in this list are answered
  # with the portal UI (PORTAL_HOSTS in ecs.tf) instead of being forwarded to
  # an app task; it also drives the uploads bucket's CORS origins.
  portal_hosts = [local.proxy_fqdn]

  # RETIRED 2026-09-10. dashboards.tools.stratevi.com was the ADR-0013 Lambda
  # menu's address, then briefly an alias for this portal. It is no longer a
  # portal host: the proxy now answers it with the branded unknown-host 404.
  #
  # It is deliberately still in the Cognito client's callback list
  # (cognito.tf). The ALB authenticates BEFORE the proxy sees the request, so
  # an unregistered callback would give a visitor a raw Cognito
  # "redirect_uri mismatch" error instead of our page. Keeping it costs
  # nothing and makes a stale bookmark fail politely.
  #
  # It is deliberately NOT in portal_hosts, so it gets no portal UI, and NOT
  # in the CORS origins, so it cannot be used to upload.
  #
  # Retire fully -- drop this local and the callback -- once no bookmark
  # points here. Reason it existed at all: `dashboard` (the Shiny app) and
  # `dashboards` (this) differ by one character, and mistyping the app's
  # name wakes a Fargate task that costs money.
  retired_portal_fqdn = "${var.portal_menu_subdomain}.${data.aws_ssm_parameter.domain_name.value}"

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
