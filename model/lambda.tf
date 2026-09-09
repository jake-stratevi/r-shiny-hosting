# ---------------------------------------------------------------------------
# LEGACY SCALER -- every resource below is `count = var.proxied ? 0 : 1`.
#
# The waker (holding page + scale-up) and the sleeper (idle scale-down +
# pre-warm) are the per-app scaler for the legacy request path. When proxied,
# the proxy does both jobs itself and does them better: wake-on-request from the
# real request, and server-side sleep measured from actual proxied traffic and
# open websockets rather than from RequestCountPerTarget. That also retires the
# ADR-0006 client-side heartbeat for this app -- see docs/design/proxy.md,
# "Wake-on-request" and "Sleep is server-side".
#
# Do NOT leave both running against one service: the sleeper would scale to zero
# underneath a user the proxy considers active. proxied = true is the switch.
# Rollback = flip it back to false and apply; both Lambdas, their log groups,
# the ALB invoke permission and the EventBridge schedule are recreated from the
# same source in lambda/.
#
# The archive_file data sources stay unconditional -- they only zip local
# directories, cost nothing and produce no plan diff either way.
# ---------------------------------------------------------------------------

data "archive_file" "waker" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/waker"
  output_path = "${path.module}/.build/waker.zip"
}

data "archive_file" "sleeper" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/sleeper"
  output_path = "${path.module}/.build/sleeper.zip"
}

# --- Waker: the ALB's forward target while this app is asleep ---------------

resource "aws_cloudwatch_log_group" "waker" {
  count = var.proxied ? 0 : 1

  name              = "/aws/lambda/${local.name}-waker"
  retention_in_days = local.log_retention_days
}

resource "aws_lambda_function" "waker" {
  count = var.proxied ? 0 : 1

  function_name    = "${local.name}-waker"
  role             = data.aws_ssm_parameter.scaler_role_arn.value
  handler          = "index.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.waker.output_path
  source_code_hash = data.archive_file.waker.output_base64sha256
  timeout          = 15
  memory_size      = 256

  # The app's cost kill-switch. waker_enabled = false sets reserved
  # concurrency to 0, so the ALB can never invoke this function and nothing
  # can scale the service up -- visitors get an ALB error page instead of a
  # cold start. Flip the tfvar back to true and re-apply to re-enable.
  # -1 is the provider's "unreserved" default.
  #
  # Orthogonal to var.proxied: waker_enabled disables a waker that still
  # exists, proxied stops creating one.
  reserved_concurrent_executions = var.waker_enabled ? -1 : 0

  environment {
    variables = {
      CLUSTER        = data.aws_ssm_parameter.ecs_cluster_name.value
      SERVICE        = aws_ecs_service.this.name
      RULE_ARN       = aws_lb_listener_rule.this[0].arn
      ECS_TG_ARN     = aws_lb_target_group.ecs[0].arn
      APP_LABEL      = var.app_label
      COGNITO_CONFIG = jsonencode(local.cognito_config)
    }
  }

  depends_on = [aws_cloudwatch_log_group.waker]
}

resource "aws_lambda_permission" "alb_invoke_waker" {
  count = var.proxied ? 0 : 1

  statement_id  = "AllowALBInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.waker[0].function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.waker[0].arn
}

# --- Sleeper: idle scale-down and business-hours pre-warm -------------------

resource "aws_cloudwatch_log_group" "sleeper" {
  count = var.proxied ? 0 : 1

  name              = "/aws/lambda/${local.name}-sleeper"
  retention_in_days = local.log_retention_days
}

resource "aws_lambda_function" "sleeper" {
  count = var.proxied ? 0 : 1

  function_name    = "${local.name}-sleeper"
  role             = data.aws_ssm_parameter.scaler_role_arn.value
  handler          = "index.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.sleeper.output_path
  source_code_hash = data.archive_file.sleeper.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      APP_NAME           = var.app_key
      CLUSTER            = data.aws_ssm_parameter.ecs_cluster_name.value
      SERVICE            = aws_ecs_service.this.name
      RULE_ARN           = aws_lb_listener_rule.this[0].arn
      ECS_TG_ARN         = aws_lb_target_group.ecs[0].arn
      WAKER_TG_ARN       = aws_lb_target_group.waker[0].arn
      TG_DIMENSION       = local.ecs_tg_dimension
      ALB_DIMENSION      = data.aws_ssm_parameter.alb_dimension.value
      IDLE_MINUTES       = tostring(var.idle_minutes)
      MIN_UPTIME_MINUTES = tostring(var.min_uptime_minutes)
      WARM_ENABLED       = var.warm_enabled ? "true" : "false"
      WARM_DAYS          = jsonencode(var.warm_days)
      WARM_START_UTC     = tostring(var.warm_start_utc)
      WARM_END_UTC       = tostring(var.warm_end_utc)
      COGNITO_CONFIG     = jsonencode(local.cognito_config)
    }
  }

  depends_on = [aws_cloudwatch_log_group.sleeper]
}

resource "aws_cloudwatch_event_rule" "sleeper" {
  count = var.proxied ? 0 : 1

  name                = "${local.name}-sleeper"
  description         = "Idle check and pre-warm for ${var.app_label}"
  schedule_expression = "rate(${var.sleeper_interval_minutes} minutes)"
}

resource "aws_cloudwatch_event_target" "sleeper" {
  count = var.proxied ? 0 : 1

  rule      = aws_cloudwatch_event_rule.sleeper[0].name
  target_id = "sleeper"
  arn       = aws_lambda_function.sleeper[0].arn
}

resource "aws_lambda_permission" "events_invoke_sleeper" {
  count = var.proxied ? 0 : 1

  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sleeper[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sleeper[0].arn
}
