"""The six branded responses the proxy serves itself.

They are packaged with the app (``page.html``) and reference nothing external:
ADR-0014 requires the 404/expired/starting pages to work when the database is
unhappy, and a page that fetches a stylesheet from an app that is asleep is
not a page that works. Stratevi design language -- system fonts, neutral
palette -- and deliberately no Assembled Intelligence branding.

The template is a :class:`string.Template`, so the CSS's braces need no
escaping; every value substituted into it is HTML-escaped first.
"""

from __future__ import annotations

import html
from importlib import resources
from string import Template
from typing import Sequence

from aiohttp import web

#: Who a refused user is told to ask. Matches access.R's ACCESS_CONTACT
#: default rather than naming an address the proxy has no way to know per app.
DEFAULT_CONTACT = "your Stratevi contact"

#: The meta-refresh interval on the cold-start page, in seconds.
STARTING_REFRESH = 3

_TEMPLATE = Template(
    resources.files(__package__).joinpath("page.html").read_text(encoding="utf-8")
)


def render(
    status: int,
    *,
    title: str,
    heading: str,
    paragraphs: Sequence[str],
    mono: str = "",
    refresh: int = 0,
    retry_after: int = 0,
) -> web.Response:
    """Build one branded page as a complete aiohttp response."""
    body = _TEMPLATE.substitute(
        refresh=(
            f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
        ),
        title=html.escape(title),
        heading=html.escape(heading),
        bar='<div class="bar"><span></span></div>' if refresh else "",
        paragraphs="".join(f"<p>{html.escape(text)}</p>" for text in paragraphs),
        mono=f"<p><code>{html.escape(mono)}</code></p>" if mono else "",
    )

    headers = {
        # These pages are decisions about one caller at one moment. Never let
        # a browser or an intermediary keep one.
        "Cache-Control": "no-store, must-revalidate",
        "X-Content-Type-Options": "nosniff",
    }
    if retry_after:
        headers["Retry-After"] = str(int(retry_after))

    return web.Response(
        status=status,
        text=body,
        content_type="text/html",
        charset="utf-8",
        headers=headers,
    )


def starting(label: str = "") -> web.Response:
    """Served while a sleeping app's task comes up.

    HTTP 200, not 503: this is a working page telling a person what is
    happening, every browser renders and meta-refreshes it without argument,
    and some browsers cache a 503 for the retry window and never come back.
    """
    return render(
        200,
        title="Starting up",
        heading="Starting up",
        paragraphs=[
            "This application was asleep to keep hosting costs down. Starting "
            "it takes about 30 to 60 seconds.",
            "This page refreshes itself every few seconds and will load the "
            "application as soon as it answers. You do not need to do anything.",
        ],
        mono=label,
        refresh=STARTING_REFRESH,
        retry_after=STARTING_REFRESH,
    )


def not_signed_in() -> web.Response:
    """The 401. Should never be seen: the ALB authenticates before forwarding.

    It exists because failing closed means having something to fail closed to.
    """
    return render(
        401,
        title="Not signed in",
        heading="You are not signed in",
        paragraphs=[
            "This application is behind Stratevi single sign-on, and the "
            "request arrived without a verified identity.",
            "Close the tab and open the link again. If that keeps happening, "
            f"tell {DEFAULT_CONTACT} -- it means something is wrong with the "
            "sign-in rule, not with you.",
        ],
    )


def no_access(who: str = "") -> web.Response:
    """The 403: signed in, but not entitled to this app."""
    return render(
        403,
        title="No access",
        heading="You do not have access to this application",
        paragraphs=[
            "You are signed in, but this account is not on the access list "
            "for this application.",
            f"If you think this is wrong, contact {DEFAULT_CONTACT} and "
            "mention the address below.",
        ],
        mono=who,
    )


def expired(disabled: bool = False) -> web.Response:
    """The 410, covering both expired and disabled apps.

    From the visitor's side they are the same event, and the proxy answers
    directly rather than starting a container in order to refuse someone.
    """
    if disabled:
        heading = "This application has been turned off"
        body = "It has been disabled by its owner and is no longer available."
    else:
        heading = "This application has expired"
        body = (
            "It was published with an end date, and that date has passed. The "
            "application has been shut down and is no longer available."
        )
    return render(
        410,
        title=heading,
        heading=heading,
        paragraphs=[body, f"If you still need it, contact {DEFAULT_CONTACT}."],
    )


def unknown_host(host: str = "") -> web.Response:
    """The 404 for a hostname with no app behind it.

    The wildcard listener rule catches every ``*.tools.stratevi.com`` name,
    including ones nobody has provisioned and ones that were deleted.
    """
    return render(
        404,
        title="No application here",
        heading="There is no application at this address",
        paragraphs=[
            "The address resolved to Stratevi hosting, but no application is "
            "published on it. It may have been removed, or the link may be "
            "mistyped."
        ],
        mono=host,
    )


def unhealthy() -> web.Response:
    """The 503: the app should be reachable and is not, or the control
    plane's own dependencies are failing. Also the fail-closed answer to a
    store error -- never open."""
    return render(
        503,
        title="Temporarily unavailable",
        heading="This application is temporarily unavailable",
        paragraphs=[
            "It is published and you have access to it, but it is not "
            "answering right now.",
            f"Try again in a minute. If it stays like this, tell {DEFAULT_CONTACT}.",
        ],
        retry_after=15,
    )
