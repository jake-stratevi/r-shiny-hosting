# ---------------------------------------------------------------------------
# Portal P1 (docs/design/portal.md): the portal is routes on this same
# service, gated by the __config__ row below.
#
# The __config__ row is TERRAFORM-OWNED, deliberately: changing who is a
# platform admin should be a reviewed commit, not a runtime click. Rows whose
# host starts with "__" are configuration -- the service skips them as apps.
# Do not add other attributes to this row from application code, or Terraform
# will fight over it on the next apply.
#
# Every person appears twice: Cognito returns a different email per identity
# provider (native @stratevi.com vs Microsoft365-federated
# @assembledintelligence.co.uk). Same rule as every allowlist -- see the
# comment in catalog.yaml and docs/STATUS.md.
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table_item" "portal_config" {
  table_name = aws_dynamodb_table.apps.name
  hash_key   = aws_dynamodb_table.apps.hash_key

  item = jsonencode({
    host = { S = "__config__" }
    admin_emails = { SS = [
      "jake@stratevi.com",
      "jake.pistotnik@assembledintelligence.co.uk",
      "nick@stratevi.com",
      "nick.adair@assembledintelligence.co.uk",
      "yi@stratevi.com",
      "yi.pan@assembledintelligence.co.uk",
      "josh@stratevi.com",
      "josh.epstein@assembledintelligence.co.uk",
    ] }
  })
}
