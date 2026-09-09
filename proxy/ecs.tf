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
  referenced_security_group_id = data.aws_security_group.alb.id
  from_port                    = var.container_port
  to_port                      = var.container_port
  ip_protocol                  = "tcp"
  description                  = "Proxy service from ALB only"
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

    # Config contract per docs/design/proxy.md. Everything the Go service
    # needs to run comes from here -- no per-app config, the proxy reads app
    # rows out of APPS_TABLE at request time.
    environment = [
      { name = "AWS_REGION", value = var.region },
      { name = "ECS_CLUSTER", value = data.aws_ssm_parameter.ecs_cluster_name.value },
      { name = "APPS_TABLE", value = aws_dynamodb_table.apps.name },
      { name = "AUDIT_TABLE", value = aws_dynamodb_table.audit.name },
      { name = "PORT", value = tostring(var.container_port) },
      { name = "LOG_LEVEL", value = var.log_level },
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
      command     = ["CMD-SHELL", "curl -fsS http://localhost:${var.container_port}/__proxy/healthz || exit 1"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 15 # a static Go binary needs no package-loading grace period, unlike the R apps
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
