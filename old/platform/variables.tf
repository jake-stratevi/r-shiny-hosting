variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Prefix for every resource, and the SSM namespace the app stacks read from."
  type        = string
  default     = "shiny"
}

# --- Networking -------------------------------------------------------------

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "Two public subnets in different AZs. Tasks run here with public IPs so no NAT Gateway is needed (~$33/mo saved); they are locked to the ALB security group."
  type        = list(string)
  default     = ["10.40.1.0/24", "10.40.2.0/24"]
}

variable "alb_allowed_cidrs" {
  description = "Who can reach the ALB. Cognito already gates access; narrow this for defence in depth."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# --- DNS / TLS --------------------------------------------------------------

variable "route53_zone_id" {
  description = "Existing Route 53 public hosted zone ID."
  type        = string
}

variable "domain_name" {
  description = "Apex matching the hosted zone, e.g. tools.example.com. A wildcard cert is issued for *.<domain_name> so app stacks need no cert of their own."
  type        = string
}

# --- Cognito ----------------------------------------------------------------

variable "cognito_domain_prefix" {
  description = "Globally unique hosted-UI domain prefix."
  type        = string
}

variable "admin_create_users_only" {
  description = "Invite-only. Recommended for client-facing tools."
  type        = bool
  default     = true
}

variable "oidc_provider" {
  description = "Optional OIDC federation (e.g. Entra ID). Supply via TF_VAR_oidc_provider so the secret stays out of files."
  type = object({
    name          = string
    issuer        = string
    client_id     = string
    client_secret = string
    scopes        = string
  })
  default   = null
  sensitive = true
}

# --- ALB --------------------------------------------------------------------

variable "alb_idle_timeout" {
  description = "Seconds. Must exceed the longest blocking model run, because parLapply blocks the Shiny websocket. Max 4000."
  type        = number
  default     = 3600
}

# --- Shared defaults handed to app stacks -----------------------------------

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "monthly_budget_usd" {
  type    = number
  default = 75
}

variable "budget_alert_emails" {
  description = "Empty list disables the budget."
  type        = list(string)
  default     = []
}
