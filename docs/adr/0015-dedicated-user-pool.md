# ADR-0015: A dedicated Cognito user pool for the Shiny platform

**Status:** Accepted (Jake, 2026-09-10) — supersedes [ADR-0007](0007-reuse-hub-cognito-pool.md)
**Date:** 2026-09-10

## Context

ADR-0007 reused the shared Assembled Hub pool to avoid a second directory and
a second Entra registration. That was right when the platform only needed to
*authenticate* people someone else had already onboarded.

The portal now needs to *manage* users: create them, list them, search them
when granting app access (portal.md, P2.5). Doing that against the Hub pool
means a Stratevi tool writing into a directory that other Assembled products
depend on — a boundary Jake explicitly does not want to cross. The
dual-identity wart ADR-0007 accepted (every person listed twice, once per
IdP) has also been resolved operationally, removing most of the Hub pool's
remaining value to this platform.

## Decision

The platform stack owns a user pool again: `shiny-platform`, invite-only
(`allow_admin_create_user_only` — admins and the portal create users; nobody
self-registers), email sign-in, managed login v2, its own hosted-UI domain.
The SSM exports repoint every consumer at the new pool; the proxy's shared
app client moves on its next apply.

**Two populations, one pool** (settled 2026-09-10, after the switchover):

- **Staff** — `@stratevi.com` and `@assembledintelligence.co.uk` — sign in
  through the **Microsoft365** Entra federation. Cognito auto-provisions
  each as a `microsoft365_<sub>` EXTERNAL_PROVIDER user on their *first*
  sign-in; they do not exist in the pool before that.
- **External clients** are created by an admin (later, by the portal) with
  admin-create-user and sign in with email + password on the same page.

Terraform declares **no users at all**. Both populations are runtime data,
not infrastructure.

## Alternatives considered

**Keep the Hub pool, add users via invites only.** Least work, but every
user-management feature in the portal still writes into shared
infrastructure, and any Hub-side change (password policy, MFA, branding) is
someone else's decision applied to Stratevi's clients.

**A new pool federated to Entra from day one.** Rejected as premature when
this was written — then done anyway, hours later, because staff wanted SSO
back immediately. The federation went in by hand on 2026-09-10
(`Microsoft365`, OIDC, `email`/`name`/`sub` mapped) and native staff
accounts were retired in favour of it. Two costs were paid for doing it by
hand rather than in Terraform: the provider is unmanaged drift (its client
secret lives only in the Cognito API), and the redirect URI landed under
Entra's *Single-page application* platform, which forces PKCE and made
Cognito's confidential-client token redemption fail with AADSTS9002325
until it was moved to **Web**. Both are recorded in GOTCHAS.md.

## Consequences

- Everyone signs in again once, against the new pool (new client id on the
  ALB rule).
- **Authorization is unaffected by any of this.** The proxy reads the email
  claim from `x-amzn-oidc-data`, lowercases it, and checks it against the
  app row's `allowed_emails` and the `__config__` row's `admin_emails`.
  Nothing in that path knows or cares whether the caller is federated or
  native, so access and admin controls work identically for staff and for
  external clients. (Entra emits `jake@STRATEVI.COM` in caps; both sides
  lowercase, so it matches.)
- **Email is the username, so an address can exist only once in the pool.**
  A native account for an address Entra also emits collides with that
  person's federated identity — `AliasExistsException`. This is why the
  four seeded staff users were deleted on 2026-09-10 and why
  `platform/cognito.tf` now declares no users: recreating them would fail
  an apply halfway through. Only ever hand-create users at addresses the
  federation will never return.
- **The portal's user picker cannot rely on the pool alone** (P2.5): a
  colleague who has never signed in is not in it. It needs a roster, or
  free-text entry, alongside a pool listing.
- **No standing break-glass account, by choice.** If federation fails
  nobody with a staff address can sign in; recovery is console-creating a
  temporary user at a non-Entra address and adding it to `admin_emails`
  (RUNBOOK.md, "User administration"). Fewer standing credentials, at the
  cost of needing the console in an outage. Revisit if the platform ever
  matters at 3am.
- The portal can create/disable/search users with no external coordination —
  the user registry P2.5 needs is just the pool plus a portal table.
- **Sequencing traps, recorded here and in platform/ssm.tf:** the ADR-0013
  Lambda portal (rule 50) and the un-migrated model stack were last applied
  against the Hub pool. Re-applying either before it is retired/migrated
  would recreate its client against the new pool and break its login until
  the managed-login branding CLI is run. Retire, don't re-apply.
- Every new app client in the v2 pool needs
  `create-managed-login-branding` after creation (the GOTCHAS.md entry).
- The Hub pool is untouched and keeps serving the other Assembled products;
  this platform's clients there die with the stacks that own them.
