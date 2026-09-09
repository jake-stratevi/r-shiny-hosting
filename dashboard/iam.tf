# ---------------------------------------------------------------------------
# This app's own task role. See docs/adr/0010-per-app-iam-roles.md.
#
# A Shiny app executes arbitrary R with these credentials, so a shared role
# means any app can reach whatever every other app can reach. This role is the
# blast radius of THIS app and nothing else -- when data moves out of the
# container image, the S3 grant goes here, scoped to this app's prefix.
#
# The EXECUTION role stays shared in the platform stack: it only pulls the
# image and writes logs, which is identical for every app and carries no app
# code.
#
# Apply order matters. This stack creates the role and repoints its own task
# definition; the platform stack's shared `task` role can only be removed once
# BOTH dashboard and model have been applied with roles of their own.
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
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# ECS Exec, so you can shell into a running task to debug R without SSH.
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
