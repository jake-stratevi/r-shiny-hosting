# ---------------------------------------------------------------------------
# The contract between this stack and the app stacks.
#
# App stacks read these with data "aws_ssm_parameter", which means they do NOT
# need access to this stack's Terraform state. Deploy platform once, then
# dashboard and model independently, in either order, by different people.
#
# Parameters are Standard tier: free.
# ---------------------------------------------------------------------------

locals {
  exports = {
    vpc_id                  = aws_vpc.this.id
    task_security_group_id  = aws_security_group.tasks.id
    alb_arn                 = aws_lb.this.arn
    alb_dns_name            = aws_lb.this.dns_name
    alb_zone_id             = aws_lb.this.zone_id
    alb_dimension           = local.alb_dimension
    https_listener_arn      = aws_lb_listener.https.arn
    ecs_cluster_name        = aws_ecs_cluster.this.name
    ecs_cluster_arn         = aws_ecs_cluster.this.arn
    task_execution_role_arn = aws_iam_role.task_execution.arn

    scaler_role_arn       = aws_iam_role.scaler.arn
    cognito_user_pool_id  = var.cognito_user_pool_id
    cognito_user_pool_arn = local.cognito_user_pool_arn
    cognito_domain        = var.cognito_hosted_ui_domain
    oidc_provider_name    = var.cognito_staff_idp_name
    route53_zone_id       = var.route53_zone_id
    domain_name           = var.domain_name
    log_retention_days    = tostring(var.log_retention_days)
  }
}

resource "aws_ssm_parameter" "export" {
  for_each = local.exports

  name  = "${local.ssm}/${each.key}"
  type  = "String"
  value = each.value
}

resource "aws_ssm_parameter" "subnet_ids" {
  name  = "${local.ssm}/subnet_ids"
  type  = "StringList"
  value = join(",", aws_subnet.public[*].id)
}
