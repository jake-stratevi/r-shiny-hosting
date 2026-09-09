# ---------------------------------------------------------------------------
# The proxy's own task role. Unlike the app stacks (ADR-0010), this is the
# ONLY task role in the request path for every app, so keep it as narrow as
# the spec allows: ECS control-plane calls scoped to this one cluster, and
# DynamoDB item/query calls scoped to the two proxy tables.
#
# Execution role stays the shared platform one (data.tf) -- it only pulls the
# image and writes logs, identical to every other stack.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "shiny-proxy-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# --- ECS control plane: wake apps, discover their task IPs ------------------
#
# Condition shape copied from platform/iam.tf's scaler policy (the waker /
# sleeper Lambdas' role) -- same "ArnEquals ecs:cluster" scoping, so the proxy
# can only ever act inside shiny-cluster, never any other cluster in the
# account.

data "aws_iam_policy_document" "task" {
  statement {
    sid    = "ScaleAndDiscoverApps"
    effect = "Allow"
    actions = [
      "ecs:DescribeServices",
      "ecs:UpdateService",
      "ecs:ListTasks",
      "ecs:DescribeTasks",
    ]
    resources = ["*"]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [data.aws_ssm_parameter.ecs_cluster_arn.value]
    }
  }

  statement {
    sid    = "Entitlements"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.apps.arn,
      "${aws_dynamodb_table.apps.arn}/index/*",
      aws_dynamodb_table.audit.arn,
      "${aws_dynamodb_table.audit.arn}/index/*",
    ]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "shiny-proxy-task"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ECS Exec, so you can shell into a running proxy task to debug without SSH --
# same block as every app stack's iam.tf.
data "aws_iam_policy_document" "task_exec_channel" {
  statement {
    effect = "Allow"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task_exec_channel" {
  name   = "ecs-exec"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_exec_channel.json
}
