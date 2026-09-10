# ---------------------------------------------------------------------------
# The proxy's own security group. It is NOT the shared platform
# task_security_group_id -- that one only opens 3838 (Shiny) from the ALB SG,
# and this task listens on 8080. Ingress from the ALB SG only, same shape as
# platform/network.tf's aws_security_group.tasks.
#
# Egress is open (no NAT Gateway; ADR-0004): the proxy calls ECR, CloudWatch
# Logs and DynamoDB over the public endpoint via its public IP, and reaches
# app tasks' private ENI IPs inside the VPC directly -- that last path is NOT
# opened here. It requires the APP stacks' security group to accept ingress
# from THIS security group on the container port, which is a one-line change
# made to dashboard/model at migration time (out of scope for this stack; do
# not add it here).
# ---------------------------------------------------------------------------

resource "aws_security_group" "proxy" {
  name        = "shiny-proxy"
  description = "Authorizing proxy task. Ingress from the ALB only."
  vpc_id      = data.aws_ssm_parameter.vpc_id.value
  tags        = { Name = "shiny-proxy" }
}

resource "aws_vpc_security_group_ingress_rule" "proxy_from_alb" {
  security_group_id            = aws_security_group.proxy.id
  referenced_security_group_id = data.aws_ssm_parameter.alb_security_group_id.value
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
  description                  = "Proxy service from ALB only"
}

# The proxy forwards straight to app task IPs (docs/design/proxy.md), so the
# SHARED tasks SG -- which otherwise only admits the ALB on 3838 -- must also
# admit the proxy. Owned here, not in platform or the app stacks: the rule
# exists because the proxy exists, and dies with it.
resource "aws_vpc_security_group_ingress_rule" "apps_from_proxy" {
  security_group_id            = data.aws_ssm_parameter.task_security_group_id.value
  referenced_security_group_id = aws_security_group.proxy.id
  from_port                    = 3838
  to_port                      = 3838
  ip_protocol                  = "tcp"
  description                  = "App tasks from the authorizing proxy (direct-to-task routing)"
}

resource "aws_vpc_security_group_egress_rule" "proxy_all" {
  security_group_id = aws_security_group.proxy.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "ECR pulls, CloudWatch Logs, DynamoDB, app task ENIs"
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.project}/proxy"
  retention_in_days = local.log_retention_days
}

# ONE log group shared by every portal-created app, streams separated by the
# app key. Terraform owns it rather than the portal because a log group is
# the one piece of a created app that must OUTLIVE it: if the portal made
# the group and a delete removed it, the logs explaining why an app died
# would go with it. It also keeps the boundary policy's log-write grant
# (boundary.tf) matchable by a single ARN pattern.
resource "aws_cloudwatch_log_group" "apps" {
  name              = "/ecs/${var.project}/apps"
  retention_in_days = local.log_retention_days
}

