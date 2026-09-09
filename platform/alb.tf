# ---------------------------------------------------------------------------
# ONE ALB for every app. This is the only component that cannot scale to zero:
# $0.0225/hr base (~$16.43/mo) plus LCU charges. Sharing it is the entire
# reason the platform and app stacks are separated: a per-app ALB would add
# $16/mo per app for nothing.
#
# App stacks attach themselves by creating listener rules on the HTTPS listener
# with a host-header condition and their own priority.
# ---------------------------------------------------------------------------

resource "aws_lb" "this" {
  name               = substr("${local.name}-alb", 0, 32)
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # parLapply blocks the Shiny process. Shorter than the longest run and the
  # websocket drops mid-simulation.
  idle_timeout = var.alb_idle_timeout

  drop_invalid_header_fields = true
  enable_http2               = true

  # See logging.tf. AWS validates the target S3 bucket's policy at the
  # moment access logging is enabled/updated, so the policy must already
  # exist -- hence the explicit depends_on rather than relying on the
  # implicit reference through aws_s3_bucket.alb_logs.id, which only orders
  # against the bucket itself, not the policy attached to it.
  access_logs {
    bucket  = aws_s3_bucket.alb_logs.id
    enabled = true
  }

  depends_on = [aws_s3_bucket_policy.alb_logs]
}

# One wildcard cert covers every current and future app hostname, so app
# stacks never need to touch ACM.
resource "aws_acm_certificate" "wildcard" {
  domain_name       = "*.${var.domain_name}"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.wildcard.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id         = var.route53_zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "wildcard" {
  certificate_arn         = aws_acm_certificate.wildcard.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.wildcard.certificate_arn

  # Anything that matches no app's host header lands here.
  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}
