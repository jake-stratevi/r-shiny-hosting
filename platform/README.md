# Platform stack (shared)

Deploy this **once**, before either app. It owns everything the apps share, and
publishes its wiring to SSM Parameter Store so the app stacks can find it
without needing access to this stack's Terraform state.

| Resource | Why it's shared |
|---|---|
| VPC, public subnets, security groups | One network for everything |
| **Application Load Balancer** | $16.43/mo base. A per-app ALB would add $16/mo per app for nothing |
| Wildcard ACM cert `*.<domain>` | Covers every current and future app hostname; apps never touch ACM |
| Cognito user pool + hosted domain | One identity for all tools. Apps create their own *client* |
| ECS cluster | Costs nothing; only running tasks bill |
| Task execution role, task role, scaler role | Same permissions for every app |
| Budget alert | Watches total project spend |

**Cost: ~$20/month, and it never scales to zero.** That is the floor for the
whole platform, no matter how many apps you add.

## No NAT Gateway, deliberately

Tasks run in public subnets with public IPs and a security group that only
accepts traffic from the ALB. A NAT Gateway would add $32.85/month plus
$0.045/GB, more than doubling the bill of a stack whose entire point is being
small. If a security review demands private subnets, add interface endpoints
for `ecr.api`, `ecr.dkr` and `logs` ($0.01/hr per AZ each) plus the free S3
gateway endpoint.

## Deploy

```bash
cp terraform.tfvars.example terraform.tfvars   # edit it
terraform init
terraform apply
```

Prerequisite: a Route 53 public hosted zone you control, matching `domain_name`.

## Invite a user

```bash
aws cognito-idp admin-create-user \
  --user-pool-id $(terraform output -raw cognito_user_pool_id) \
  --username someone@client.com \
  --user-attributes Name=email,Value=someone@client.com Name=email_verified,Value=true
```

## Listener rule priority register

Every app puts a rule on the shared HTTPS listener and priorities must be
unique. Keep the register here:

| Priority | App |
|---|---|
| 100 | dashboard |
| 200 | model |
| 300 | *next app* |

## Destroy order

Destroy the app stacks first. This stack owns the listener their rules attach
to, so tearing it down underneath them leaves orphaned resources.
