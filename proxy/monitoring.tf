# ---------------------------------------------------------------------------
# The proxy is in the request path for every app, so its own unhealthy state
# is the one alarm this stack cannot skip. AWS/ApplicationELB metrics are
# free. No SNS wiring yet (matches docs/design/proxy.md -- add a topic and
# subscription once someone is on call for this).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "unhealthy" {
  alarm_name          = "${local.name}-unhealthy"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  period              = 300
  statistic           = "Maximum"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  treat_missing_data  = "notBreaching"

  dimensions = {
    TargetGroup  = local.ecs_tg_dimension
    LoadBalancer = data.aws_ssm_parameter.alb_dimension.value
  }

  alarm_description = "The proxy has had an unhealthy target for 5 minutes straight. This is in the request path for every app -- treat as an incident, not a metric."
}
