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
  name              = "/aws/lambda/${local.name}-waker"
  retention_in_days = local.log_retention_days
}

resource "aws_lambda_function" "waker" {
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
  reserved_concurrent_executions = var.waker_enabled ? -1 : 0

  environment {
    variables = {
      CLUSTER        = data.aws_ssm_parameter.ecs_cluster_name.value
      SERVICE        = aws_ecs_service.this.name
      RULE_ARN       = aws_lb_listener_rule.this.arn
      ECS_TG_ARN     = aws_lb_target_group.ecs.arn
      APP_LABEL      = var.app_label
      COGNITO_CONFIG = jsonencode(local.cognito_config)
    }
  }

  depends_on = [aws_cloudwatch_log_group.waker]
}

resource "aws_lambda_permission" "alb_invoke_waker" {
  statement_id  = "AllowALBInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.waker.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.waker.arn
}

# --- Sleeper: idle scale-down and business-hours pre-warm -------------------

resource "aws_cloudwatch_log_group" "sleeper" {
  name              = "/aws/lambda/${local.name}-sleeper"
  retention_in_days = local.log_retention_days
}

resource "aws_lambda_function" "sleeper" {
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
      RULE_ARN           = aws_lb_listener_rule.this.arn
      ECS_TG_ARN         = aws_lb_target_group.ecs.arn
      WAKER_TG_ARN       = aws_lb_target_group.waker.arn
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
  name                = "${local.name}-sleeper"
  description         = "Idle check and pre-warm for ${var.app_label}"
  schedule_expression = "rate(${var.sleeper_interval_minutes} minutes)"
}

resource "aws_cloudwatch_event_target" "sleeper" {
  rule      = aws_cloudwatch_event_rule.sleeper.name
  target_id = "sleeper"
  arn       = aws_lambda_function.sleeper.arn
}

resource "aws_lambda_permission" "events_invoke_sleeper" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sleeper.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.sleeper.arn
}
