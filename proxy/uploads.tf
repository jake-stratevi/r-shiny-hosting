# ---------------------------------------------------------------------------
# Portal P2a: the upload bucket the creation wizard drops app zips into.
#
# The zip is an INPUT, not an archive -- the ECR image is the artifact that
# gets kept. So: no versioning, and a 7-day expiry that quietly cleans up
# after every build (docs/design/portal-p2a.md, "Terraform additions").
#
# Naming is `shiny-portal-uploads-<acct>` so it falls under the deploy
# policy's existing `arn:aws:s3:::shiny-*` grant (iam/shiny-platform-deploy-
# policy.json, sid ProjectBuckets) -- no policy change needed to create it.
# ---------------------------------------------------------------------------

locals {
  uploads_bucket = "${var.project}-portal-uploads-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "uploads" {
  bucket = local.uploads_bucket

  # force_destroy stays false: a `terraform destroy` should not silently eat
  # an in-flight upload. Objects expire on their own in 7 days anyway.
  force_destroy = false
}

# No `aws_s3_bucket_versioning` resource here, deliberately. A new bucket is
# unversioned by default, which is what we want -- versioning on a bucket of
# disposable build inputs just means the 7-day expiry leaves noncurrent
# versions behind and you pay to store zips nobody will ever read again.
# If you add versioning later you must also add a
# noncurrent_version_expiration rule below, or the lifecycle stops working.

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  # SSE-S3 (AES256), not KMS. KMS would mean every presigned PUT the browser
  # makes needs kms:GenerateDataKey on the caller and every CodeBuild read
  # needs kms:Decrypt -- two more grants and two more ways for a 403 to show
  # up only in the browser. The zip is customer code we are about to run
  # anyway; at-rest encryption is the requirement, not key custody.
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  # Expire UPLOADED BUNDLES only -- scoped by prefix, deliberately.
  #
  # This rule used to have an empty filter (= the whole bucket), which also
  # swept the `_pipeline/` assets the buildspec downloads at the start of
  # every build (Dockerfile.template, validate.py, render.py). Those are
  # re-uploaded on each apply, so the effect was a hidden clock: eight days
  # without a `terraform apply` and EVERY app build dies in pre_build with
  # "pipeline asset missing" -- including a brand-new app's first build,
  # which is exactly when a non-engineer is watching it happen. Nothing else
  # on this platform needs a weekly apply to keep working, and a build
  # pipeline that rots on a timer is a worse failure than a stale one.
  rule {
    id     = "expire-uploaded-bundles"
    status = "Enabled"

    filter {
      prefix = "uploads/"
    }

    expiration {
      days = 7
    }
  }

  # Bucket-wide, and correctly so: a browser upload that dies halfway leaves
  # multipart parts that are invisible in the console object list and billed
  # forever. This is the one lifecycle rule every bucket should have and
  # almost none do.
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# ---------------------------------------------------------------------------
# CORS. The browser PUTs the zip STRAIGHT TO S3 with a presigned URL -- the
# bytes never pass through the proxy task (a 100 MB upload through a 0.25 vCPU
# Go proxy would be miserable, and API Gateway-style size limits would bite).
#
# That makes this a cross-origin request: the page is on
# shinyplatform.tools.stratevi.com, the PUT goes to s3.amazonaws.com. Without
# this rule the presigned URL is perfectly valid and the PUT still fails --
# but ONLY in a browser, and only with an opaque CORS error in the devtools
# console. curl with the same URL succeeds. That asymmetry is a genuinely
# miserable thing to debug, so: this block is load-bearing, do not drop it
# because "the presigned URL works when I test it".
#
# Origins are derived from local.portal_hosts (data.tf) rather than typed out,
# so renaming the portal hostname (as happened 2026-09-10, proxy ->
# shinyplatform) cannot leave a stale origin behind here.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  cors_rule {
    allowed_origins = [for host in local.portal_hosts : "https://${host}"]
    allowed_methods = ["PUT"]

    # A presigned PUT carries Content-Type and the SDK may add
    # x-amz-* headers; "*" here is the request-header allowlist for the
    # preflight, not a wildcard origin.
    allowed_headers = ["*"]

    # The upload screen reads the ETag back to confirm the object landed.
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}
