"""Signing out, which on this platform is two things and not one.

Authentication happens at the ALB, not in this service. The
``authenticate-cognito`` action on the listener rule (``proxy/alb.tf``) mints
its own session cookie on the app's domain and Cognito keeps a hosted-UI
session of its own. Clearing one without the other does nothing a user can
see:

1. **Expire the ALB session cookie.** It is called
   ``AWSELBAuthSessionCookie-0`` and the ALB SHARDS it into ``-1``, ``-2``,
   ... whenever the claims are too big for one 4 KB cookie -- which a
   federated Entra identity, with its long synthetic username and its
   ``upn``/``preferred_username`` claims, reliably is. Expire ``-0`` alone
   and the browser keeps sending the rest; the ALB reassembles them and the
   session survives.
2. **End the Cognito session**, by sending the browser to the hosted UI's
   ``/logout``. Skip this and the ALB's very next authenticate action
   completes silently against a session Cognito still holds, and sign-out
   looks like a page that flickered.

What this module CANNOT do, and does not pretend to: end the user's
Microsoft session. Entra is the upstream identity provider and its session
lives on Microsoft's domain, shared with Outlook and Teams in the same
browser. Ending the Cognito session is the truthful boundary of what this
platform owns -- and it is why :func:`proxy_app.pages.signed_out` says so in
plain words instead of leaving someone to discover it by clicking
"Microsoft365" and landing back inside.
"""

from __future__ import annotations

from typing import Iterable, Mapping
from urllib.parse import quote, urlencode

from .config import SignOut

#: The ALB's default session cookie name. Shards append ``-<n>``; the
#: unsharded form the docs use in examples is ``-0``, and a bare
#: ``AWSELBAuthSessionCookie`` is accepted here only so a hand-set or
#: legacy-named cookie is not left behind.
ALB_COOKIE = "AWSELBAuthSessionCookie"

#: The ALB sets its cookie with ``Path=/`` and NO ``Domain`` attribute --
#: a host-only cookie. A deletion must match on name, path and domain, and
#: "matching domain" for a host-only cookie means sending no ``Domain`` at
#: all: adding one would create (and immediately expire) a DIFFERENT,
#: domain-scoped cookie and leave the real session untouched.
COOKIE_PATH = "/"

#: Belt and braces. ``Max-Age=0`` is the modern instruction; ``Expires`` in
#: the past is what a browser that ignores ``Max-Age`` understands. Both say
#: the same thing and no browser honours only the one we left out.
EPOCH = "Thu, 01 Jan 1970 00:00:00 GMT"


def shard_names(cookies: Mapping[str, str] | Iterable[str]) -> list[str]:
    """Every ALB session cookie the browser sent, plus ``-0``, in shard order.

    ``-0`` is included whether or not it arrived: a request that reached a
    logout link almost certainly carried one, and expiring a cookie that is
    not there costs one header and removes a whole class of "it worked on my
    machine".
    """
    names = cookies.keys() if hasattr(cookies, "keys") else cookies

    found: list[str] = []
    for name in names:
        text = str(name)
        if text == ALB_COOKIE:
            found.append(text)
        elif text.startswith(ALB_COOKIE + "-") and text[len(ALB_COOKIE) + 1 :].isdigit():
            found.append(text)

    zero = f"{ALB_COOKIE}-0"
    if zero not in found:
        found.append(zero)

    # Numeric, not lexicographic: shard 10 comes after shard 9, and an
    # ordering that reads oddly in a header dump gets "tidied" eventually.
    return sorted(set(found), key=_shard_order)


def _shard_order(name: str) -> tuple[int, int]:
    suffix = name[len(ALB_COOKIE) + 1 :]
    return (0, -1) if not suffix else (0, int(suffix))


def expiry_headers(cookies: Mapping[str, str] | Iterable[str]) -> list[str]:
    """One ``Set-Cookie`` value per shard, each of them already expired.

    ``Secure`` and ``HttpOnly`` mirror how the ALB set them; a deletion whose
    attributes disagree with the original is refused by some browsers and
    ignored by the rest. ``SameSite=Lax`` matches the top-level navigation
    this is always served on.
    """
    return [
        f"{name}=; Path={COOKIE_PATH}; Max-Age=0; Expires={EPOCH}; "
        "Secure; HttpOnly; SameSite=Lax"
        for name in shard_names(cookies)
    ]


def hosted_ui(settings: SignOut) -> str:
    """The hosted UI's origin, from a prefix or a full custom domain.

    Same rule the ALB applies to ``user_pool_domain``: a value with a dot in
    it is already a hostname, and one without it is the prefix Cognito
    registered under ``.auth.<region>.amazoncognito.com``.
    """
    domain = settings.domain.strip().rstrip("/")
    # Tolerated, not encouraged: someone will eventually paste the whole URL
    # from the Cognito console into the variable, and refusing that with a
    # "https://https://" is a silly way to break sign-out.
    if "://" in domain:
        domain = domain.split("://", 1)[1]
    if "." in domain:
        return f"https://{domain}"
    return f"https://{domain}.auth.{settings.region}.amazoncognito.com"


def logout_url(settings: SignOut, signed_out_url: str) -> str:
    """``https://<hosted ui>/logout?client_id=...&logout_uri=...``

    ``logout_uri`` is percent-encoded in full (``quote`` with no safe
    characters, not ``quote_plus``): Cognito compares the decoded value
    against the client's LogoutURLs list character for character, and a
    ``+`` where a space was, or a bare ``:``/``/``, is how that comparison
    starts failing in ways that only show up in the browser.
    """
    target = (settings.signed_out_url or signed_out_url).strip()
    query = urlencode(
        {"client_id": settings.client_id, "logout_uri": target}, quote_via=quote
    )
    return f"{hosted_ui(settings)}/logout?{query}"


__all__ = [
    "ALB_COOKIE",
    "COOKIE_PATH",
    "expiry_headers",
    "hosted_ui",
    "logout_url",
    "shard_names",
]
