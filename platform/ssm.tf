# ---------------------------------------------------------------------------
# The contract between this stack and the app stacks.
#
# Consumers read these with data "aws_ssm_parameter", which means they do NOT
# need access to this stack's Terraform state. Deploy platform once, then
# proxy (and any app stack) independently, by different people.
#
# Parameters are Standard tier: free.
# ---------------------------------------------------------------------------

locals {
  exports = {
    vpc_id                  = aws_vpc.this.id
    task_security_group_id  = aws_security_group.tasks.id
    alb_security_group_id   = aws_security_group.alb.id
    alb_arn                 = aws_lb.this.arn
    alb_dns_name            = aws_lb.this.dns_name
    alb_zone_id             = aws_lb.this.zone_id
    alb_dimension           = local.alb_dimension
    https_listener_arn      = aws_lb_listener.https.arn
    ecs_cluster_name        = aws_ecs_cluster.this.name
    ecs_cluster_arn         = aws_ecs_cluster.this.arn
    task_execution_role_arn = aws_iam_role.task_execution.arn

    # UNUSED since 2026-09-11: the waker/sleeper Lambdas that assumed this
    # role are gone (ADR-0002's mechanism; the proxy does the scaling now).
    # Kept because removing it is a plan diff for no benefit -- delete both
    # this and aws_iam_role.scaler next time this stack is touched anyway.
    scaler_role_arn = aws_iam_role.scaler.arn

    # ----------------------------------------------------------------------
    # These three now point at the pool THIS STACK OWNS (cognito.tf,
    # ADR-0015), not at the Assembled Hub pool. Applying this stack REPOINTS
    # every consumer at a directory in which no app client exists yet.
    #
    # Consumers, and what happens to each:
    #
    #   proxy/      Reads all four on every apply. Its shared app client is
    #               REPLACED into the new pool on the next proxy apply --
    #               which means a new client id/secret on the ALB rule, a
    #               forced re-login for everyone, and a mandatory
    #               create-managed-login-branding call for the new client
    #               (see the banner in cognito.tf). Expected and intended.
    #
    #   portal/     GONE. The ADR-0013 Lambda portal was destroyed
    #               2026-09-10; the React portal on the proxy replaced it.
    #
    #   model/      GONE. Destroyed 2026-09-11 rather than migrated -- that
    #               app is now `microsimulation-model`, created through the
    #               portal. Its stale client in the Hub pool went with it.
    #
    #   dashboard/  Already migrated to the proxy, no client of its own, so
    #               it reads none of this.
    # ----------------------------------------------------------------------
    cognito_user_pool_id  = aws_cognito_user_pool.this.id
    cognito_user_pool_arn = local.cognito_user_pool_arn
    cognito_domain        = aws_cognito_user_pool_domain.this.domain

    # The IdP name consumers put into supported_identity_providers. It is
    # "Microsoft365" (the Entra federation, added by hand 2026-09-10), NOT
    # the "COGNITO" placeholder this briefly held -- while it was the
    # placeholder, a proxy apply would have stripped the Microsoft button
    # off the login page and locked every federated user out. proxy/cognito.tf
    # also carries ignore_changes on supported_identity_providers now, so a
    # stale value here can no longer do that damage on its own. Both belts
    # are deliberate; see docs/GOTCHAS.md.
    oidc_provider_name = var.cognito_staff_idp_name

    route53_zone_id    = var.route53_zone_id
    domain_name        = var.domain_name
    log_retention_days = tostring(var.log_retention_days)
  }
}

resource "aws_ssm_parameter" "export" {
  for_each = local.exports

  name  = "${local.ssm}/${each.key}"
  type  = "String"
  value = each.value
}

resource "aws_ssm_parameter" "subnet_ids" {
  name  = "${local.ssm}/subnet_ids"
  type  = "StringList"
  value = join(",", aws_subnet.public[*].id)
}
