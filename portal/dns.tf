# The platform stack issued a wildcard cert for *.<domain>, so this stack
# needs only an alias record -- no ACM of its own. See ADR-0005.
resource "aws_route53_record" "this" {
  zone_id = data.aws_ssm_parameter.route53_zone_id.value
  name    = local.fqdn
  type    = "A"

  alias {
    name                   = data.aws_ssm_parameter.alb_dns_name.value
    zone_id                = data.aws_ssm_parameter.alb_zone_id.value
    evaluate_target_health = false
  }
}
