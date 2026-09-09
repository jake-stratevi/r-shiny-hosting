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
  description = "Must be unique across every app on the shared ALB listener. Keep a register: dashboard 100, model 200, next app 300."
  type        = number
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
