# ---------------------------------------------------------------------------
# Portal P2a: the shared image builder.
#
# ONE CodeBuild project for every app, not one per app. The project is a
# template -- everything app-specific (which zip, which repo, which tag)
# arrives as an environment override on StartBuild, so creating an app needs
# no Terraform run (ADR-0012). docs/design/portal-p2a.md, "The pipeline".
#
# The portal never runs Docker itself. It cannot: it is a 0.25 vCPU Fargate
# task with no privileged mode and no docker socket, and giving it one would
# be a container-escape surface in the request path for every app. CodeBuild
# is the sandbox -- separate account-level service, separate role, and its
# blast radius is "can push images to shiny-* repos", nothing more.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "build" {
  name = "/aws/codebuild/${var.project}-app-build"

  # Fixed at 30 days rather than local.log_retention_days (the platform-wide
  # default from SSM). Build logs are the only record of WHY an app failed to
  # build, and the failure gets looked at days later when someone asks "why
  # is my app still red" -- a shorter platform default would throw that away.
  retention_in_days = 30
}

# ---------------------------------------------------------------------------
# The build service role.
#
# Separate from the proxy task role on purpose: this one can push images, and
# the proxy must not be able to. If the proxy could push to shiny-* it could
# overwrite a live app's `:latest` with anything it liked. The split means
# a compromised portal can only ASK for a build (StartBuild) -- and the build
# then executes a buildspec that Terraform owns, from a repo file, not
# anything the caller supplied.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "build_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "build" {
  name               = "${var.project}-app-build"
  assume_role_policy = data.aws_iam_policy_document.build_assume.json
}

