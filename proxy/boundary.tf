# ---------------------------------------------------------------------------
# THE FENCE. Read this before changing anything in here.
#
# Threat model, stated plainly: the portal is a web service that can call
# iam:CreateRole and iam:PutRolePolicy. A web service that can mint IAM roles
# is a privilege-escalation engine. An attacker who gets code execution in the
# proxy task -- through a dependency, a request-handling bug, or a malicious
# R app that somehow reaches the proxy's credentials -- would otherwise do
# this: create a role, attach AdministratorAccess to it as an inline policy,
# assume it, own the account. Every grant in iam.tf's IAM statements is
# worthless without this file.
#
# The defence is a permissions boundary. Every role the portal creates MUST
# carry this policy as its boundary (enforced by the iam:PermissionsBoundary
# condition on the portal's iam:CreateRole grant -- see iam.tf, sid
# CreateAppRolesOnlyWithBoundary). A boundary caps EFFECTIVE permissions to
# the intersection of the role's own policies and this one. So the attacker
# above still creates a role, still writes AdministratorAccess into it, and
# the role still cannot do anything outside the three things below. The
# escalation dead-ends.
#
# What the portal cannot do, and why each matters:
#   - create roles outside the `shiny-app-*` prefix   (resource scope)
#   - create a role without this boundary             (condition)
#   - attach MANAGED policies to a role               (never granted; managed
#     policies are how you'd bolt on AdministratorAccess in one call)
#   - change, version, or detach this boundary        (never granted, and
#     explicitly denied in iam.tf's NoBoundaryTampering statement)
#
# Changing this policy therefore requires a Terraform apply by a human with
# the deploy credentials. That is the point. Widening it widens every app
# role in the platform at once, including ones created months ago.
#
# WHY THIS IS BROAD WHERE THE ROLES ARE NARROW
# --------------------------------------------
# The S3 statement below is `shiny-app-data-<acct>/*` -- every app's prefix,
# not one app's. That looks wrong for about ten seconds and then doesn't:
#
#   A boundary is a CEILING, not a GRANT.
#
# One boundary policy is shared by every `shiny-app-*` role, so it has to be
# the union of what any app role might legitimately need. It gives nothing on
# its own -- a role with this boundary and no inline policy has zero
# permissions. What actually confines app A to its own data is the INLINE
# policy the portal writes onto `shiny-app-A-task`, which says
# `shiny-app-data-<acct>/A/*` and nothing else (ADR-0010: per-app roles, each
# with only the permissions that app needs).
#
# So the two layers do different jobs:
#   inline policy (per-app, SDK-written) -> what this app may actually do
#   boundary      (shared, Terraform)    -> what ANY app role can ever do,
#                                           even if the inline policy is
#                                           attacker-controlled
#
# Narrowing the boundary per app would mean one managed policy per app, which
# hits the 1500-policies-per-account quota and, worse, would have to be
# created by the portal -- putting the fence inside the thing it fences.
# ---------------------------------------------------------------------------

locals {
  # The per-app data bucket. It does NOT exist yet -- moving app data out of
  # container images into S3 is a later change (ADR-0010 "Consequences", and
  # portal.md's deferred "per-app data-in-S3"). Naming it here now means the
  # boundary already permits the access pattern when the bucket lands, so
  # creating it does not require re-issuing every app role's boundary.
  # An ARN in a policy for a bucket that does not exist is legal and inert.
  app_data_bucket = "${var.project}-app-data-${data.aws_caller_identity.current.account_id}"

  app_role_prefix = "${var.project}-app-*"
}

data "aws_iam_policy_document" "app_boundary" {
  # --- ECS Exec ------------------------------------------------------------
  #
  # `aws ecs execute-command` into a running app task, to debug a Shiny app
  # that is misbehaving in Fargate. Same four actions as every task role in
  # this repo (see iam.tf's task_exec_channel). ssmmessages takes no resource
  # -- the session is scoped by the ECS side of the call, not by IAM ARNs.
  statement {
    sid    = "EcsExec"
    effect = "Allow"
    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }

  # --- CloudWatch Logs -----------------------------------------------------
  #
  # Write only, and only into this platform's log-group namespace. An app that
  # can write logs cannot read another app's logs: no logs:Get*/Filter* here.
  # Scoped to /ecs/<project>* so a compromised app role cannot scribble into
  # unrelated log groups in the account (log injection is a real way to
  # poison an audit trail).
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.project}*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.project}*:log-stream:*",
    ]
  }

  # --- Per-app data in S3 --------------------------------------------------
  #
  # Read only, and only inside the data bucket. GetObject, not ListBucket:
  # an app reads the objects it was told about, it does not get to enumerate
  # every other app's prefix. The `/*` here is the ceiling for ALL app roles
  # (see the banner) -- the per-app inline policy narrows it to that app's
  # own `<app-key>/` prefix, which is the boundary the client security review
  # actually cares about.
  #
  # Deliberately absent from this whole policy: s3:PutObject anywhere,
  # dynamodb:*, sts:AssumeRole, secretsmanager:*, ssm:GetParameter, ecr:*.
  # An app role cannot write to S3, cannot read the entitlements tables,
  # cannot pivot to another role, and cannot read platform secrets -- no
  # matter what inline policy is written onto it.
  statement {
    sid       = "ReadOwnAppData"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${local.app_data_bucket}/*"]
  }
}

resource "aws_iam_policy" "app_boundary" {
  name        = "${var.project}-app-boundary"
  description = "Permissions boundary for every portal-created shiny-app-* task role. Ceiling, not a grant. See proxy/boundary.tf before widening."
  policy      = data.aws_iam_policy_document.app_boundary.json
}
