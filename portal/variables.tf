variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  description = "Must match the platform stack. Used to find the SSM namespace."
  type        = string
  default     = "shiny"
}

variable "app_key" {
  description = "Short slug for this stack. Names every resource."
  type        = string
  default     = "portal"
}

variable "app_label" {
  description = "Human-readable name, shown at the top of the portal page."
  type        = string
  default     = "Stratevi Dashboards"
}

variable "subdomain" {
  description = "Host under the platform's domain, e.g. 'dashboards' becomes dashboards.tools.example.com"
  type        = string
  default     = "dashboards"
}

variable "listener_rule_priority" {
  description = "Must be unique across every app on the shared ALB listener. See CLAUDE.md's registry."
  type        = number
}
