output "ssm_namespace" {
  description = "App stacks read their wiring from here. Pass the project name to each app stack and it finds the rest."
  value       = local.ssm
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "cognito_user_pool_id" {
  description = "Invite users: aws cognito-idp admin-create-user --user-pool-id <id> --username someone@client.com --user-attributes Name=email,Value=... Name=email_verified,Value=true"
  value       = aws_cognito_user_pool.this.id
}

output "cognito_hosted_ui" {
  value = "https://${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com"
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "estimated_fixed_monthly_usd" {
  description = "What this stack costs whether or not any app is awake."
  value = {
    alb_base      = 16.43
    alb_lcu_light = 3.00
    route53_zone  = 0.50
    acm           = 0.00
    cognito       = 0.00
    ssm_standard  = 0.00
    total         = 19.93
  }
}
