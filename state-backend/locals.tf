locals {
  # Must exactly match the "RemoteStateOptional" resource ARNs in
  # iam/shiny-platform-deploy-policy.json: arn:aws:s3:::stratevi-tf-state-<account-id>.
  # That policy statement names this bucket literally, not as a prefix
  # pattern, so a typo or a different suffix here silently breaks under the
  # deployment IAM policy the moment any stack tries to use the backend.
  bucket_name = "stratevi-tf-state-${data.aws_caller_identity.current.account_id}"
}
