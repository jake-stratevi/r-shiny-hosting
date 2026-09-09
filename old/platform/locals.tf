locals {
  name = var.project
  ssm  = "/${var.project}/platform"

  # CloudWatch dimension values are ARN suffixes, not full ARNs.
  # arn:aws:elasticloadbalancing:<region>:<acct>:loadbalancer/app/<name>/<id>
  alb_dimension = replace(element(split(":", aws_lb.this.arn), 5), "loadbalancer/", "")
}
