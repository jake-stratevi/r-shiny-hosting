output "url" {
  value     = "https://${local.proxy_fqdn}"
  sensitive = true
}

output "ecr_repository" {
  value = aws_ecr_repository.this.repository_url
}

output "apps_table_name" {
  value = aws_dynamodb_table.apps.name
}

output "audit_table_name" {
  value = aws_dynamodb_table.audit.name
}

output "ecs_service_name" {
  value = aws_ecs_service.this.name
}

# --- P2a pipeline ------------------------------------------------------------
#
# Handy for a manual `aws codebuild start-build` while testing the buildspec
# against the existing dashboard bundle (portal-p2a.md phasing step 2), before
# any UI exists to drive it.

output "uploads_bucket_name" {
  value = aws_s3_bucket.uploads.id
}

output "codebuild_project_name" {
  value = aws_codebuild_project.app_build.name
}

output "app_role_boundary_arn" {
  description = "Every portal-created shiny-app-* role must be created with this as its permissions boundary, or iam:CreateRole is denied. See proxy/boundary.tf."
  value       = aws_iam_policy.app_boundary.arn
}

output "docker_push_commands" {
  description = "Build and push the proxy image."
  sensitive   = true
  value = join("\n", [
    "aws ecr get-login-password --region ${var.region} | docker login --username AWS --password-stdin ${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com",
    "docker build --platform linux/amd64 -t ${aws_ecr_repository.this.repository_url}:${var.image_tag} .",
    "docker push ${aws_ecr_repository.this.repository_url}:${var.image_tag}",
    "aws ecs update-service --cluster ${data.aws_ssm_parameter.ecs_cluster_name.value} --service ${aws_ecs_service.this.name} --force-new-deployment",
  ])
}
