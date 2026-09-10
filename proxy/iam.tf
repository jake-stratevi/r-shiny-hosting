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

  # =========================================================================
  # PORTAL P2a -- self-service app creation.
  #
  # Everything below exists so the "+ New app" wizard can provision an app
  # without a Terraform run (ADR-0012). Each statement is the narrowest form
  # of one step in the provisioning order from docs/design/portal-p2a.md:
  #
  #   zip to S3 -> CodeBuild -> ECR -> IAM role -> task def -> service ->
  #   Cognito callback -> row flips to active
  #
  # The IAM statements at the bottom are the sensitive ones. Read
  # boundary.tf before touching them.
  # =========================================================================

  # --- Step: the wizard's upload -------------------------------------------
  #
  # PutObject only, and only into the uploads bucket. The portal does not
  # actually push bytes -- it hands the browser a presigned PUT (uploads.tf)
  # -- but presigning requires the signer to hold the permission it is
  # delegating, so this grant IS the upload permission. No GetObject: the
  # portal never reads a bundle back, CodeBuild does that with its own role.
  statement {
    sid       = "UploadAppBundles"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/*"]
  }

  # --- Step: start and poll the image build --------------------------------
  #
  # That one project, by ARN. Not codebuild:* and not "*" -- StartBuild on an
  # arbitrary project is arbitrary code execution with that project's role.
  # BatchGetBuilds is the polling call behind `GET /api/v1/apps/{host}/build`.
  statement {
    sid    = "RunAppBuilds"
    effect = "Allow"
    actions = [
      "codebuild:StartBuild",
      "codebuild:BatchGetBuilds",
    ]
    resources = [aws_codebuild_project.app_build.arn]
  }

  # --- Step: tail the build log for the build screen -----------------------
  #
  # Read-only, and only the build project's own group. This is what makes the
  # failure output visible in the UI instead of "check CloudWatch".
  statement {
    sid    = "TailBuildLogs"
    effect = "Allow"
    actions = [
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      aws_cloudwatch_log_group.build.arn,
      "${aws_cloudwatch_log_group.build.arn}:*",
    ]
  }

  # --- Step: the app's ECR repository --------------------------------------
  #
  # Create the repo and put its keep-5-images lifecycle policy on it, within
  # the shiny-* prefix. Note what is NOT here: no ecr:PutImage, no
  # ecr:DeleteRepository, no ecr:BatchDeleteImage. The portal creates the
  # box; only CodeBuild's role (codebuild.tf) may put images in it, and
  # nothing may delete a repository without a human.
  statement {
    sid    = "CreateAppRepositories"
    effect = "Allow"
    actions = [
      "ecr:CreateRepository",
      "ecr:DescribeRepositories",
      "ecr:PutLifecyclePolicy",
      "ecr:TagResource",
    ]
    resources = [
      "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${var.project}-*",
    ]
  }

  # --- Step: register the task definition ----------------------------------
  #
  # Resource MUST be "*". ecs:RegisterTaskDefinition takes no resource --
  # there is nothing to scope it to, because the task-definition ARN it would
  # be scoped against does not exist until the call succeeds. AWS's service
  # authorization reference confirms it supports neither resource-level
  # permissions in the useful sense nor the ecs:cluster condition key, so
  # writing an ARN or copying the ArnEquals condition from the statement
  # above simply denies every call.
  #
  # What actually contains this grant is PassRole (below): a task definition
  # is only dangerous because of the roles it names, and the portal can only
  # name shiny-app-* roles, which all carry the boundary.
  statement {
    sid       = "RegisterAppTaskDefinitions"
    effect    = "Allow"
    actions   = ["ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  # --- Step: create the service (desired 0, born proxied) ------------------
  #
  # Same ArnEquals ecs:cluster fence as ScaleAndDiscoverApps above, so a
  # created service can only ever land in shiny-cluster. DescribeServices and
  # UpdateService are already granted there and are deliberately not repeated
  # here -- one statement per fact.
  #
  # TagResource is here for a cost reason, not a cosmetic one: the platform
  # budget (platform/monitoring.tf) filters on `user:Project$shiny`, so a
  # service the portal creates WITHOUT that tag spends money invisibly to
  # the alerting. The portal must tag every resource it creates; this is the
  # grant that lets it. ECS applies create-time tags through CreateService
  # itself, but TagResource is needed for the ECR repo and for retagging.
  # GetRole is not decoration: after creating a role the portal reads it
  # back and compares the attached permissions boundary, deleting the role
  # if it does not match (provision.py, boundary invariant 4). Without this
  # grant that verification throws and EVERY create fails -- so the read is
  # granted deliberately, in the same statement as the writes it verifies.
  statement {
    sid    = "VerifyAppRoleBoundary"
    effect = "Allow"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
    ]
    resources = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/shiny-app-*"]
  }

  statement {
    sid       = "CreateAppServices"
    effect    = "Allow"
    actions   = ["ecs:CreateService", "ecs:TagResource"]
    resources = ["*"]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [data.aws_ssm_parameter.ecs_cluster_arn.value]
    }
  }

  # --- Step: register the new host's Cognito callback ----------------------
  #
  # The proxy uses ONE shared user pool client (cognito.tf) whose callback
  # list grows by one URL per app. Resource is the user pool ARN because
  # Cognito has no per-app-client ARN -- pool level is the tightest scope the
  # service offers. Update is read-modify-write, hence Describe: overwriting
  # the callback list without reading it first would unhook every existing
  # app, which is why the portal must read before it writes.
  statement {
    sid    = "ManageSharedCognitoClient"
    effect = "Allow"
    actions = [
      "cognito-idp:DescribeUserPoolClient",
      "cognito-idp:UpdateUserPoolClient",
    ]
    resources = [data.aws_ssm_parameter.cognito_user_pool_arn.value]
  }

  # =========================================================================
  # THE SPICY PART: per-app IAM roles, created by a web service.
  #
  # See boundary.tf for the full threat model. Short version: these grants
  # let the portal mint roles, and the ONLY reason that is acceptable is the
  # iam:PermissionsBoundary condition below. Delete the condition and you
  # have handed anyone who compromises the proxy a path to account admin.
  #
  # Verified against AWS's service authorization reference and the
  # "Delegating responsibility to others using permissions boundaries"
  # example in the IAM user guide:
  #
  #   - the key is spelled `iam:PermissionsBoundary` (plural "Permissions")
  #   - AWS's own delegation example uses StringEquals with it, matching the
  #     policy ARN exactly; ArnEquals also works, StringEquals is what the
  #     documented pattern uses so it is what we use
  #   - it is supported on CreateRole, PutRolePolicy, DeleteRolePolicy,
  #     AttachRolePolicy, DetachRolePolicy, PutRolePermissionsBoundary
  #   - it is NOT supported on iam:TagRole or iam:DeleteRole -- the key is
  #     simply absent from the request context for those two, so a
  #     StringEquals condition on them evaluates false and denies EVERY call.
  #     That is why they are in a separate statement below rather than in
  #     this one. Putting them here looks more secure and would silently
  #     break tagging and cleanup.
  # =========================================================================

  statement {
    sid    = "CreateAppRolesOnlyWithBoundary"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-app-*",
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.app_boundary.arn]
    }
  }

  # TagRole and DeleteRole cannot carry the boundary condition (see above).
  # They are fenced by the role-name prefix alone, which is sufficient
  # because neither can raise privilege: TagRole writes metadata, and
  # DeleteRole only destroys, and only a role that already has to be inside
  # shiny-app-* and therefore already had the boundary. The worst a
  # compromised portal does with these is vandalise its own apps -- noisy,
  # recoverable, and not an escalation.
  statement {
    sid    = "TagAndRetireAppRoles"
    effect = "Allow"
    actions = [
      "iam:TagRole",
      "iam:DeleteRole",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-app-*",
    ]
  }

  # Belt and braces, and cheap: an explicit Deny that survives someone later
  # widening an Allow by accident. Nothing below is granted anywhere in this
  # policy today, but these five actions are exactly the escape hatches --
  # attach a managed policy (AdministratorAccess in one call), or take the
  # boundary off a role, or edit the boundary policy itself. An explicit Deny
  # beats any Allow, including one added in a hurry six months from now.
  statement {
    sid    = "NoBoundaryTampering"
    effect = "Deny"
    actions = [
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PutRolePermissionsBoundary",
      "iam:DeleteRolePermissionsBoundary",
      "iam:CreatePolicyVersion",
    ]
    resources = ["*"]
  }

  # --- PassRole: the other half of the task-definition grant ---------------
  #
  # RegisterTaskDefinition names a task role; naming a role you may not pass
  # is how you would otherwise borrow one. `iam:PassedToService` (string
  # operator, StringEquals -- verified in the IAM condition-key reference,
  # and the same shape the deploy policy already uses in its
  # PassProjectRolesToEcsAndLambdaOnly statement) pins the destination, so
  # even within shiny-app-* the portal cannot pass a role to Lambda, EC2, or
  # anything else that would run code under it.
  statement {
    sid     = "PassAppRolesToEcsOnly"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-app-*",
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # Beyond the spec's list, and required for it to work at all: a task
  # definition names an EXECUTION role as well as a task role, and every app
  # shares the platform's one (data.tf, task_execution_role_arn -- it only
  # pulls images and writes logs, ADR-0010 keeps it shared). Without PassRole
  # on it, RegisterTaskDefinition fails with an opaque
  # "ECS was unable to assume the role" / AccessDenied on the very first
  # create. Same PassedToService fence; passing an image-pull role to ECS is
  # not an escalation.
  statement {
    sid       = "PassSharedExecutionRoleToEcs"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [data.aws_ssm_parameter.task_execution_role_arn.value]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
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
