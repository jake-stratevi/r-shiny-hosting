variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  description = "Must match the platform stack's project. Used to find the SSM namespace."
  type        = string
  default     = "shiny"
}

variable "app_key" {
  description = "Short slug for this app. Names every resource."
  type        = string
}

variable "app_label" {
  description = "Human-readable name, shown on the 'starting up' page."
  type        = string
}

variable "subdomain" {
  description = "Host under the platform's domain, e.g. 'model' becomes model.tools.example.com"
  type        = string
}

variable "listener_rule_priority" {
  description = "Must be unique across every app on the shared ALB listener. Keep a register: dashboard 100, model 200, next app 300. Ignored when proxied = true -- no rule is created."
  type        = number
}

# --- Migration to the authorizing proxy -------------------------------------
#
# See docs/design/proxy.md, "Routing: catch-all, lowest precedence, migrate
# app-by-app". The proxy is an always-on ECS service behind ONE priority-5000
# catch-all listener rule for *.tools.stratevi.com. It authenticates there with
# a shared Cognito client, connects straight to the task ENI's private IP, and
# does wake-on-request and server-side sleep itself.
#
# proxied = false (default): exactly today's behaviour. This stack owns its host
#   listener rule, the never-matches ECS-association rule, both target groups,
#   the waker and sleeper Lambdas with their log groups and EventBridge
#   schedule, its own Cognito app client, and the target-group-dimensioned alarm
#   and CloudWatch dashboard.
#
# proxied = true: none of that is created. What remains is what the proxy needs
#   and cannot own -- the ECS service and task definition, the ECR repo and
#   lifecycle policy, the per-app task IAM role, the DNS record and the app log
#   group.
#
# Before flipping to true: add https://<host>/oauth2/idpresponse to the shared
# proxy Cognito client's callbacks, and seed this app's row in
# shiny-proxy-apps. Otherwise the first request after apply has nowhere to go.
#
# Rollback is symmetrical -- set it back to false and apply; every resource
# above is recreated from this same configuration. Delete the app's
# shiny-proxy-apps row in the same change, or the proxy and the sleeper will
# both drive desired_count. The ECS service itself is updated IN PLACE in both
# directions -- it is not replaced, and its ARN does not change (see ecs.tf).
variable "proxied" {
  description = "true hands this host to the authorizing proxy: skip the per-app listener rules, target groups, waker/sleeper Lambdas, Cognito client and target-group monitoring. See docs/design/proxy.md."
  type        = bool
  default     = false
}

# --- Task sizing ------------------------------------------------------------

variable "cpu" {
  description = "Fargate CPU units. 512 = 0.5 vCPU, 4096 = 4 vCPU."
  type        = number
}

variable "memory" {
  description = "MiB. Must be a valid pairing with cpu."
  type        = number
}

variable "container_port" {
  type    = number
  default = 3838
}

variable "image_tag" {
  type    = string
  default = "latest"
}

# --- Sleep behaviour --------------------------------------------------------

variable "idle_minutes" {
  description = "Scale to zero after this long with no heartbeat traffic."
  type        = number
  default     = 20
}

variable "min_uptime_minutes" {
  description = "Never scale down a task younger than this. Protects a cold start that has not accumulated metrics yet."
  type        = number
  default     = 10
}

variable "warm_enabled" {
  description = "Pre-start during a weekly window so nobody waits on a cold start."
  type        = bool
  default     = false
}

variable "warm_days" {
  description = "0 = Monday ... 6 = Sunday."
  type        = list(number)
  default     = []
}

variable "warm_start_utc" {
  type    = number
  default = 0
}

variable "warm_end_utc" {
  description = "May be smaller than warm_start_utc; the window then wraps past midnight UTC."
  type        = number
  default     = 0
}

variable "sleeper_interval_minutes" {
  type    = number
  default = 5
}

variable "stuck_alarm_hours" {
  description = "Alarm if the task stays awake this many hours straight. Catches a broken sleeper before it costs a month of compute."
  type        = number
  default     = 12
}

# --- Per-app authorization --------------------------------------------------
#
# The ALB answers "did this person sign in". It cannot answer "may this person
# see this app". That decision happens in the app, from the identity headers
# the ALB injects. See docs/adr/0008-authorization-strategy.md.

variable "access_mode" {
  description = <<-EOT
    off     no check -- local development only
    emails  allow the addresses in allowed_emails
    groups  allow members of the Cognito groups in allowed_groups

    Start on emails. Move to groups once they are populated and synced from
    Entra; that is a tfvars change, not a code change.
  EOT
  type        = string
  default     = "emails"

  validation {
    condition     = contains(["off", "emails", "groups"], var.access_mode)
    error_message = "access_mode must be off, emails or groups."
  }
}

variable "allowed_emails" {
  description = "Addresses permitted to use THIS app. Per-app, not shared."
  type        = list(string)
  default     = []
}

variable "allowed_groups" {
  description = "Cognito groups permitted to use THIS app. Used when access_mode = groups."
  type        = list(string)
  default     = []
}

variable "access_contact" {
  description = "Who a refused user is told to contact."
  type        = string
  default     = "your Stratevi contact"
}

variable "waker_enabled" {
  description = "Cost kill-switch. false blocks the waker Lambda (reserved concurrency 0) so nothing can scale this app up. See lambda.tf."
  type        = bool
  default     = true
}
