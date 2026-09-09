"""
Portal landing page for dashboards.tools.stratevi.com.

Sits behind the same ALB authenticate-cognito action every other app uses.
The ALB decodes the sign-in and forwards the request to this Lambda with the
caller's identity in headers; this function reads them, decides which apps
in the shared catalog (../catalog.json, generated from the repo's
catalog.yaml by lambda.tf) that person may see, and renders a small menu
linking out to each app's own subdomain.

This does NOT enforce access to the apps themselves -- each app still makes
its own decision from its own allowlist (ADR-0008, access.R). This page only
decides what to SHOW. Hiding a tile here is a convenience, not a security
boundary; the real gate is unchanged and lives in each app.

SECURITY NOTE -- mirrors access.R's reasoning exactly: x-amzn-oidc-data's
JWT signature is not verified here. A Lambda target group can only be
invoked by the ALB itself (see the aws_lambda_permission resource scoping
invocation to this specific target group ARN) -- there is no path for a
forged header to arrive. If that ever changes (a Function URL, a second
trigger), verify the signature against
https://public-keys.auth.elb.<region>.amazonaws.com/<kid> before trusting it.
"""

import base64
import json
import os

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "catalog.json")
with open(_CATALOG_PATH) as _f:
    CATALOG = json.load(_f)


def _b64url_decode(segment):
    segment += "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment.encode("ascii"))


def _claims(token):
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        return json.loads(_b64url_decode(parts[1]))
    except Exception:
        return {}


def _email(claims):
    # Same fallback order as access.R: a federated user often has no `email`
    # claim, just a synthetic username and an `upn`/`preferred_username`.
    for key in ("email", "upn", "preferred_username", "custom:email"):
        value = str(claims.get(key) or "").strip().lower()
        if "@" in value:
            return value
    return ""


def _visible_apps(email):
    visible = []
    for app in CATALOG.get("apps", []):
        mode = app.get("access_mode", "users")
        if mode == "all_users":
            visible.append(app)
        elif mode == "users" and email and email in {
            e.strip().lower() for e in app.get("allowed_emails", [])
        }:
            visible.append(app)
    return visible


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render(email, apps):
    if apps:
        cards = "".join(
            '<a class="card" href="{url}">'
            "<h2>{label}</h2><p>{description}</p>"
            "</a>".format(
                url=_escape(app.get("url", "#")),
                label=_escape(app.get("label", app.get("key", "Untitled"))),
                description=_escape(app.get("description", "")),
            )
            for app in apps
        )
    else:
        cards = (
            '<p class="empty">No dashboards are shared with your account yet. '
            "Contact your Stratevi admin if you think that's wrong.</p>"
        )

    who = _escape(email) if email else "an account with no email address"

    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stratevi Dashboards</title>
<style>
  body {{
    font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    background: #f7f8fa; color: #1c2230; margin: 0; padding: 48px 40px;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #66708a; font-size: 13px; margin: 0 0 32px; }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 16px; max-width: 900px;
  }}
  .card {{
    display: block; background: #fff; border: 1px solid #e2e6ee;
    border-radius: 10px; padding: 20px 22px; text-decoration: none;
    color: inherit; box-shadow: 0 1px 3px rgba(20,30,60,.06);
  }}
  .card h2 {{ font-size: 15px; margin: 0 0 6px; }}
  .card p {{ font-size: 13px; color: #55607a; margin: 0; line-height: 1.5; }}
  .empty {{ color: #66708a; font-size: 14px; }}
</style>
</head>
<body>
  <h1>Stratevi Dashboards</h1>
  <p class="sub">Signed in as {who}</p>
  <div class="grid">{cards}</div>
</body>
</html>""".format(who=who, cards=cards)


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    claims = _claims(headers.get("x-amzn-oidc-data"))
    email = _email(claims)
    apps = _visible_apps(email)

    return {
        "statusCode": 200,
        "statusDescription": "200 OK",
        "isBase64Encoded": False,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": _render(email, apps),
    }
