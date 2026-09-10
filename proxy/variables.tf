variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  description = "Must match the platform stack's project. Used to find the SSM namespace."
  type        = string
  default     = "shiny"
}

# --- Task sizing -------------------------------------------------------------
#
# 0.25 vCPU / 512 MB per docs/design/proxy.md -- a static Go binary handling
# HTTP routing and DynamoDB lookups, not an R runtime.

variable "cpu" {
  type    = number
  default = 256
}

variable "memory" {
  type    = number
  default = 512
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "log_level" {
  type    = string
  default = "info"
}

# --- Service sizing ------------------------------------------------------------
#
# Terraform owns this outright (ecs.tf) -- there is no waker/sleeper for the
# proxy itself. 1 now, 2 "when a client is live" per the spec.

variable "desired_count" {
  type    = number
  default = 1
}

# --- Routing / DNS -----------------------------------------------------------

variable "listener_rule_priority" {
  description = "Register in CLAUDE.md. Fixed at 5000 per docs/design/proxy.md -- deliberately far above every app's explicit rule (dashboard 100, model 200) so the catch-all never outranks a still-unmigrated app."
  type        = number
  default     = 5000
}

variable "portal_menu_subdomain" {
  description = "Host of the user-facing portal menu (Jake's call 2026-09-10: keep 'dashboards', continuity with the ADR-0013 Lambda portal it replaces). The proxy answers it with the portal UI once rule 50 is retired."
  type        = string
  default     = "dashboards"
}

variable "proxy_subdomain" {
  description = "Host the proxy answers on directly (health page, and its own Cognito callback), e.g. 'proxy' -> proxy.tools.stratevi.com. Covered by the wildcard alias in dns.tf, so no separate DNS record is needed for it."
  type        = string
  default     = "proxy"
}

# --- Cognito ------------------------------------------------------------------

variable "app_hosts" {
  description = <<-EOT
    Every app hostname the proxy's shared Cognito client must accept a
    callback for, e.g. ["model.tools.stratevi.com"]. Empty until the first app
    (model, per the spec) is migrated. The portal phase replaces this with SDK
    calls that add a host the moment an app is created -- this variable is the
    manual stand-in until then.
  EOT
  type        = list(string)
  default     = []
}