data "aws_iam_policy_document" "build" {
  # --- Read the uploaded zip ----------------------------------------------
  #
  # GetObject on the uploads bucket, and nothing else in S3. The build does
  # not need to list the bucket (the portal hands it an exact ZIP_KEY) and it
  # must never be able to write there -- a build that can rewrite its own
  # input is a build whose provenance means nothing.
  statement {
    sid       = "ReadUploadedBundle"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/*"]
  }

  # --- Its own logs --------------------------------------------------------
  #
  # Scoped to the one group above, not `*`. The portal tails these logs to
  # show the build screen's failure output (iam.tf, sid TailBuildLogs).
  statement {
    sid    = "WriteBuildLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      aws_cloudwatch_log_group.build.arn,
      "${aws_cloudwatch_log_group.build.arn}:*",
    ]
  }

  # --- ECR login -----------------------------------------------------------
  #
  # Resource MUST be "*" here. ecr:GetAuthorizationToken is an account-level
  # API that takes no resource at all -- it returns a registry credential, it
  # does not act on a repository. Writing a repository ARN in this statement
  # does not tighten anything; it just makes `docker login` fail with an
  # AccessDenied that reads like a repository permissions problem and sends
  # you looking in entirely the wrong place. The real fence is the next
  # statement.
  statement {
    sid       = "EcrLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # --- ECR push/pull, shiny-* repositories only ----------------------------
  #
  # This is the statement that matters. The build runs a Dockerfile derived
  # from user-supplied app code; if that code could reach `docker push` for an
  # arbitrary repository it could poison any image in the account. Pinned to
  # the platform's own prefix.
  statement {
    sid    = "EcrPushToPlatformRepos"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [
      "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${var.project}-*",
    ]
  }
}

resource "aws_iam_role_policy" "build" {
  name   = "${var.project}-app-build"
  role   = aws_iam_role.build.id
  policy = data.aws_iam_policy_document.build.json
}

# ---------------------------------------------------------------------------
# The project itself.
#
# NO_SOURCE + an inline buildspec read from the repo. The alternative --
# pointing CodeBuild at a git source -- would mean giving it a source
# credential and would make "what did this build actually run" a question
# about a branch state rather than about this commit. With NO_SOURCE the
# buildspec is baked into the project by `terraform apply`, so the only way
# to change what a build does is a reviewed commit plus an apply.
#
# The buildspec lives in proxy-app/buildspec/ (owned by the proxy-app side of
# P2a, not by this stack) and does the work described in portal-p2a.md "The
# build": download and unzip the bundle, refuse zip-slip, render the pinned
# rocker/P3M Dockerfile template, build, push shiny-<key>:r<N> and :latest.
# ---------------------------------------------------------------------------

resource "aws_codebuild_project" "app_build" {
  name         = "${var.project}-app-build"
  description  = "Builds a portal-uploaded R Shiny bundle into shiny-<key>:r<N>. One shared project; app-specific values arrive as StartBuild environment overrides."
  service_role = aws_iam_role.build.arn

  # Minutes, both. A first R image build is 10-20 (package installs dominate,
  # portal-p2a.md "The build"), so 30 leaves headroom without letting a wedged
  # build burn an hour of billable minutes. queued_timeout only bites if
  # concurrent builds pile up, which needs several apps created at once.
  build_timeout  = 30
  queued_timeout = 60

  artifacts {
    # The artifact is the ECR image, which the buildspec pushes itself.
    # CodeBuild has nothing to hand back.
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type = "BUILD_GENERAL1_SMALL"
    image        = "aws/codebuild/standard:7.0"
    type         = "LINUX_CONTAINER"

    # Required to run a Docker daemon inside the build container. This is the
    # whole reason CodeBuild exists in this design: it is the one place in the
    # platform allowed to be privileged, and it is not in any request path.
    privileged_mode = true

    image_pull_credentials_type = "CODEBUILD"

    # --- Contract with proxy-app/buildspec/buildspec.yml -------------------
    #
    # These names ARE the interface between this stack and the buildspec, and
    # they are enforced on the other side: render.py has a REQUIRED tuple of
    # exactly APP_KEY, ZIP_KEY, RELEASE_TAG, UPLOADS_BUCKET, ECR_REPO_URI,
    # PACKAGES and fails in pre_build naming the missing one. Rename a
    # variable here without renaming it there and every build dies -- fast,
    # at least, rather than ten minutes in.
    #
    # Two static values below (the bucket, the asset prefix) and four
    # placeholders that StartBuild overrides per build
    # (environmentVariablesOverride). The placeholders are declared here
    # anyway: the project then documents its own contract in the console, and
    # a hand-run test build fails on an obviously-bogus value instead of on
    # an empty string.
    #
    # The portal reads results back out of exported-variables via
    # codebuild:BatchGetBuilds -- IMAGE_URI, IMAGE_DIGEST, BUILD_RESULT,
    # BUILD_ERROR -- so it never has to scrape the log to find out what
    # happened. Nothing to declare here for those; the buildspec exports them.

    environment_variable {
      name  = "UPLOADS_BUCKET"
      value = aws_s3_bucket.uploads.id
    }

    # Where the buildspec finds Dockerfile.template / validate.py / render.py.
    # Under NO_SOURCE there is no checkout, so those three are mirrored into
    # the uploads bucket by aws_s3_object.pipeline_assets below. This value
    # and those object keys derive from the same local so they cannot drift.
    environment_variable {
      name  = "PIPELINE_PREFIX"
      value = local.pipeline_prefix
    }

    # App slug, e.g. "acme-forecast". render.py cross-checks it against
    # ECR_REPO_URI's repository name, so the two must agree.
    environment_variable {
      name  = "APP_KEY"
      value = "OVERRIDDEN_PER_BUILD"
    }

    # S3 object key of the uploaded zip inside UPLOADS_BUCKET.
    environment_variable {
      name  = "ZIP_KEY"
      value = "OVERRIDDEN_PER_BUILD"
    }

    # Immutable release tag, e.g. "r1". The buildspec pushes this AND :latest.
    environment_variable {
      name  = "RELEASE_TAG"
      value = "OVERRIDDEN_PER_BUILD"
    }

    # Full repository URI, <acct>.dkr.ecr.<region>.amazonaws.com/shiny-<key>.
    # The buildspec derives the registry to `docker login` to by trimming
    # everything after the first slash, so this must be the URI, not just the
    # repository name.
    environment_variable {
      name  = "ECR_REPO_URI"
      value = "OVERRIDDEN_PER_BUILD"
    }

    # The confirmed R package list from the wizard (renv.lock, packages.txt,
    # or the scanned library() calls -- portal-p2a.md "Packages: accept
    # either, confirm always"). Recorded on the release so a rebuild is
    # reproducible.
    environment_variable {
      name  = "PACKAGES"
      value = "OVERRIDDEN_PER_BUILD"
    }
  }

  source {
    type = "NO_SOURCE"

    # Read at plan time from the repo, not fetched at build time. If this
    # errors with "no file exists at ../proxy-app/buildspec/buildspec.yml",
    # the buildspec side of P2a has not landed yet -- that file is owned by
    # proxy-app/, not by this stack.
    buildspec = file("${path.module}/../proxy-app/buildspec/buildspec.yml")
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.build.name
      # stream_name unset: CodeBuild then names each stream after the build
      # id, which is exactly the handle the portal stores on the release row
      # and uses to tail logs for the build screen.
    }

    s3_logs {
      status = "DISABLED"
    }
  }
}

# ---------------------------------------------------------------------------
# Pipeline assets: the three helper files the buildspec runs.
#
# NO_SOURCE means the build container starts with no checkout of this repo,
# so Dockerfile.template, validate.py and render.py have to reach it somehow.
# The buildspec's contract is: look next to CODEBUILD_SRC_DIR first, and
# otherwise `aws s3 cp` them out of s3://$UPLOADS_BUCKET/$PIPELINE_PREFIX --
# "Terraform should mirror proxy-app/buildspec/ there", in its own words.
# This is that mirror. Without it every build dies in pre_build with
# "pipeline asset missing".
#
# etag = filemd5(...) is what makes an edit to any of those files show up as
# a plan diff. Drop it and Terraform uploads once and never notices again,
# which means the build silently keeps running last month's validate.py --
# a genuinely nasty class of bug, because the file in the repo looks right.
#
# These objects live under a `_pipeline/` prefix in the uploads bucket, which
# means the bucket's 7-day expiry applies to them too. That is fine and
# deliberate: they are re-uploaded on every apply, and if the platform has
# not been applied in over a week the right failure is a loud "asset missing"
# rather than a build running stale code. (If that ever becomes annoying,
# the fix is a lifecycle filter excluding this prefix, not turning off the
# expiry.)
# ---------------------------------------------------------------------------

locals {
  pipeline_prefix = "_pipeline/"
  pipeline_assets = ["Dockerfile.template", "validate.py", "render.py"]
}

resource "aws_s3_object" "pipeline_assets" {
  for_each = toset(local.pipeline_assets)

  bucket = aws_s3_bucket.uploads.id
  key    = "${local.pipeline_prefix}${each.value}"
  source = "${path.module}/../proxy-app/buildspec/${each.value}"
  etag   = filemd5("${path.module}/../proxy-app/buildspec/${each.value}")
}
