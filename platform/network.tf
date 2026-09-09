# ---------------------------------------------------------------------------
# Deliberately NO NAT Gateway. Fargate tasks sit in public subnets with public
# IPs so they can pull from ECR and write logs directly. A NAT Gateway adds
# ~$32.85/mo plus $0.045/GB, which would more than double the bill of a
# scale-to-zero stack. Tasks are not reachable from the internet: their
# security group only accepts traffic from the ALB security group.
#
# If a security review demands private subnets, add interface endpoints for
# ecr.api, ecr.dkr and logs ($0.01/hr per AZ each) plus the free S3 gateway
# endpoint. Still cheaper than NAT.
# ---------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  count = length(var.public_subnet_cidrs)

  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.name}-public-${count.index + 1}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- Security groups --------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public ingress to the shared load balancer"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name}-alb" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  count = length(var.alb_allowed_cidrs)

  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = var.alb_allowed_cidrs[count.index]
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  description       = "HTTPS"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  count = length(var.alb_allowed_cidrs)

  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = var.alb_allowed_cidrs[count.index]
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  description       = "HTTP, redirected to HTTPS"
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "Fargate tasks for every app. Ingress from the ALB only."
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name}-tasks" }
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 3838
  to_port                      = 3838
  ip_protocol                  = "tcp"
  description                  = "Shiny Server from ALB only"
}

resource "aws_vpc_security_group_egress_rule" "tasks_all" {
  security_group_id = aws_security_group.tasks.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "ECR pulls, CloudWatch Logs, outbound HTTPS"
}
