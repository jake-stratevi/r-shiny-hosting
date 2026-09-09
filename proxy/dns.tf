# ---------------------------------------------------------------------------
# The wildcard alias. Every current app keeps its own literal Route 53 record
# from its own dns.tf (dashboard.tools.stratevi.com, model.tools.stratevi.com)
# -- Route 53 resolves an exact match ahead of a wildcard, so those keep
# working untouched. This record exists so any OTHER *.tools.stratevi.com
# host (the proxy's own host, and every future app once it stops getting its
# own dns.tf) resolves to the same ALB. The platform stack already issued a
# wildcard cert for *.<domain>, so no ACM work is needed here either.
# ---------------------------------------------------------------------------
resource "aws_route53_record" "wildcard" {
  zone_id = data.aws_ssm_parameter.route53_zone_id.value
  name    = local.wildcard_fqdn
  type    = "A"

  alias {
    name                   = data.aws_ssm_parameter.alb_dns_name.value
    zone_id                = data.aws_ssm_parameter.alb_zone_id.value
    evaluate_target_health = false
  }
}
