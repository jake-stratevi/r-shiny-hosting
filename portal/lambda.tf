# ---------------------------------------------------------------------------
# The catalog is authored once, at the repo root (catalog.yaml). Terraform
# derives the JSON copy the Lambda bundles, so there is exactly one file a
# human edits -- not a second hand-kept copy inside lambda/portal/.
# ---------------------------------------------------------------------------

resource "local_file" "catalog_json" {
  filename = "${path.module}/lambda/portal/catalog.json"
  content  = jsonencode(local.catalog)
}

data "archive_file" "this" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/portal"
  output_path = "${path.module}/.build/portal.zip"

  depends_on = [local_file.catalog_json]
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = local.log_retention_days
}

resource "aws_lambda_function" "this" {
  function_name    = local.name
  role             = aws_iam_role.lambda.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256
  timeout          = 10
  memory_size      = 128

  depends_on = [aws_cloudwatch_log_group.this]
}

resource "aws_lambda_permission" "alb_invoke" {
  statement_id  = "AllowALBInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.this.arn
}