resource "aws_ecs_task_definition" "this" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = data.aws_ssm_parameter.task_execution_role_arn.value
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "proxy"
    image     = "${aws_ecr_repository.this.repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{
      containerPort = var.container_port
      protocol      = "tcp"
    }]

    # Config contract per docs/design/proxy.md (+ portal additions per
    # docs/design/portal-api.md). Everything the service needs to run comes
    # from here -- no per-app config, the proxy reads app rows out of
    # APPS_TABLE at request time. PORTAL_HOSTS are answered by the portal UI
    # and API instead of being proxied to an app; the menu host only actually
    # arrives here once the ADR-0013 Lambda portal's rule 50 is retired.
    environment = [
      { name = "AWS_REGION", value = var.region },
      { name = "ECS_CLUSTER", value = data.aws_ssm_parameter.ecs_cluster_name.value },
      { name = "APPS_TABLE", value = aws_dynamodb_table.apps.name },
      { name = "AUDIT_TABLE", value = aws_dynamodb_table.audit.name },
      { name = "PORT", value = tostring(var.container_port) },
      { name = "LOG_LEVEL", value = var.log_level },
      { name = "PORTAL_HOSTS", value = join(",", local.portal_hosts) },

      # --- P2a pipeline (docs/design/portal-p2a.md) ---------------------
      # The four handles the creation wizard needs. Each corresponds to a
      # grant on the task role (iam.tf); if one of these is empty the
      # portal must refuse to create rather than guess a name.
      { name = "UPLOADS_BUCKET", value = aws_s3_bucket.uploads.id },
      { name = "CODEBUILD_PROJECT", value = aws_codebuild_project.app_build.name },
      { name = "APP_ROLE_BOUNDARY_ARN", value = aws_iam_policy.app_boundary.arn },

      # NAME ONLY -- this bucket does not exist yet. Moving app data out of
      # container images into S3 is a later change (ADR-0010's consequences;
      # portal.md defers the data-upload UI). The value is the name it WILL
      # have, so the per-app inline role policy the portal writes today
      # already points at the right prefix and nothing has to be reissued
      # when the bucket lands. Anything that tries to READ from it before
      # then gets NoSuchBucket, which is the correct, loud failure.
      { name = "APP_DATA_BUCKET", value = local.app_data_bucket },

      # What a created app's ECS service is made OF. The portal registers a
      # task definition and creates a service through the SDK (ADR-0012), so
      # it needs the same handles this stack uses for its own service. All
      # already exist here -- only APP_LOG_GROUP is new.
      #
      # config.py treats the whole creation block as all-or-nothing: if any
      # of these is blank the wizard is disabled and its routes answer 503,
      # rather than the task refusing to boot. That is deliberate -- this
      # container is in the request path for every app on the platform, and
      # a misconfigured wizard must never take the dashboard down with it.
      { name = "APP_DOMAIN", value = data.aws_ssm_parameter.domain_name.value },
      { name = "APP_SUBNET_IDS", value = join(",", local.subnet_ids) },
      { name = "APP_SECURITY_GROUP_ID", value = data.aws_ssm_parameter.task_security_group_id.value },
      { name = "APP_EXECUTION_ROLE_ARN", value = data.aws_ssm_parameter.task_execution_role_arn.value },
      { name = "APP_LOG_GROUP", value = aws_cloudwatch_log_group.apps.name },
      { name = "COGNITO_USER_POOL_ID", value = data.aws_ssm_parameter.cognito_user_pool_id.value },
      { name = "COGNITO_CLIENT_ID", value = aws_cognito_user_pool_client.this.id },

      # Sign-out needs the hosted UI's address to end the Cognito session
      # (expiring the ALB cookie alone just lets the ALB re-authenticate
      # from its own still-valid session). This SSM value is the domain
      # PREFIX; the service completes it with AWS_REGION, the same rule the
      # ALB's authenticate action uses.
      { name = "COGNITO_DOMAIN", value = data.aws_ssm_parameter.cognito_domain.value },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "task"
      }
    }

    healthCheck = {
      # python:3.12-slim ships no curl -- a CMD-SHELL curl exits 127 and ECS
      # kills the task ~90s after boot, in a loop. Probe with the stdlib
      # instead (same command as the Dockerfile HEALTHCHECK, which ECS
      # ignores -- only this block counts).
      command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${var.container_port}/__proxy/healthz', timeout=3).status == 200 else 1)\""]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 15
    }
  }])
}

# ---------------------------------------------------------------------------
# Terraform owns desired_count here. Unlike the app stacks there is no waker
# or sleeper Lambda mutating this service at runtime -- the proxy IS the
# thing that wakes and sleeps the apps, but nothing scales the proxy itself.
# See docs/design/proxy.md ("no lifecycle ignore blocks in its stack") and the
# CLAUDE.md rule about the two lifecycle blocks: that rule protects the APP
# stacks' blocks, it does not require one here. Do not add
# `lifecycle { ignore_changes = [desired_count] }` -- there is nothing to
# fight Terraform for ownership with.
#
# Rolling deploys must not drop the proxy to zero capacity (it is in the
# request path for every app), so unlike the app stacks' 0/200 this is
# 100/200: at least one task always up during a deployment.
# ---------------------------------------------------------------------------
resource "aws_ecs_service" "this" {
  name            = local.name
  cluster         = data.aws_ssm_parameter.ecs_cluster_arn.value
  task_definition = aws_ecs_task_definition.this.arn
  launch_type     = "FARGATE"
  desired_count   = var.desired_count

  enable_execute_command = true

  health_check_grace_period_seconds = 30

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [aws_security_group.proxy.id]
    assign_public_ip = true # no NAT Gateway; see platform/network.tf
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.proxy.arn
    container_name   = "proxy"
    container_port   = var.container_port
  }

  # ECS refuses to create a service against a target group with no listener
  # rule pointing at it yet ("does not have an associated load balancer" --
  # see docs/GOTCHAS.md). The app stacks work around this with a dummy
  # ecs_association rule on a *.invalid host because their REAL rule targets
  # the waker Lambda's target group instead. This stack has no waker, so the
  # real rule (alb.tf) already targets this target group -- just order after
  # it explicitly rather than relying on the implicit reference alone.
  depends_on = [aws_lb_listener_rule.this]
}
