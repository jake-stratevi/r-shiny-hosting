# Budget covers every stack tagged with this project, so it catches app-level
# spend too. App stacks own their own per-app alarms.
resource "aws_budgets_budget" "this" {
  count = length(var.budget_alert_emails) > 0 ? 1 : 0

  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Project$%s", var.project)]
  }

  # Early tripwire at ~53% (= ~$40 of the $75 budget): days-not-month-end
  # warning that something stopped sleeping. The realistic runaway is an app
  # task held awake 24/7 -- model at $0.233/hr is ~$170/month.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 53
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_alert_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_alert_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.budget_alert_emails
  }
}
