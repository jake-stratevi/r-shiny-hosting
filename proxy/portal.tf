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
    # Alphabetical -- DynamoDB returns string sets sorted, and any other
    # order here shows up as a perpetual cosmetic diff on every plan.
    admin_emails = { SS = [
      "jake.pistotnik@assembledintelligence.co.uk",
      "jake@stratevi.com",
      "josh.epstein@assembledintelligence.co.uk",
      "josh@stratevi.com",
      "nick.adair@assembledintelligence.co.uk",
      "nick@stratevi.com",
      "yi.pan@assembledintelligence.co.uk",
      "yi@stratevi.com",
    ] }

    # --- P2a: who may CREATE an app ---------------------------------------
    #
    # A separate set from admin_emails, deliberately (Jake, 2026-09-10 --
    # docs/design/portal-p2a.md "Decisions"). Being an admin lets you edit
    # access, expiry and settings on apps that already exist; it does NOT
    # let you stand up a new container, a new IAM role and a new public
    # hostname. Creation is the permission that spends money and publishes
    # something clients can see, so it is its own set.
    #
    # Jake decides who else goes in here. Adding a creator is a reviewed
    # commit and an apply, not a click in the UI -- that is the entire point
    # of the row being Terraform-owned, so please do not "temporarily" add
    # someone through the console.
    #
    # The API reads this and returns can_create from /me so the UI can hide
    # the "+" card rather than dangle a 403. Fail-closed: a missing row, an
    # empty set, or a read error all mean nobody may create.
    #
    # Alphabetical, same reason as admin_emails above -- DynamoDB returns
    # string sets sorted and any other order is a perpetual plan diff.
    creator_emails = { SS = [
      "jake.pistotnik@assembledintelligence.co.uk",
      "jake@stratevi.com",
    ] }

    # --- P2a: substrings banned from a public hostname --------------------
    #
    # `<key>.tools.stratevi.com` is visible to clients and shows up in
    # browser history, TLS logs and screen shares, so a key must not leak a
    # brand, molecule or client name. These are SUBSTRING matches, not exact
    # ones: "tarpeyo" rejects "tarpeyo", "tarpeyo-v2" and "old-tarpeyo"
    # alike. Lowercase only -- the key is already lowercased before the
    # check, per the shape rule in portal-p2a.md.
    #
    # The wizard rejects with "that name can't be used in a public hostname
    # -- pick a project codename" and NEVER echoes which term matched;
    # saying so would turn this list into an oracle for the very names it
    # exists to hide.
    #
    # THIS IS A STARTER SET, NOT THE REAL LIST. Jake still owes the actual
    # brand/molecule/client terms (portal-p2a.md, "Hostnames are policed").
    # It lives in DynamoDB rather than in code so that list can be edited
    # without a deploy once it exists -- but it is seeded here so the
    # mechanism is provably wired up on day one rather than shipping empty
    # and untested.
    #
    # Alphabetical, same reason as the two sets above.
    key_denylist = { SS = [
      "acme",
      "clientco",
      "confidential",
      "tarpeyo",
    ] }
  })
}
