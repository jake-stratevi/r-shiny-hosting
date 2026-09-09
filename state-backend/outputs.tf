output "bucket_name" {
  description = "Paste into the backend \"s3\" block (bucket = ...) of every stack's versions.tf."
  value       = aws_s3_bucket.tf_state.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.tf_state.arn
}
