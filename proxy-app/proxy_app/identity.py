"""Turns the headers the ALB injects into a principal.

This is a port of ``dashboard-app/access.R``'s ``access_principal()`` and
``portal/lambda/portal/index.py``'s ``_claims()``/``_email()``. All three must
agree: the same person signing in the same way has to resolve to the same
address whether the decision is made in R, in the portal Lambda, or here. If
you change the claim fallback order in one, change it in all three.

SECURITY NOTE -- the JWT signature is not verified here, exactly as in
access.R and the portal Lambda.

``x-amzn-oidc-data`` is signed by the ALB with ES256, and the correct hardening
is to fetch the ALB's public key from
``https://public-keys.auth.elb.<region>.amazonaws.com/<kid>`` and verify it. We
do not, because the proxy task's security group accepts traffic only from the
ALB's security group, so there is no path by which a forged header can arrive.

That reasoning stops holding the moment the proxy task becomes reachable by
anything else -- a second ingress rule, a public target group, a service mesh,
a debugging tunnel. If that changes, verify the signature before trusting any
claim in this module.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: Headers the ALB sets on every authenticated request.
HEADER_DATA = "x-amzn-oidc-data"
HEADER_IDENTITY = "x-amzn-oidc-identity"
HEADER_ACCESS_TOKEN = "x-amzn-oidc-accesstoken"

#: The fallback order. A federated Cognito user often arrives with no `email`
#: claim and a synthetic username like "microsoft365_gwww7jtfv4cb0km6n1y5qtda";
#: walk the claims Entra actually populates, in order of how much they mean to
#: a person, and only accept a value that looks like an address.
EMAIL_CLAIMS = ("email", "upn", "preferred_username", "custom:email")

NO_EMAIL_LABEL = "an account with no email address"


@dataclass(frozen=True)
class Principal:
    """Who the ALB says is calling.

    Every field may be empty -- callers must not assume an email is present.
    Federated users routinely arrive without one.
    """

    email: str = ""
    sub: str = ""
    name: str = ""
    groups: tuple[str, ...] = field(default_factory=tuple)
    authenticated: bool = False

    def who(self) -> str:
        """Render the principal for a human, mirroring access.R's wording.

        Never show the synthetic Cognito username where a name belongs -- it
        looks broken and tells the reader nothing.
        """
        if self.email:
            return self.email
        if self.name:
            return self.name
        return NO_EMAIL_LABEL


def from_headers(headers: Mapping[str, str] | Iterable[tuple[str, str]]) -> Principal:
    """Build a principal from an ALB-authenticated request's headers."""
    lookup = _lower_keyed(headers)

    claims = decode_claims(lookup.get(HEADER_DATA, ""))
    sub = str(lookup.get(HEADER_IDENTITY, "") or "").strip()
    if not sub:
        sub = _claim_str(claims.get("sub"))

    return Principal(
        email=email_from_claims(claims),
        sub=sub,
        name=_claim_str(claims.get("name")),
        groups=groups_from_claims(decode_claims(lookup.get(HEADER_ACCESS_TOKEN, ""))),
        # A principal is authenticated if the ALB gave us anything at all to
        # go on. Only a request with no parseable identity is a 401.
        authenticated=bool(sub) or bool(claims),
    )


def decode_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload WITHOUT verifying its signature.

    See the module security note for why that is safe behind this ALB and
    what would change it. Anything malformed decodes to ``{}`` rather than
    raising: a duff header must produce "no identity", never a 500.
    """
    token = (token or "").strip()
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def email_from_claims(claims: Mapping[str, Any]) -> str:
    """First claim that looks like an address, lowercased and trimmed, or ""."""
    for key in EMAIL_CLAIMS:
        value = _claim_str(claims.get(key)).strip().lower()
        if "@" in value:
            return value
    return ""


def groups_from_claims(claims: Mapping[str, Any]) -> tuple[str, ...]:
    """Read ``cognito:groups``.

    Groups live in the ACCESS token, not in x-amzn-oidc-data: the ALB builds
    oidc-data from Cognito's userinfo endpoint, which returns standard OIDC
    claims only and omits groups entirely. Nothing in the proxy's access modes
    uses groups yet; they are parsed so audit and a future group mode have
    them.
    """
    raw = claims.get("cognito:groups")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(g for g in (_claim_str(item) for item in raw) if g)


def _b64url_decode(segment: str) -> bytes:
    # Same padding fix as the portal Lambda's _b64url_decode.
    segment += "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment.encode("ascii"))


def _claim_str(value: Any) -> str:
    # Only strings count. A claim that arrives as a number or an object is not
    # an email address, and str()-ing it would invent one.
    return value if isinstance(value, str) else ""


def _lower_keyed(headers: Mapping[str, str] | Iterable[tuple[str, str]]) -> dict[str, str]:
    items = headers.items() if hasattr(headers, "items") else headers
    return {str(key).lower(): value for key, value in items}
