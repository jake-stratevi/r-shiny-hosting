# ---------------------------------------------------------------------------
# LEGACY REQUEST PATH -- everything in this file is `count = var.proxied ? 0 : 1`.
#
# proxied = false (the default, and today's behaviour):
#
#   This app attaches itself to the shared ALB listener.
#
#   Two target groups:
#     ecs   - the real Fargate task (target_type = ip)
#     waker - the Lambda holding page (target_type = lambda)
#
#   The listener rule forwards to the waker while scaled to zero, and the waker
#   swaps it to the ECS group once a task is healthy. Because the Lambdas mutate
#   the rule, Terraform ignores changes to the action block: Terraform owns the
#   rule's existence and its host-header condition, the Lambdas own which target
#   group it currently points at.
#
# proxied = true:
#
#   The authorizing proxy owns this hostname. It sits behind one priority-5000
#   catch-all rule for *.tools.stratevi.com, authenticates there, and connects
#   straight to the task ENI's private IP -- no per-app target group, no per-app
#   listener rule, no waker. See docs/design/proxy.md ("Routing: catch-all,
#   lowest precedence, migrate app-by-app"). Dropping the per-app target groups
#   is what removes the ~50-target-group-per-listener ceiling.
#
# Rollback is `proxied = false` + apply: these resources come back exactly as
# they are here. Delete the app's row from shiny-proxy-apps at the same time, or
# the proxy and the sleeper will both try to scale the service.
# ---------------------------------------------------------------------------

resource "aws_lb_target_group" "ecs" {
  count = var.proxied ? 0 : 1

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
  count = var.proxied ? 0 : 1

  name        = substr("${local.name}-wake", 0, 32)
  target_type = "lambda"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_target_group_attachment" "waker" {
  count = var.proxied ? 0 : 1

  target_group_arn = aws_lb_target_group.waker[0].arn
  target_id        = aws_lambda_function.waker[0].arn

  depends_on = [aws_lambda_permission.alb_invoke_waker]
}

resource "aws_lb_listener_rule" "this" {
  count = var.proxied ? 0 : 1

  listener_arn = data.aws_ssm_parameter.https_listener_arn.value
  priority     = var.listener_rule_priority

  condition {
    host_header {
      values = [local.fqdn]
    }
  }

  # Auth runs BEFORE the waker. An unauthenticated visitor cannot trigger a
  # task to start, which closes the obvious cost-abuse hole.
  action {
    type  = "authenticate-cognito"
    order = 1

    authenticate_cognito {
      user_pool_arn              = data.aws_ssm_parameter.cognito_user_pool_arn.value
      user_pool_client_id        = aws_cognito_user_pool_client.this[0].id
      user_pool_domain           = data.aws_ssm_parameter.cognito_domain.value
      on_unauthenticated_request = "authenticate"
      scope                      = "openid email profile"
      # 3 hours (Jake, 2026-09-11), down from 12. This is how long a
      # signed-in browser stays signed in without re-authenticating --
      # the window a borrowed or unlocked laptop stays useful to someone
      # else. Federated users barely notice: Entra re-issues silently.
      session_timeout            = 10800
    }
  }

  action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.waker[0].arn
  }

  # NEVER remove this. The waker and sleeper Lambdas rewrite the action block at
  # runtime; without the ignore, every apply fights the scaler. It moved inside
  # the conditional resource, it did not go away.
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
#
# A proxied service has no load balancer at all, so there is nothing to satisfy
# and this rule goes with the target group.
# ---------------------------------------------------------------------------
resource "aws_lb_listener_rule" "ecs_association" {
  count = var.proxied ? 0 : 1

  listener_arn = data.aws_ssm_parameter.https_listener_arn.value
  priority     = var.listener_rule_priority + 700

  condition {
    host_header {
      values = ["never-matches-${var.app_key}.invalid"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ecs[0].arn
  }
}
