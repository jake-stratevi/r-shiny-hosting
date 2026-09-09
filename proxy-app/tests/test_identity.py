"""Claim parsing, including the federated no-email cases that caused ADR-0008.

The fallback order here must stay identical to dashboard-app/access.R and
portal/lambda/portal/index.py. If one of these tests has to change, the other
two implementations have to change with it.
"""

from __future__ import annotations

import base64
import json

import pytest

from proxy_app import identity


def jwt(payload: dict | None = None, *, raw: str | None = None) -> str:
    """A JWT whose signature is nonsense -- nothing here verifies it."""
    body = raw if raw is not None else json.dumps(payload or {})
    segment = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{segment}.signature"


# --- claim decoding --------------------------------------------------------


def test_decodes_payload_without_verifying_the_signature():
    claims = identity.decode_claims(jwt({"email": "jake@stratevi.com", "sub": "abc"}))
    assert claims == {"email": "jake@stratevi.com", "sub": "abc"}


def test_decodes_payload_that_needs_base64_padding():
    # One, two and three missing pad characters, so the padding fix is
    # exercised rather than accidentally satisfied.
    for filler in ("a", "aa", "aaa", "aaaa"):
        claims = identity.decode_claims(jwt({"email": f"{filler}@stratevi.com"}))
        assert claims["email"] == f"{filler}@stratevi.com"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "not-a-jwt",
        "onlyonesegment",
        "header.%%%not-base64%%%.sig",
        jwt(raw="not json at all"),
        jwt(raw='["a", "list", "not", "an", "object"]'),
    ],
)
def test_unparseable_tokens_decode_to_nothing_rather_than_raising(token):
    assert identity.decode_claims(token) == {}


# --- the email fallback order ----------------------------------------------


@pytest.mark.parametrize(
    "claims,expected",
    [
        ({"email": "jake@stratevi.com"}, "jake@stratevi.com"),
        ({"upn": "jake.pistotnik@assembledintelligence.co.uk"},
         "jake.pistotnik@assembledintelligence.co.uk"),
        ({"preferred_username": "nick@stratevi.com"}, "nick@stratevi.com"),
        ({"custom:email": "yi@stratevi.com"}, "yi@stratevi.com"),
        ({"email": "  JAKE@Stratevi.COM  "}, "jake@stratevi.com"),
    ],
)
def test_each_claim_in_the_fallback_order_is_accepted(claims, expected):
    assert identity.email_from_claims(claims) == expected


def test_fallback_order_prefers_email_over_upn_over_preferred_username():
    claims = {
        "email": "first@stratevi.com",
        "upn": "second@stratevi.com",
        "preferred_username": "third@stratevi.com",
        "custom:email": "fourth@stratevi.com",
    }
    assert identity.email_from_claims(claims) == "first@stratevi.com"

    del claims["email"]
    assert identity.email_from_claims(claims) == "second@stratevi.com"

    del claims["upn"]
    assert identity.email_from_claims(claims) == "third@stratevi.com"

    del claims["preferred_username"]
    assert identity.email_from_claims(claims) == "fourth@stratevi.com"


def test_a_claim_without_an_at_sign_is_skipped():
    # The exact Entra shape ADR-0008 hit: a synthetic Cognito username in
    # preferred_username and a real address one claim further down.
    claims = {
        "preferred_username": "microsoft365_gwww7jtfv4cb0km6n1y5qtda",
        "custom:email": "josh@stratevi.com",
    }
    assert identity.email_from_claims(claims) == "josh@stratevi.com"


def test_a_federated_user_with_no_address_anywhere_has_no_email():
    claims = {
        "sub": "9f1c-…",
        "preferred_username": "microsoft365_gwww7jtfv4cb0km6n1y5qtda",
        "name": "Jake Pistotnik",
    }
    assert identity.email_from_claims(claims) == ""


def test_non_string_claims_are_not_coerced_into_addresses():
    assert identity.email_from_claims({"email": 12345}) == ""
    assert identity.email_from_claims({"email": {"address": "a@b.com"}}) == ""
    assert identity.email_from_claims({}) == ""


# --- groups ----------------------------------------------------------------


def test_groups_come_from_the_access_token_claim():
    assert identity.groups_from_claims({"cognito:groups": ["admins", "viewers"]}) == (
        "admins",
        "viewers",
    )


def test_missing_or_malformed_groups_are_empty():
    assert identity.groups_from_claims({}) == ()
    assert identity.groups_from_claims({"cognito:groups": "admins"}) == ()
    assert identity.groups_from_claims({"cognito:groups": [1, None, "ok"]}) == ("ok",)


# --- the principal ---------------------------------------------------------


def test_principal_from_alb_headers():
    principal = identity.from_headers(
        {
            "X-Amzn-Oidc-Data": jwt({"email": "jake@stratevi.com", "name": "Jake"}),
            "X-Amzn-Oidc-Identity": "sub-from-header",
            "X-Amzn-Oidc-Accesstoken": jwt({"cognito:groups": ["admins"]}),
        }
    )
    assert principal.email == "jake@stratevi.com"
    assert principal.sub == "sub-from-header"
    assert principal.name == "Jake"
    assert principal.groups == ("admins",)
    assert principal.authenticated


def test_header_lookup_is_case_insensitive():
    lower = identity.from_headers({"x-amzn-oidc-data": jwt({"email": "a@b.com"})})
    upper = identity.from_headers({"X-AMZN-OIDC-DATA": jwt({"email": "a@b.com"})})
    assert lower == upper
    assert lower.email == "a@b.com"


def test_sub_falls_back_to_the_claim_when_the_identity_header_is_absent():
    principal = identity.from_headers({"x-amzn-oidc-data": jwt({"sub": "from-claim"})})
    assert principal.sub == "from-claim"
    assert principal.authenticated


def test_a_federated_principal_with_no_email_is_still_authenticated():
    # This is the case that must NOT become a 401: signed in, no address.
    principal = identity.from_headers(
        {
            "x-amzn-oidc-data": jwt(
                {
                    "sub": "9f1c",
                    "preferred_username": "microsoft365_gwww7jtfv4cb0km6n1y5qtda",
                    "name": "Jake Pistotnik",
                }
            )
        }
    )
    assert principal.email == ""
    assert principal.authenticated
    assert principal.who() == "Jake Pistotnik"


def test_identity_header_alone_is_enough_to_be_authenticated():
    principal = identity.from_headers({"x-amzn-oidc-identity": "sub-only"})
    assert principal.authenticated
    assert principal.email == ""
    assert principal.who() == identity.NO_EMAIL_LABEL


def test_no_headers_at_all_is_the_only_unauthenticated_case():
    assert not identity.from_headers({}).authenticated
    assert not identity.from_headers({"user-agent": "curl/8"}).authenticated
    assert not identity.from_headers({"x-amzn-oidc-data": "garbage"}).authenticated


def test_who_prefers_email_then_name_then_a_neutral_label():
    assert identity.Principal(email="a@b.com", name="A B").who() == "a@b.com"
    assert identity.Principal(name="A B").who() == "A B"
    assert identity.Principal(sub="synthetic-id").who() == identity.NO_EMAIL_LABEL
