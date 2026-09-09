"""The access decision matrix: modes x status x expiry x identity.

Every row here is a security assertion. A change that makes one of these
allow where it refused needs an ADR, not a commit.
"""

from __future__ import annotations

import pytest

from proxy_app import access, registry
from proxy_app.identity import Principal

NOW = 1_700_000_000.0
PAST = NOW - 3600
FUTURE = NOW + 3600


def app(**overrides) -> registry.App:
    defaults = dict(
        host="model.tools.stratevi.com",
        app_key="model",
        ecs_service="shiny-model",
        access_mode=registry.MODE_USERS,
        allowed_emails=("jake@stratevi.com",),
    )
    defaults.update(overrides)
    return registry.App.create(**defaults)


ENTITLED = Principal(email="jake@stratevi.com", sub="abc", authenticated=True)
UNENTITLED = Principal(email="stranger@example.com", sub="def", authenticated=True)
NO_EMAIL = Principal(sub="ghi", name="Jake Pistotnik", authenticated=True)
ANONYMOUS = Principal()


CASES = [
    # --- host and identity -------------------------------------------------
    ("unknown host", None, ENTITLED, False, access.OUTCOME_UNKNOWN_HOST, 404),
    ("no identity at all", app(), ANONYMOUS, False, access.OUTCOME_UNAUTHENTICATED, 401),
    (
        "no identity beats even all_users",
        app(access_mode=registry.MODE_ALL_USERS),
        ANONYMOUS,
        False,
        access.OUTCOME_UNAUTHENTICATED,
        401,
    ),
    # --- mode users --------------------------------------------------------
    ("users: on the list", app(), ENTITLED, True, access.OUTCOME_ALLOW, 200),
    ("users: not on the list", app(), UNENTITLED, False, access.OUTCOME_NOT_ENTITLED, 403),
    (
        "users: federated user with no email is 403, not 401",
        app(),
        NO_EMAIL,
        False,
        access.OUTCOME_NOT_ENTITLED,
        403,
    ),
    (
        "users: an empty list refuses everyone",
        app(allowed_emails=()),
        ENTITLED,
        False,
        access.OUTCOME_NOT_ENTITLED,
        403,
    ),
    # --- mode all_users ----------------------------------------------------
    (
        "all_users: anyone signed in",
        app(access_mode=registry.MODE_ALL_USERS),
        UNENTITLED,
        True,
        access.OUTCOME_ALLOW,
        200,
    ),
    (
        "all_users: no-email principal is allowed, matching the portal Lambda",
        app(access_mode=registry.MODE_ALL_USERS),
        NO_EMAIL,
        True,
        access.OUTCOME_ALLOW,
        200,
    ),
    # --- reserved and bogus modes fail closed ------------------------------
    (
        "team is reserved, not implemented",
        app(access_mode=registry.MODE_TEAM),
        ENTITLED,
        False,
        access.OUTCOME_MODE_UNSUPPORTED,
        403,
    ),
    (
        "organizations is reserved",
        app(access_mode=registry.MODE_ORGANIZATIONS),
        ENTITLED,
        False,
        access.OUTCOME_MODE_UNSUPPORTED,
        403,
    ),
    (
        "client_magic_link is reserved",
        app(access_mode=registry.MODE_CLIENT_MAGIC_LINK),
        ENTITLED,
        False,
        access.OUTCOME_MODE_UNSUPPORTED,
        403,
    ),
    (
        "a typo in access_mode is not an open door",
        app(access_mode="everyone_lol"),
        ENTITLED,
        False,
        access.OUTCOME_MODE_UNSUPPORTED,
        403,
    ),
    # --- status ------------------------------------------------------------
    (
        "disabled",
        app(status=registry.STATUS_DISABLED),
        ENTITLED,
        False,
        access.OUTCOME_DISABLED,
        410,
    ),
    (
        "disabled beats entitlement",
        app(status=registry.STATUS_DISABLED, access_mode=registry.MODE_ALL_USERS),
        UNENTITLED,
        False,
        access.OUTCOME_DISABLED,
        410,
    ),
    (
        "status expired",
        app(status=registry.STATUS_EXPIRED),
        ENTITLED,
        False,
        access.OUTCOME_EXPIRED,
        410,
    ),
    (
        "an unrecognised status is a data error, not an open door",
        app(status="halfway"),
        ENTITLED,
        False,
        access.OUTCOME_BAD_STATUS,
        403,
    ),
    # --- expiry, enforced against the clock --------------------------------
    (
        "expires_at in the past, reaper has not run yet",
        app(expires_at=int(PAST)),
        ENTITLED,
        False,
        access.OUTCOME_EXPIRED,
        410,
    ),
    (
        "expires_at in the future",
        app(expires_at=int(FUTURE)),
        ENTITLED,
        True,
        access.OUTCOME_ALLOW,
        200,
    ),
    (
        "expires_at exactly now",
        app(expires_at=int(NOW)),
        ENTITLED,
        False,
        access.OUTCOME_EXPIRED,
        410,
    ),
    ("expires_at zero means never", app(expires_at=0), ENTITLED, True, access.OUTCOME_ALLOW, 200),
    (
        "expiry beats all_users",
        app(access_mode=registry.MODE_ALL_USERS, expires_at=int(PAST)),
        ENTITLED,
        False,
        access.OUTCOME_EXPIRED,
        410,
    ),
]


@pytest.mark.parametrize(
    "row,who,allow,outcome,status",
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_decide_matrix(row, who, allow, outcome, status):
    decision = access.decide(row, who, NOW)
    assert decision == access.Decision(allow, outcome, status)


def test_every_known_mode_is_covered_by_the_matrix():
    """A new mode added to the registry must arrive with a row here."""
    covered = {case[1].access_mode for case in CASES if case[1] is not None}
    assert registry.KNOWN_MODES <= covered


def test_allowed_emails_are_compared_case_and_whitespace_insensitively():
    row = app(allowed_emails=("  JAKE@Stratevi.com ",))
    assert row.allowed_emails == ("jake@stratevi.com",)
    assert access.decide(row, ENTITLED, NOW).allow


def test_allows_never_matches_an_empty_email():
    row = app(allowed_emails=("",))
    assert row.allowed_emails == ()
    assert not row.allows("")
