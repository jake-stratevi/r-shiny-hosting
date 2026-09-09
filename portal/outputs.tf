output "url" {
  value     = "https://${local.fqdn}"
  sensitive = true
}

output "app_count" {
  description = "Apps currently in the catalog, for a sanity check after edits."
  value       = length(local.catalog.apps)
}

output "estimated_fixed_monthly_usd" {
  description = "Beyond the shared ALB (already counted in the platform stack): effectively $0. A Lambda that handles a handful of page loads a day costs fractions of a cent."
  value       = 0
}
