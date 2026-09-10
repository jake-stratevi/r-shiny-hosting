# ---------------------------------------------------------------------------
# The contract between this stack and the app stacks.
#
# App stacks read these with data "aws_ssm_parameter", which means they do NOT
# need access to this stack's Terraform state. Deploy platform once, then
# dashboard and model independently, in either order, by different people.
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
    #   portal/     The ADR-0013 Lambda portal (rule 50) read the OLD values
    #               at its last apply and still works, because its client
    #               lives in the Hub pool and nothing here touches it. It
    #               MUST NOT BE RE-APPLIED before it is retired: an apply
    #               would recreate its client against the new pool, and it
    #               would then serve "Login pages unavailable" until someone
    #               ran the branding CLI. portal.md retires this stack
    #               anyway; let it die rather than migrating it.
    #
    #   model/      Same trap, same rule: proxied = false, service off, last
    #               applied against the Hub pool. Do not re-apply it; migrate
    #               it to the proxy (proxied = true, its own client gone)
    #               when its image lands.
    #
    #   dashboard/  Already migrated to the proxy, no client of its own, so
    #               it reads none of this.
    # ----------------------------------------------------------------------
    cognito_user_pool_id  = aws_cognito_user_pool.this.id
    cognito_user_pool_arn = local.cognito_user_pool_arn
    cognito_domain        = aws_cognito_user_pool_domain.this.domain

    # CONTRACT NOTE -- why this is "COGNITO" and not a "none" sentinel.
    #
    # proxy/cognito.tf does, verbatim:
    #   supported_identity_providers = ["COGNITO", <this value>]
    # with no filtering. Cognito rejects any provider name that does not
    # exist in the pool, so exporting "none" -- or "" -- FAILS client
    # creation against a pool with no federation, and the fix would have to
    # live in proxy/, which this change is not allowed to touch.
    #
    # "COGNITO" is a real, always-valid value naming the pool's own native
    # directory, so the proxy client is created unchanged and correctly: the
    # only sign-in method offered is exactly the only one that works. The
    # list becomes ["COGNITO", "COGNITO"]; the API accepts it. If that ever
    # shows up as a permanent one-line diff on the proxy plan, the fix is a
    # one-word change over there -- wrap the list in distinct() -- not a
    # different value here.
    #
    # Entra federation into the new pool is a LATER, OPTIONAL step. When it
    # happens, set var.cognito_staff_idp_name to the provider's name and
    # re-apply; consumers pick it up on their next apply with no code change.
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
