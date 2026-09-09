# Deployment order

Three stacks. Apply the platform once, then each app independently, in any
order, by anyone.

```
platform/          shared, ~$20/mo, never sleeps
   │ publishes wiring to SSM: /<project>/platform/*
   ├──► dashboard/    reads SSM, ~$8/mo
   └──► model/        reads SSM, ~$10/mo
```

## Why three and not two

The ALB, VPC and Cognito user pool are shared. Giving each app its own ALB
would add $16.43/month per app for no benefit, which is the opposite of what
this architecture is for. So shared infrastructure lives in its own stack and
the apps attach to it.

The two app stacks are the **same Terraform code** with different
`terraform.tfvars`. If you'd rather not maintain two copies, delete one and run
the other with `-var-file=dashboard.tfvars` / `-var-file=model.tfvars` against
two workspaces. Keeping them separate is the better default when different
people own different tools.

## Order

```bash
# 1. Shared. Needs a Route 53 public hosted zone you control.
cd platform
cp terraform.tfvars.example terraform.tfvars   # edit
terraform init && terraform apply

# 2. Either app, in any order.
cd ../dashboard
terraform init && terraform apply
terraform output docker_push_commands           # build, push, redeploy

cd ../model
terraform init && terraform apply
terraform output docker_push_commands

# 3. Invite yourself, then open the URLs.
cd ../platform
aws cognito-idp admin-create-user \
  --user-pool-id $(terraform output -raw cognito_user_pool_id) \
  --username you@example.com \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
```

`project` must be identical in all three tfvars files. That string is how the
app stacks locate the platform's SSM parameters.

## Teardown

Reverse order. Destroy `dashboard` and `model` before `platform`, because their
listener rules attach to the platform's listener.

## Cost summary

| | Monthly |
|---|---|
| Platform (fixed, never sleeps) | ~$20 |
| Dashboard (0.5 vCPU / 2 GB, weekday warm window) | ~$8 |
| Model (4 vCPU / 16 GB, pure scale-to-zero) | ~$10 |
| **Total** | **~$38** |

Same workloads on always-on EC2: roughly $210.

## Before it works: three app code changes

Detailed in each app's README, but all three are load-bearing:

1. **Heartbeat JS in the UI.** Without it the sleeper will scale a task to zero
   underneath an active user, because a Shiny websocket emits no HTTP requests.
2. **Read `SHINY_CPU_WORKERS`** instead of calling `parallel::detectCores()`,
   which reports host cores rather than the Fargate task limit.
3. **Read `DEBUG_RUNMODEL`** instead of hardcoding `debug_runmodel <- TRUE`.
