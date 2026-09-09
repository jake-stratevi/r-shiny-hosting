# ---------------------------------------------------------------------------
# This app attaches itself to the shared ALB listener.
#
# Two target groups:
#   ecs   - the real Fargate task (target_type = ip)
#   waker - the Lambda holding page (target_type = lambda)
#
# The listener rule forwards to the waker while scaled to zero, and the waker
# swaps it to the ECS group once a task is healthy. Because the Lambdas mutate
# the rule, Terraform ignores changes to the action block: Terraform owns the
# rule's existence and its host-header condition, the Lambdas own which target
# group it currently points at.
# ---------------------------------------------------------------------------

resource "aws_lb_target_group" "ecs" {
  name        = substr("${local.name}-ecs", 0, 32)
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_ssm_parameter.vpc_id.value

  health_check {
    enabled             = true
    path                = "/"
    protocol            = "HTTP"
    matcher             = "200-399"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }

  # Websockets need to drain, but not for the default 300s.
  deregistration_delay = 30

  stickiness {
    type            = "lb_cookie"
    enabled         = true
    cookie_duration = 86400
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_target_group" "waker" {
  name        = substr("${local.name}-wake", 0, 32)
  target_type = "lambda"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_target_group_attachment" "waker" {
  target_group_arn = aws_lb_target_group.waker.arn
  target_id        = aws_lambda_function.waker.arn

  depends_on = [aws_lambda_permission.alb_invoke_waker]
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
    target_group_arn = aws_lb_target_group.waker.arn
  }

   lifecycle {
     ignore_changes = [action]
   }
}

# ---------------------------------------------------------------------------
# ECS will not create a service against a target group that has no load
# balancer association ("does not have an associated load balancer"). Our real
# listener rule points at the WAKER group until the waker swaps it, so the ECS
# group would otherwise be orphaned at create time.
#
# This rule attaches it to the listener on a condition that can never match --
# .invalid is a reserved TLD -- purely to satisfy that requirement. It never
# serves traffic.
# ---------------------------------------------------------------------------
resource "aws_lb_listener_rule" "ecs_association" {
  listener_arn = data.aws_ssm_parameter.https_listener_arn.value
  priority     = var.listener_rule_priority + 700

  condition {
    host_header {
      values = ["never-matches-${var.app_key}.invalid"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ecs.arn
  }
}
