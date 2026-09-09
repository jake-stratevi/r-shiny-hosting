resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.project}/${var.app_key}"
  retention_in_days = local.log_retention_days
}

# ---------------------------------------------------------------------------
# SHINY_CPU_WORKERS matters more than it looks.
#
# parallel::detectCores() reads the HOST's /proc/cpuinfo, not the Fargate task
# limit, so on a 4 vCPU task it can report 16, 32 or more. The app must read
# this variable instead of calling detectCores(). See README.
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = data.aws_ssm_parameter.task_execution_role_arn.value

  # Per-app role from iam.tf, not the shared platform one -- ADR-0010. The
  # execution role above stays shared; it only pulls images and writes logs.
  task_role_arn = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # ARM64 is ~15-20% cheaper if you build arm images
  }

  container_definitions = jsonencode([{
    name      = var.app_key
    image     = "${aws_ecr_repository.this.repository_url}:${var.image_tag}"
    essential = true

    portMappings = [{
      containerPort = var.container_port
      protocol      = "tcp"
    }]

    environment = [
      { name = "SHINY_CPU_WORKERS", value = tostring(local.cpu_workers) },
      { name = "DEBUG_RUNMODEL", value = "FALSE" },
      { name = "APP_NAME", value = var.app_key },
      { name = "R_MAX_VSIZE", value = "${floor(var.memory * 0.8)}Mb" },

      # Per-app authorization. The ALB authenticates but cannot decide WHO may
      # see THIS app -- authenticate-cognito is binary. access.R reads the
      # identity the ALB injects and checks it against these.
      # See docs/adr/0008-authorization-strategy.md.
      { name = "APP_LABEL", value = var.app_label },
      { name = "ACCESS_MODE", value = var.access_mode },
      { name = "ALLOWED_EMAILS", value = join(",", var.allowed_emails) },
      { name = "ALLOWED_GROUPS", value = join(",", var.allowed_groups) },
      { name = "ACCESS_CONTACT", value = var.access_contact },
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
      command     = ["CMD-SHELL", "curl -fsS http://localhost:${var.container_port}/ || exit 1"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
  }])
}

# ---------------------------------------------------------------------------
# Deployed at desired_count 0. The waker and sleeper own it from then on, so
# Terraform ignores it. Terraform owns the task definition and wiring.
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "this" {
  name            = local.name
  cluster         = data.aws_ssm_parameter.ecs_cluster_arn.value
  task_definition = aws_ecs_task_definition.this.arn
  launch_type     = "FARGATE"
  desired_count   = 0

  enable_execute_command = true

  # Give a cold-starting R container time to load its packages before the load
  # balancer starts failing it.
  health_check_grace_period_seconds = 120

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [data.aws_ssm_parameter.task_security_group_id.value]
    assign_public_ip = true # no NAT Gateway; see platform/network.tf
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.ecs.arn
    container_name   = var.app_key
    container_port   = var.container_port
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener_rule.ecs_association]
}
