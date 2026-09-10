# Gotchas

Every one of these cost real time during the initial build. They are recorded
so the next person doesn't rediscover them.

## Windows and PowerShell

**`-out=file` splits on the equals sign.** PowerShell parses `-out=plan.tfplan`
as two arguments and Terraform reports "Too many command line arguments". Use
`-out plan.tfplan` or quote the whole thing. Same trap applies to `-var=` and
`-target=`.

**Single quotes are not quotes in `cmd.exe`.** `--query 'Foo.Bar'` reaches the
AWS CLI with the quotes attached, and JMESPath reads a single-quoted token as a
string literal — so it returns the constant text instead of looking up the
field. You get an empty table and no error. Use double quotes, or use
PowerShell.

**`--query length()` lies on paginated commands.** AWS CLI v2 auto-paginates at
10 items and applies `--query` per page, so
`get-parameters-by-path --query "length(Parameters)"` printed `10` twice for 20
parameters. Add `--max-items 100` or `--no-paginate`.

**Native stderr in PowerShell.** `& aws @args 2>&1` wraps stderr into
ErrorRecord objects, not strings, so text matching against error output silently
never matches. Shell the redirect out to `cmd /c "… 2>&1"` instead. This is what
made the first preflight script report 17 passes on 17 failures.

**`docker login --password-stdin` still 400s, even with the password assigned
to a variable first.** The usual workaround for PowerShell mangling piped text —
`$pw = aws ecr get-login-password; $pw | docker login --password-stdin ...` —
doesn't hold up here; PowerShell's pipeline still re-encodes the password on
its way to `docker login`, and ECR rejects it with a 400. The only form that
survives is keeping the *entire* pipe inside `cmd`, so PowerShell's pipeline
never touches the bytes:

```powershell
cmd /c "aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 652063276768.dkr.ecr.us-east-1.amazonaws.com"
```

## Terraform

**`$${` is the escape for a literal `${`, not for a literal `$`.** Two separate
bugs came from this: `"user:Project$${var.project}"` in the budget cost filter
produced a filter that matched no resources and silently never alerted, and
`"$${format(...)}"` in a CloudWatch dashboard title failed to parse at all. Use
`format()` and let it handle the dollar sign as plain text.

**SSM rejects empty parameter values.** A conditional export that resolves to
`""` fails with a length validation error. Use a sentinel like `"none"` and have
the consumer check for it.

**`aws_ssm_parameter.value` is marked sensitive by the provider.** Anything
derived from it inherits that, so root outputs referencing it need explicit
`sensitive = true`. Read them back with `terraform output -raw <name>`.

**ECS won't create a service against an unassociated target group.** Our real
listener rule points at the waker target group, so the ECS target group has no
load balancer attached and `CreateService` fails with "does not have an
associated load balancer". Fixed with `aws_lb_listener_rule.ecs_association` — a
rule on a host condition that can never match (`*.invalid`), existing purely to
satisfy the association requirement.

**A failed resource strands its dependents.** When `CreateService` failed, both
Lambdas, both permissions, the waker attachment and the event target were never
created either — because they all reference the service name. The retry plan was
8 resources, not 1.

**The ECS service takes 3–4 minutes to create even at zero desired count.**
That's the ELB attachment handshake, not something hanging.

**Once a stack's `versions.tf` has an S3 backend block, every terraform
command demands init first — including `terraform state list`.** There's no
"just read the local state" mode once the block is uncommented; the CLI
insists on `terraform init -migrate-state` before it'll answer even a
read-only query. That means a "before" state inventory has to be captured
*before* uncommenting the backend block, not after. If you forget, the only
way to see what the old local state held is to open `terraform.tfstate.bak`
directly — it's plain JSON, and `resources[].instances[].attributes` has what
you need — rather than asking Terraform for it.

## Docker and R

**`--platform linux/amd64` is mandatory on Apple Silicon.** The task definition
specifies X86_64. An arm64 image pushes fine and then fails at runtime with
`exec format error`.

**`0.0.0.0` is a bind address, not a destination.** Shiny prints
`Listening on http://0.0.0.0:3838`; browsing there gives
`ERR_ADDRESS_INVALID`. Use `localhost`.

**Browser extensions inject 404 storms into devtools.** A tight loop of failing
fetches for a hashed bundle name like `index-ozS_Ts9Q.js`, all exactly the same
byte size, from `VM…` initiators, is an extension content script — not your app.
Check in an incognito window.

**Debian-based images already have a system user named `proxy`.**
`python:3.12-slim` and most other Debian-based base images ship a built-in
`proxy` account at uid 13. `useradd ... proxy` in a Dockerfile fails with exit
code 9 — a name collision — and `useradd: user 'proxy' already exists` doesn't
say that the name is baked into the base image rather than something earlier
in the Dockerfile. Pick a different name for the app user (`appuser`,
`shinyproxy`, anything not already spoken for) and move on.

## AWS

**App Runner does not support WebSockets**, which rules it out for Shiny
entirely. This is why the architecture uses ECS behind an ALB.

**`parallel::detectCores()` reads the host CPU count inside Fargate**, not the
task limit. On a 4 vCPU task it can report 16 or more, and
`makeCluster(detectCores() - 2)` will spawn that many R processes in a container
that cannot feed them. Read `SHINY_CPU_WORKERS` from the environment instead.

**A Shiny websocket generates zero HTTP requests.** Any idle detection based on
request-count metrics will see an actively-used app as idle. See
[ADR-0006](adr/0006-heartbeat-idle-detection.md).

**A new Cognito app client in a Managed Login pool shows "Login pages
unavailable. Please contact an administrator."** Not a permissions problem and
not a callback-URL problem -- the Hub pool uses Managed Login (the branded
hosted UI), and every client needs a branding-style association before its
/login will render at all. Terraform (provider 5.x) cannot create one; after
creating or recreating a client, run
`aws cognito-idp create-managed-login-branding --user-pool-id <pool>
--client-id <client> --use-cognito-provided-values`. See proxy/cognito.tf.

**Entra returns AADSTS9002325 ("Proof Key for Code Exchange is required for
cross-origin authorization code redemption").** The redirect URI is
registered under Entra's **Single-page application** platform instead of
**Web**. SPA-registered URIs force PKCE and browser-origin redemption;
Cognito is a confidential client that redeems server-side with a secret and
no `code_verifier`, so Entra refuses. Move the URI to the Web platform (the
same URI cannot exist under both -- delete it from SPA first). The URI that
matters is the POOL's hosted-UI callback,
`https://<domain-prefix>.auth.<region>.amazoncognito.com/oauth2/idpresponse`,
not the application's.

**ALB returns 401 with `AuthMissingStateParam` in the access logs.** Nothing
is misconfigured -- the sign-in was started from a Cognito hosted-UI URL (or
a stale tab sitting on `/oauth2/idpresponse`) instead of from the
application. The ALB plants a `state` value only when IT begins the flow and
rejects any callback lacking it. Always enter at the app hostname. The
`error_reason` field in the ALB access logs names this directly -- and
distinguishes it from `ELBAuthUserClaimsSizeExceeded` (claims over 11 KB,
which also 401s) and `ELBAuthError` (genuine misconfiguration or IdP
unreachable).

**A Cognito pool whose username attribute is `email` allows one account per
address, native and federated combined.** Creating a native user for an
address the IdP also emits fails with `AliasExistsException` -- and if that
user is declared in Terraform, the failure lands halfway through an apply.
Either federate a domain or hand-create accounts in it, never both. See
ADR-0015.
