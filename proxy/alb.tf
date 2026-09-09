# ---------------------------------------------------------------------------
# ONE target group and ONE listener rule for every app hostname. This is what
# lifts the ~50-target-group ceiling (ADR-0012): app stacks stop creating
# per-app target groups and listener rules once migrated, everything lands
# here on a host-header wildcard, and the proxy itself resolves Host -> app
# and forwards to the app task's private IP (not through the ALB again).
#
# Priority register (CLAUDE.md): portal 50, dashboard 100, model 200, proxy
# 5000, ECS association rules at app_priority+700. 5000 is deliberately far
# above every existing and near-future explicit rule so the catch-all never
# wins ahead of a still-unmigrated app's own rule.
# ---------------------------------------------------------------------------

resource "aws_lb_target_group" "proxy" {
  name        = "shiny-proxy"
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_ssm_parameter.vpc_id.value

  health_check {
    enabled             = true
    path                = "/__proxy/healthz"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }

  # Websockets proxied through here need to drain, but not for the default
  # 300s -- same rationale as dashboard/alb.tf's ecs target group.
  deregistration_delay = 30

  # No stickiness. Unlike a per-app target group (one app, cookie-pinned to
  # survive a single task instance), every proxy task is equivalent: none of
  # them hold per-app state, they all read the same DynamoDB tables and
  # re-discover app task IPs per request (10s cache). Any proxy task can
  # answer any request.

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_listener_rule" "this" {
  listener_arn = data.aws_ssm_parameter.https_listener_arn.value
  priority     = var.listener_rule_priority

  condition {
    host_header {
      values = [local.wildcard_fqdn]
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
    target_group_arn = aws_lb_target_group.proxy.arn
  }

  # ---------------------------------------------------------------------------
  # Deliberately NO `lifecycle { ignore_changes = [action] }` here.
  #
  # CLAUDE.md's rule protects that block on the APP stacks' listener rules
  # because their waker/sleeper Lambdas call ModifyRule at runtime to swap the
  # action between the waker Lambda target group and the ECS target group --
  # remove the ignore block there and every `terraform apply` fights the
  # scaler for ownership of `action`.
  #
  # Nothing mutates THIS rule at runtime. The proxy is always-on and always
  # forwards to the same target group; there is no waker to swap it away from.
  # Terraform owns this action block outright. If you are reading this because
  # you hit a plan diff on `action` and were about to "fix" it by adding the
  # ignore block back like the app stacks have -- don't. That would just hide
  # a real drift instead of showing it to you.
  # ---------------------------------------------------------------------------
}
