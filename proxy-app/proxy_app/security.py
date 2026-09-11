"""The security headers every response this service emits carries.

One module rather than a literal at each `web.Response(...)` because there are
five kinds of response here -- the branded pages, the JSON API, the React
bundle's files, the plain `/__proxy/` replies, and whatever an app upstream
sent -- and a header that is on four of them protects nothing.

--- why not aiohttp middleware -----------------------------------------------

The obvious implementation is a middleware that stamps the headers on whatever
the handler returned. It does not work here: `server.Proxy._proxy_http` calls
`response.prepare(request)` itself and streams the body from inside the
handler, so by the time a middleware saw the object its headers would already
be on the wire. Headers are therefore set where each response is BUILT, and
this module is the single place that decides what they are.

--- what is set, and why -----------------------------------------------------

* ``Content-Security-Policy: frame-ancestors 'none'`` -- the modern control.
  Nothing may put this service, or any app behind it, in an iframe. An
  authenticated Shiny session inside a hostile frame is a clickjacking target:
  the victim is signed in, the app's controls do real things, and the frame
  decides where the clicks land.

  **``frame-ancestors`` only.** A full CSP with ``script-src`` would break
  every app on the platform -- Shiny writes inline ``<script>`` blocks into
  the document it serves, and htmlwidgets inline more. Do not "complete" this
  policy without testing an actual app.

* ``X-Frame-Options: DENY`` -- the same statement for anything that does not
  implement ``frame-ancestors``. Redundant on a current browser, free, and
  the header a security questionnaire asks about by name.

* ``Referrer-Policy: strict-origin-when-cross-origin`` -- an app's hostname is
  the thing this platform is trying not to leak (see `creation.host_suffix`).
  Without this, every outbound link in a dashboard hands the destination site
  the full URL it was clicked from.

* ``X-Content-Type-Options: nosniff`` -- already set on the pages and the API
  before this module existed; collected here so there is one list.
"""

from __future__ import annotations

from typing import Any, Mapping

CSP_HEADER = "Content-Security-Policy"
FRAME_OPTIONS_HEADER = "X-Frame-Options"
REFERRER_HEADER = "Referrer-Policy"
CONTENT_TYPE_OPTIONS_HEADER = "X-Content-Type-Options"

#: The only directive in our policy. See the module banner before adding one.
FRAME_ANCESTORS = "frame-ancestors 'none'"

FRAME_OPTIONS = "DENY"
REFERRER_POLICY = "strict-origin-when-cross-origin"
CONTENT_TYPE_OPTIONS = "nosniff"

#: What every response the proxy composes itself carries: the branded pages,
#: the JSON API, the bundle's files, the plain `/__proxy/` replies.
OWN_RESPONSE_HEADERS: dict[str, str] = {
    CSP_HEADER: FRAME_ANCESTORS,
    FRAME_OPTIONS_HEADER: FRAME_OPTIONS,
    REFERRER_HEADER: REFERRER_POLICY,
    CONTENT_TYPE_OPTIONS_HEADER: CONTENT_TYPE_OPTIONS,
}


def headers(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The security headers, plus whatever this particular response adds.

    ``extra`` wins on a collision so a caller can still say something more
    specific (a `Cache-Control`, a `Retry-After`), but nothing in this
    codebase overrides a security header that way -- and if something ever
    tries, it is one grep from here.
    """
    merged = dict(OWN_RESPONSE_HEADERS)
    merged.update(extra or {})
    return merged


def harden(out: Any) -> None:
    """Stamp the headers onto a response RELAYED from an upstream app.

    Mutates ``out`` (a ``CIMultiDict`` of the upstream's headers) in place.

    The rule, decided here once so it is not re-decided per header:

    * **Framing: the proxy overrides, always.** Whether an app may be framed
      is a platform decision, not an app's -- an app that shipped
      ``X-Frame-Options: ALLOWALL`` would otherwise opt its users out of the
      protection. Any upstream ``X-Frame-Options`` is dropped and replaced.
      Two ``X-Frame-Options`` values is not "more secure", it is undefined:
      the header has no composition rule and browsers disagree about which
      one (if either) applies, so removing the old one is mandatory rather
      than tidy.
    * **CSP: merged, never duplicated.** An app's own policy is kept -- it may
      carry ``script-src`` or ``img-src`` rules that are none of our business
      -- but any ``frame-ancestors`` directive it declared is stripped out
      and ours is appended as a further policy. The result is exactly ONE
      ``Content-Security-Policy`` header holding comma-separated policies,
      which is the spec's own equivalent of sending several: each policy is
      enforced independently, so the app cannot loosen ours and we have not
      loosened theirs.
    * **Referrer-Policy: the app wins if it said anything.** It is not a
      platform control and an app that set ``no-referrer`` chose something
      stricter than our default. Ours is a floor for apps that are silent.

    ``Content-Security-Policy-Report-Only`` is left alone: it enforces
    nothing, so it cannot weaken this.
    """
    policies: list[str] = []
    for value in _popall(out, CSP_HEADER):
        for policy in value.split(","):
            kept = strip_frame_ancestors(policy)
            if kept:
                policies.append(kept)
    policies.append(FRAME_ANCESTORS)
    out[CSP_HEADER] = ", ".join(policies)

    _popall(out, FRAME_OPTIONS_HEADER)
    out[FRAME_OPTIONS_HEADER] = FRAME_OPTIONS

    if REFERRER_HEADER not in out:
        out[REFERRER_HEADER] = REFERRER_POLICY


def strip_frame_ancestors(policy: str) -> str:
    """One CSP policy with any ``frame-ancestors`` directive removed.

    Returns "" when nothing survives, so a policy that said only
    ``frame-ancestors https://evil.example`` disappears rather than being
    relayed as an empty policy.
    """
    kept = []
    for directive in policy.split(";"):
        name = directive.strip().split(" ", 1)[0].lower()
        if not directive.strip() or name == "frame-ancestors":
            continue
        kept.append(directive.strip())
    return "; ".join(kept)


def _popall(out: Any, name: str) -> list[str]:
    """Every value for ``name``, removed from ``out``. [] when there are none."""
    try:
        return [str(value) for value in out.popall(name)]
    except KeyError:
        return []


__all__ = [
    "CONTENT_TYPE_OPTIONS",
    "CONTENT_TYPE_OPTIONS_HEADER",
    "CSP_HEADER",
    "FRAME_ANCESTORS",
    "FRAME_OPTIONS",
    "FRAME_OPTIONS_HEADER",
    "OWN_RESPONSE_HEADERS",
    "REFERRER_HEADER",
    "REFERRER_POLICY",
    "harden",
    "headers",
    "strip_frame_ancestors",
]
