# ---------------------------------------------------------------------------
# On-demand (PAY_PER_REQUEST): no capacity to plan for, and no NAT/VPC
# endpoint needed to reach it -- tasks have public IPs (ADR-0004), and
# DynamoDB traffic to a public endpoint stays on AWS's network edge. See
# docs/design/proxy.md.
# ---------------------------------------------------------------------------

# One item per app the proxy fronts. PITR on: this is the entitlements
# database -- losing it means every app stops resolving.
resource "aws_dynamodb_table" "apps" {
  name         = "shiny-proxy-apps"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "host"

  attribute {
    name = "host"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

# Append-only audit events: allow | deny | wake | sleep | expired.
# TTL retires rows after 90 days (the app writes the "ttl" epoch value;
# Terraform only turns the attribute on). Not PITR -- it's a log, not a
# source of truth, and TTL already bounds its size.
#
# The sort key is named "ts" (value "<epoch-ms>#<8 hex>") -- normative in
# docs/design/proxy.md "Contract details"; the service writes exactly this.
resource "aws_dynamodb_table" "audit" {
  name         = "shiny-proxy-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "host"
  range_key    = "ts"

  attribute {
    name = "host"
    type = "S"
  }

  attribute {
    name = "ts"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}
