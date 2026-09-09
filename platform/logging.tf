# ---------------------------------------------------------------------------
# ALB access logs to S3.
#
# The ALB itself never sleeps, so this bucket is the only always-on storage
# cost the platform adds. Logs are tiny (one line per request, and traffic
# here is a handful of users) and short-lived -- 90-day expiration keeps
# storage in the low cents/month. No extra always-on infra: delivery is done
# by the ELB service directly writing to S3, not through Firehose or a
# Lambda.
# ---------------------------------------------------------------------------

# Bucket name must be globally unique across all of S3, not just this
# account, so the account ID is baked in rather than relying on the
# "shiny-alb" style short names used elsewhere in this stack.
resource "aws_s3_bucket" "alb_logs" {
  bucket = "${local.name}-alb-logs-${data.aws_caller_identity.current.account_id}"

  # This is a log bucket with a 90-day expiration, not a source of truth.
  # force_destroy so `terraform destroy` / teardown.ps1 doesn't get stuck on
  # a non-empty bucket.
  force_destroy = true
}

# ACLs are not used anywhere in the bucket policy below (see the comment on
# aws_s3_bucket_policy.alb_logs for why), so disable them outright. This is
# already the S3 default for newly created buckets, but pinning it avoids
# any drift and documents the intent.
resource "aws_s3_bucket_ownership_controls" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# These are access logs, not a public asset -- block every public-access
# vector S3 offers.
resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# NOTE: ALB access logs only support SSE-S3 (AES256). SSE-KMS with a CMK is
# not supported -- ELB does not have a mechanism to be granted access to a
# customer-managed key, and AWS documents SSE-S3 as the only supported
# option for ELB/ALB access log buckets. Don't "upgrade" this to a CMK.
resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Logs are operational exhaust, not an audit trail we need to retain --
# expire them well before storage cost becomes noticeable at this traffic
# level.
resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire-old-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = 90
    }

    # Belt-and-suspenders: clean up any multipart upload that never
    # completes so it doesn't sit around accruing storage cost forever.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---------------------------------------------------------------------------
# Bucket policy -- this is the part that's easy to get subtly wrong and have
# the ALB update fail at apply time with an opaque "Access Denied creating
# access logs bucket" error, so the shape here is taken directly from AWS's
# current documentation for ALB access logs
# (https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html#attach-bucket-policy),
# Step 2 "Attach a policy to your S3 bucket".
#
# AWS actually documents two shapes:
#
#   1. Current/recommended (all Regions activated after Aug 2022 REQUIRE
#      this; older Regions may also use it): principal is the service
#      "logdelivery.elasticloadbalancing.amazonaws.com".
#   2. Legacy (only valid in Regions that existed before Aug 2022 --
#      us-east-1 is one of them, and AWS still lists it as supported, not
#      deprecated): principal is the AWS-owned, per-Region ELB account as an
#      account root ARN. For us-east-1 that account is 127311923021.
#
# This uses shape 2 (legacy) because it's the one that matches the account
# ARN given for this task and is explicitly still supported for us-east-1 --
# but note it could be swapped for shape 1 (the service-principal form) if
# AWS ever deprecates it here. Neither shape needs an
# "s3:x-amz-acl": "bucket-owner-full-control" condition for ALB (that
# condition only appears in AWS's docs for the Outposts Zones variant, which
# doesn't apply to a normal VPC-hosted ALB like this one) -- which is also
# why ACLs are disabled above rather than half-configured.
data "aws_iam_policy_document" "alb_logs" {
  statement {
    sid    = "AllowELBAccountLogDelivery"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::127311923021:root"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.alb_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = data.aws_iam_policy_document.alb_logs.json
}
