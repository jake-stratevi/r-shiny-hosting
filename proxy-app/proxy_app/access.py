"""The entitlement decision, and nothing else.

It is pure: an app row, a principal, a clock. No AWS, no HTTP, no logging.
This is the gate ADR-0008 promised and ADR-0014 moved out of the apps, so it
is the one function in the service worth being paranoid about.

It fails closed everywhere: an unknown mode, an unknown status, a mode
reserved for the portal phase, a missing identity, and (in the caller) a
database row we could not read all refuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

from . import registry
from .identity import Principal
from .registry import App

# Outcome names why a request was allowed or refused. It is both the audit
# event detail and the page selector.
OUTCOME_ALLOW = "allow"
OUTCOME_UNKNOWN_HOST = "unknown_host"
OUTCOME_UNAUTHENTICATED = "unauthenticated"
OUTCOME_EXPIRED = "expired"
OUTCOME_DISABLED = "disabled"
OUTCOME_NOT_ENTITLED = "not_entitled"
OUTCOME_MODE_UNSUPPORTED = "mode_unsupported"
OUTCOME_BAD_STATUS = "bad_status"


@dataclass(frozen=True)
class Decision:
    """The answer, plus the HTTP status to answer a refusal with."""

    allow: bool
    outcome: str
    status: int


_ALLOW = Decision(True, OUTCOME_ALLOW, HTTPStatus.OK)


def decide(app: App | None, principal: Principal, now: float) -> Decision:
    """Answer whether this principal may use this app right now.

    ``app is None`` means the Host header matched no row -- the wildcard
    listener rule catches every ``*.tools.stratevi.com`` name, including ones
    nobody has provisioned and ones that were deleted.
    """
    if app is None:
        return Decision(False, OUTCOME_UNKNOWN_HOST, HTTPStatus.NOT_FOUND)

    # An unauthenticated request reaching here means something is wrong with
    # the ALB rule -- the catch-all rule authenticates before forwarding. The
    # safe response is refusal, same as access.R's. This is the ONLY 401: a
    # signed-in principal whose email we cannot resolve is authenticated.
    if not principal.authenticated:
        return Decision(False, OUTCOME_UNAUTHENTICATED, HTTPStatus.UNAUTHORIZED)

    # Expiry is enforced against the clock, not against the status attribute:
    # the reaper runs once a minute and an app must not stay reachable for
    # that minute. Both the expired status and a lapsed expires_at answer
    # directly, with no cold start needed in order to refuse someone.
    if app.status == registry.STATUS_EXPIRED or app.is_expired(now):
        return Decision(False, OUTCOME_EXPIRED, HTTPStatus.GONE)
    if app.status == registry.STATUS_DISABLED:
        return Decision(False, OUTCOME_DISABLED, HTTPStatus.GONE)
    if app.status != registry.STATUS_ACTIVE:
        # An unrecognised status is a data error, not a reason to let people in.
        return Decision(False, OUTCOME_BAD_STATUS, HTTPStatus.FORBIDDEN)

    if app.access_mode == registry.MODE_ALL_USERS:
        # Any authenticated Hub-pool user. Deliberately does NOT require an
        # email: a federated user with only a synthetic username is still a
        # signed-in member of the pool, and this mode asks nothing more of
        # them. The portal Lambda's catalog makes the same call.
        return _ALLOW

    if app.access_mode == registry.MODE_USERS:
        # No resolvable email cannot match any list entry, so a federated
        # user with only a synthetic username is refused here -- by the same
        # rule that refuses anyone else who is not on the list.
        if app.allows(principal.email):
            return _ALLOW
        return Decision(False, OUTCOME_NOT_ENTITLED, HTTPStatus.FORBIDDEN)

    # team / organizations / client_magic_link are reserved by ADR-0014 so the
    # schema does not change later. Until the portal phase implements them, an
    # app carrying one is closed, not open -- and so is a typo.
    return Decision(False, OUTCOME_MODE_UNSUPPORTED, HTTPStatus.FORBIDDEN)
