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
# One entry per person, and that is new. Until 2026-09-10 every person
# appeared TWICE here, because the Hub pool had two identity providers for the
# same human and returned a different email from each: a native
# @stratevi.com account and a Microsoft365-federated
# @assembledintelligence.co.uk one. Both had to be listed or an admin lost
# the control plane depending on which button they clicked.
#
# ADR-0015 ended that. The platform has its own pool, staff sign in only
# through the Microsoft365 federation, and it emits the @stratevi.com
# address; the native staff accounts were deleted (they would now collide
# with the federated identity -- AliasExistsException, see CLAUDE.md). The
# four @assembledintelligence.co.uk entries were therefore addresses that
# nothing could ever authenticate as, in the row that decides who
# administers the platform, which is the wrong place to keep dead entries.
# They are gone.
#
# Do not re-add a second address for one person. If a staff member ever
# arrives on a different domain, widen `staff_domains` below and add the one
# address they actually sign in with.
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table_item" "portal_config" {
  table_name = aws_dynamodb_table.apps.name
  hash_key   = aws_dynamodb_table.apps.hash_key

  item = jsonencode({
    host = { S = "__config__" }
    # Alphabetical -- DynamoDB returns string sets sorted, and any other
    # order here shows up as a perpetual cosmetic diff on every plan.
    admin_emails = { SS = [
      "jake@stratevi.com",
      "josh@stratevi.com",
      "nick@stratevi.com",
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
    # All four staff may create (Jake, 2026-09-11). The permission stays a
    # separate set for the reason above -- it is not implied by admin, and it
    # is not implied by being staff either: `staff_domains` below is a
    # NECESSARY condition for holding it, never a sufficient one, so an
    # address has to be both staff and named here.
    #
    # Adding a creator is a reviewed commit and an apply, not a click in the
    # UI -- that is the entire point of the row being Terraform-owned, so
    # please do not "temporarily" add someone through the console.
    #
    # The API reads this and returns can_create from /me so the UI can hide
    # the "+" card rather than dangle a 403. Fail-closed: a missing row, an
    # empty set, or a read error all mean nobody may create.
    #
    # Alphabetical, same reason as admin_emails above -- DynamoDB returns
    # string sets sorted and any other order is a perpetual plan diff.
    creator_emails = { SS = [
      "jake@stratevi.com",
      "josh@stratevi.com",
      "nick@stratevi.com",
      "yi@stratevi.com",
    ] }

    # --- who counts as STAFF ----------------------------------------------
    #
    # The domain half of every control-plane permission. The portal ANDs this
    # with the two sets above: `admin_emails` and `creator_emails` say WHO,
    # this says who is eligible to be named. An address outside these domains
    # is refused admin and create even when it is named in one of those sets,
    # so "only Stratevi staff hold the control plane" is enforced by the
    # service rather than by whoever last pruned a list. That matters because
    # both sets also accumulate client addresses by mistake far more easily
    # than anyone expects -- an app's `allowed_emails` is the right place for
    # a client, and this makes pasting one here harmless.
    #
    # It does NOT restrict who may USE an app. Entitlement is the app row's
    # `allowed_emails` and /menu is unchanged: clients sign in to the same
    # pool (ADR-0015's two populations) and see their own dashboards.
    #
    # Matching is on the email's domain, case-insensitively, EXACT domain
    # only -- no wildcards and no suffix test, so "notstratevi.com" and
    # "stratevi.com.evil.test" both fail.
    #
    # Unlike the other three sets this one does not fail closed. A missing,
    # empty or unreadable attribute falls back to ("stratevi.com",) in
    # proxy_app/registry.py (DEFAULT_STAFF_DOMAINS -- the reasoning is in
    # full there): failing closed on a domain list would refuse EVERY
    # administrator at once during a DynamoDB blip, and this platform has no
    # break-glass account. The fallback can only ever be narrower than what
    # is written here, never wider.
    #
    # Alphabetical, same reason as every set above.
    staff_domains = { SS = [
      "stratevi.com",
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
