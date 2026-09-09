# ---------------------------------------------------------------------------
# The one bucket every stack's remote state lives in (ADR-0009). One object
# per stack, key "shiny/<stack>.tfstate", set in each stack's versions.tf.
#
# Versioning is the actual point: it's what makes a corrupted or wrongly
# -applied state file recoverable (roll back to the previous version) instead
# of a total loss. Encryption and the public access block are baseline
# hygiene for a bucket that holds resource IDs, ARNs, and in places
# provider-marked-sensitive values. The lifecycle rule keeps that version
# history from growing forever -- state files are small, but they change on
# every apply, and 90 days of noncurrent versions is more than enough runway
# to notice a bad apply and recover from it.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "tf_state" {
  bucket = local.bucket_name

  # Deliberately no force_destroy. This bucket is the only durable record of
  # what exists in AWS across four stacks; destroying it should require
  # emptying it by hand first, not a single `terraform destroy`.
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Noncurrent versions (every prior version of every stack's state file) are
# not needed forever -- expire them well after any realistic "we need to roll
# back" window, not before it.
resource "aws_s3_bucket_lifecycle_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}
