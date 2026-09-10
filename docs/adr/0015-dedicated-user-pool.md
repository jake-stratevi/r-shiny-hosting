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
(`allow_admin_create_user_only` — the portal and admins create users; nobody
self-registers), email sign-in, managed login v2, its own hosted-UI domain.
The four staff are seeded as Terraform-managed users and receive invite
emails with temporary passwords. The SSM exports repoint every consumer at
the new pool; the proxy's shared app client moves on its next apply.

Entra federation into the new pool is optional and deferred: nothing breaks
without it (native accounts work today); when wanted, it is one Entra app
registration and one `oidc_provider_name` change.

## Alternatives considered

**Keep the Hub pool, add users via invites only.** Least work, but every
user-management feature in the portal still writes into shared
infrastructure, and any Hub-side change (password policy, MFA, branding) is
someone else's decision applied to Stratevi's clients.

**A new pool federated to Entra from day one.** Cleaner for staff, but
front-loads an Entra registration for zero functional gain today, and
re-imports the federated-user-invisible-until-first-login quirk into the
portal's user list before the portal exists to handle it.

## Consequences

- Everyone signs in again once, against the new pool (new client id on the
  ALB rule). Password-based to start; invites go to the four staff on apply.
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
