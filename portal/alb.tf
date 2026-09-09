# ---------------------------------------------------------------------------
# Unlike dashboard/model, this rule needs no `ignore_changes` lifecycle block.
# There is no waker/sleeper here rewriting the action at runtime -- the portal
# is one stable Lambda target the whole time, so Terraform owns this rule
# completely and every apply reflects reality.
# ---------------------------------------------------------------------------

resource "aws_lb_target_group" "this" {
  name        = "${local.name}-tg"
  target_type = "lambda"

  lambda_multi_value_headers_enabled = false
}

resource "aws_lb_target_group_attachment" "this" {
  target_group_arn = aws_lb_target_group.this.arn
  target_id        = aws_lambda_function.this.arn

  depends_on = [aws_lambda_permission.alb_invoke]
}

resource "aws_lb_listener_rule" "this" {
  listener_arn = data.aws_ssm_parameter.https_listener_arn.value
  priority     = var.listener_rule_priority

  condition {
    host_header {
      values = [local.fqdn]
    }
  }

  action {
    type  = "authenticate-cognito"
    order = 1

    authenticate_cognito {
      user_pool_arn              = data.aws_ssm_parameter.cognito_user_pool_arn.value
      user_pool_client_id        = aws_cognito_user_pool_client.this.id
      user_pool_domain           = data.aws_ssm_parameter.cognito_domain.value
      on_unauthenticated_request = "authenticate"
      scope                      = "openid email profile"
      session_timeout            = 43200
    }
  }

  action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.this.arn
  }
}
