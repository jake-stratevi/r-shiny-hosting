variable "region" {
  description = "AWS region. Must match every other stack -- the bucket has no cross-region replication and each stack's backend block hardcodes this same region."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Tagging only, for Cost Explorer grouping alongside every other stack. Not used in the bucket name -- the bucket name is pinned to the account ID so it matches iam/shiny-platform-deploy-policy.json exactly."
  type        = string
  default     = "shiny"
}
