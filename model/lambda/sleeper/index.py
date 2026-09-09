"""
Sleeper for a single app. Runs on a schedule and does two jobs.

  1. Warm window. If this app is configured for scheduled warmth and we are
     inside the window, make sure it is running and the listener rule points at
     the ECS target group. Nobody pays a cold start during working hours.

  2. Idle scale-down. Outside the warm window, if the app has seen no traffic
     for IDLE_MINUTES, point the listener rule back at the waker and set
     desiredCount to 0.

Idle is measured with RequestCountPerTarget, NOT active connections, and this
only works because the app sends a heartbeat. A Shiny session sitting on an
open websocket during a long parLapply run emits zero HTTP requests and would
otherwise look idle, so the sleeper would kill a user mid-simulation. The app
MUST include the heartbeat snippet from the README.

MIN_UPTIME_MINUTES protects a task that just started but has not yet
accumulated metric datapoints.
"""

import datetime
import json
import os

import boto3

ecs = boto3.client("ecs")
elbv2 = boto3.client("elbv2")
cw = boto3.client("cloudwatch")

APP_NAME = os.environ["APP_NAME"]
CLUSTER = os.environ["CLUSTER"]
SERVICE = os.environ["SERVICE"]
RULE_ARN = os.environ["RULE_ARN"]
ECS_TG_ARN = os.environ["ECS_TG_ARN"]
WAKER_TG_ARN = os.environ["WAKER_TG_ARN"]
TG_DIMENSION = os.environ["TG_DIMENSION"]
ALB_DIMENSION = os.environ["ALB_DIMENSION"]

IDLE_MINUTES = int(os.environ["IDLE_MINUTES"])
MIN_UPTIME_MINUTES = int(os.environ["MIN_UPTIME_MINUTES"])
WARM_ENABLED = os.environ.get("WARM_ENABLED", "false").lower() == "true"
WARM_DAYS = set(json.loads(os.environ.get("WARM_DAYS", "[]")))
WARM_START_UTC = int(os.environ.get("WARM_START_UTC", "0"))
WARM_END_UTC = int(os.environ.get("WARM_END_UTC", "0"))

COGNITO = json.loads(os.environ["COGNITO_CONFIG"])


def listener_actions(target_group_arn):
    """The auth action must be preserved on every modify_rule call, otherwise
    swapping the target silently drops Cognito from the rule."""
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
        {"Type": "forward", "Order": 2, "TargetGroupArn": target_group_arn},
    ]


def in_warm_window(now):
    if not WARM_ENABLED or not WARM_DAYS:
        return False

    # A window that wraps past midnight UTC belongs to the day it started on.
    if WARM_START_UTC <= WARM_END_UTC:
        return now.weekday() in WARM_DAYS and WARM_START_UTC <= now.hour < WARM_END_UTC

    if now.hour >= WARM_START_UTC:
        return now.weekday() in WARM_DAYS
    return ((now.weekday() - 1) % 7) in WARM_DAYS and now.hour < WARM_END_UTC


def running_task_age_minutes(now):
    """Minutes since the oldest running task started, or None if none run."""
    arns = ecs.list_tasks(
        cluster=CLUSTER, serviceName=SERVICE, desiredStatus="RUNNING"
    )["taskArns"]
    if not arns:
        return None

    tasks = ecs.describe_tasks(cluster=CLUSTER, tasks=arns)["tasks"]
    starts = [t["startedAt"] for t in tasks if t.get("startedAt")]
    if not starts:
        return 0

    oldest = min(starts).replace(tzinfo=datetime.timezone.utc)
    return (now - oldest).total_seconds() / 60


def request_count(now):
    stats = cw.get_metric_statistics(
        Namespace="AWS/ApplicationELB",
        MetricName="RequestCountPerTarget",
        Dimensions=[
            {"Name": "TargetGroup", "Value": TG_DIMENSION},
            {"Name": "LoadBalancer", "Value": ALB_DIMENSION},
        ],
        StartTime=now - datetime.timedelta(minutes=IDLE_MINUTES),
        EndTime=now,
        Period=60,
        Statistics=["Sum"],
    )
    return sum(d["Sum"] for d in stats["Datapoints"])


def has_healthy_target():
    health = elbv2.describe_target_health(TargetGroupArn=ECS_TG_ARN)
    return any(
        t["TargetHealth"]["State"] == "healthy"
        for t in health["TargetHealthDescriptions"]
    )


def handler(event, context):
    now = datetime.datetime.now(datetime.timezone.utc)
    desired = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0][
        "desiredCount"
    ]

    if in_warm_window(now):
        if desired == 0:
            print(f"[{APP_NAME}] inside warm window, pre-warming")
            ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=1)
        elif has_healthy_target():
            elbv2.modify_rule(RuleArn=RULE_ARN, Actions=listener_actions(ECS_TG_ARN))
        return {"app": APP_NAME, "action": "warm"}

    if desired == 0:
        return {"app": APP_NAME, "action": "already-asleep"}

    age = running_task_age_minutes(now)
    if age is not None and age < MIN_UPTIME_MINUTES:
        print(f"[{APP_NAME}] running {age:.1f}m, under min uptime, leaving alone")
        return {"app": APP_NAME, "action": "too-young"}

    hits = request_count(now)
    if hits > 0:
        print(f"[{APP_NAME}] {hits:.0f} requests in last {IDLE_MINUTES}m, active")
        return {"app": APP_NAME, "action": "active", "requests": hits}

    print(f"[{APP_NAME}] idle for {IDLE_MINUTES}m, scaling to zero")
    elbv2.modify_rule(RuleArn=RULE_ARN, Actions=listener_actions(WAKER_TG_ARN))
    ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
    return {"app": APP_NAME, "action": "slept"}
