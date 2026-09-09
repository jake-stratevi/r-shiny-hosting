# ---------------------------------------------------------------------------
# All AWS/ApplicationELB metrics, which are free. HealthyHostCount is a good
# proxy for "a task is awake and billing".
#
# Both resources below are `count = var.proxied ? 0 : 1` because every metric
# they plot is dimensioned on this app's ECS target group, which only exists on
# the legacy request path (alb.tf). A proxied app has no target group, so
# AWS/ApplicationELB has nothing to say about it -- the proxy's own structured
# logs and the shared proxy dashboard replace this. See docs/design/proxy.md.
#
# Rollback (proxied = false + apply) recreates alarm and dashboard as they are
# here; CloudWatch metric history is retained independently of them.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "stuck_running" {
  count = var.proxied ? 0 : 1

  alarm_name          = "${local.name}-stuck-running"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = var.stuck_alarm_hours
  datapoints_to_alarm = var.stuck_alarm_hours
  threshold           = 0
  period              = 3600
  statistic           = "Minimum"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TargetGroup  = local.ecs_tg_dimension
    LoadBalancer = data.aws_ssm_parameter.alb_dimension.value
  }

  alarm_description = "${var.app_label} has been awake ${var.stuck_alarm_hours}h straight. Either genuinely in use, or the sleeper is broken and you are burning money."
}

resource "aws_cloudwatch_dashboard" "this" {
  count = var.proxied ? 0 : 1

  dashboard_name = "${local.name}-runtime"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 24
        height = 6
        properties = {
          title  = format("%s: awake (hours here x $%.4f/hr = your Fargate bill)", var.app_label, (var.cpu / 1024) * 0.04048 + (var.memory / 1024) * 0.004445)
          region = var.region
          stat   = "Maximum"
          period = 300
          view   = "timeSeries"
          metrics = [[
            "AWS/ApplicationELB", "HealthyHostCount",
            "TargetGroup", local.ecs_tg_dimension,
            "LoadBalancer", data.aws_ssm_parameter.alb_dimension.value,
            { label = var.app_key }
          ]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 24
        height = 6
        properties = {
          title  = "${var.app_label}: requests per target (heartbeat traffic drives idle detection)"
          region = var.region
          stat   = "Sum"
          period = 300
          view   = "timeSeries"
          metrics = [[
            "AWS/ApplicationELB", "RequestCountPerTarget",
            "TargetGroup", local.ecs_tg_dimension,
            "LoadBalancer", data.aws_ssm_parameter.alb_dimension.value,
            { label = var.app_key }
          ]]
        }
      }
    ]
  })
}
