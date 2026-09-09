"""
Waker: the ALB's forward target while an app is scaled to zero.

Flow, per authenticated request:
  1. If the service is at desiredCount 0, set it to 1.
  2. Check whether the ECS target group has a healthy target yet.
  3. If it does, rewrite the listener rule to forward to the ECS target group
     and return a page that refreshes immediately.
  4. If it does not, return a holding page that refreshes in a few seconds.

The browser's own refresh is the polling loop, so this Lambda never needs to
block waiting for the container. Each invocation is milliseconds.
"""

import json
import os

import boto3

ecs = boto3.client("ecs")
elbv2 = boto3.client("elbv2")

CLUSTER = os.environ["CLUSTER"]
SERVICE = os.environ["SERVICE"]
RULE_ARN = os.environ["RULE_ARN"]
ECS_TG_ARN = os.environ["ECS_TG_ARN"]
APP_LABEL = os.environ.get("APP_LABEL", "Application")
COGNITO = json.loads(os.environ["COGNITO_CONFIG"])


def listener_actions(target_group_arn):
    """Rebuild the full action list. The auth action must be preserved on every
    modify_rule call, otherwise swapping the target silently drops Cognito."""
    return [
        {
            "Type": "authenticate-cognito",
            "Order": 1,
            "AuthenticateCognitoConfig": {
                "UserPoolArn": COGNITO["user_pool_arn"],
                "UserPoolClientId": COGNITO["client_id"],
                "UserPoolDomain": COGNITO["domain"],
                "OnUnauthenticatedRequest": "authenticate",
                "Scope": "openid email profile",
                "SessionTimeout": 43200,
            },
        },
        {
            "Type": "forward",
            "Order": 2,
            "TargetGroupArn": target_group_arn,
        },
    ]


def response(html, status=200):
    return {
        "statusCode": status,
        "statusDescription": f"{status} OK",
        "isBase64Encoded": False,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        },
        "body": html,
    }


def holding_page(refresh_seconds, message):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{refresh_seconds}">
  <title>Starting {APP_LABEL}</title>
  <style>
    body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
           background: #f7f8fa; color: #1c2230; display: flex; align-items: center;
           justify-content: center; height: 100vh; margin: 0; }}
    .card {{ background: #fff; border: 1px solid #e2e6ee; border-radius: 10px;
            padding: 40px 48px; max-width: 460px; text-align: center;
            box-shadow: 0 1px 3px rgba(20,30,60,.06); }}
    h1 {{ font-size: 18px; margin: 0 0 10px; font-weight: 600; }}
    p  {{ font-size: 14px; line-height: 1.55; color: #55607a; margin: 0; }}
    .bar {{ height: 3px; background: #e2e6ee; border-radius: 2px; overflow: hidden;
           margin: 24px 0 20px; }}
    .bar span {{ display: block; height: 100%; width: 40%; background: #3b6ce4;
                animation: slide 1.4s ease-in-out infinite; }}
    @keyframes slide {{ 0% {{ transform: translateX(-100%); }}
                        100% {{ transform: translateX(300%); }} }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Starting {APP_LABEL}</h1>
    <div class="bar"><span></span></div>
    <p>{message}</p>
  </div>
</body>
</html>"""


def handler(event, context):
    services = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"]
    if not services:
        return response("<h1>Service not found</h1>", 500)

    service = services[0]

    if service["desiredCount"] == 0:
        print(f"scaling {SERVICE} up to 1")
        ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=1)

    health = elbv2.describe_target_health(TargetGroupArn=ECS_TG_ARN)
    healthy = [
        t
        for t in health["TargetHealthDescriptions"]
        if t["TargetHealth"]["State"] == "healthy"
    ]

    if healthy:
        print("target healthy, pointing listener rule at ECS target group")
        elbv2.modify_rule(RuleArn=RULE_ARN, Actions=listener_actions(ECS_TG_ARN))
        return response(holding_page(1, "Ready. Loading now."))

    return response(
        holding_page(
            5,
            "This tool sleeps when nobody is using it, which keeps hosting costs "
            "down. It usually takes 60 to 90 seconds to come back. This page will "
            "refresh on its own.",
        )
    )
