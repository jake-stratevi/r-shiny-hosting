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
