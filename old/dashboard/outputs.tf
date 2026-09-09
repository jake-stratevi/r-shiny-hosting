output "url" {
  value = "https://${local.fqdn}"
  sensitive = true
}

output "ecr_repository" {
  value = aws_ecr_repository.this.repository_url
}

output "docker_push_commands" {
  description = "Build and push this app's image."
  sensitive = true
  value = join("\n", [
    "aws ecr get-login-password --region ${var.region} | docker login --username AWS --password-stdin ${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com",
    "docker build --platform linux/amd64 -t ${aws_ecr_repository.this.repository_url}:${var.image_tag} .",
    "docker push ${aws_ecr_repository.this.repository_url}:${var.image_tag}",
    "aws ecs update-service --cluster ${data.aws_ssm_parameter.ecs_cluster_name.value} --service ${aws_ecs_service.this.name} --force-new-deployment",
  ])
}

output "ecs_service_name" {
  value = aws_ecs_service.this.name
}

output "task_size" {
  value = "${var.cpu / 1024} vCPU / ${var.memory / 1024} GB"
}

output "cost_per_awake_hour_usd" {
  description = "Fargate: $0.04048/vCPU-hr + $0.004445/GB-hr. Multiply by awake hours from the CloudWatch dashboard."
  value       = format("%.4f", (var.cpu / 1024) * 0.04048 + (var.memory / 1024) * 0.004445)
}

output "cost_if_never_slept_usd" {
  description = "What this app would cost at 730 hrs. The gap between this and your actual bill is what scale-to-zero saves."
  value       = format("%.2f", ((var.cpu / 1024) * 0.04048 + (var.memory / 1024) * 0.004445) * 730)
}
