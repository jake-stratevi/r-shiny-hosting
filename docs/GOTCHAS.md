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

**A Cognito app client's login-page configuration is runtime state, not
config -- Terraform will clobber it.** Three fields on
`aws_cognito_user_pool_client` get edited outside Terraform in normal use:
`callback_urls` and `logout_urls` (the portal appends one per app it
creates) and `supported_identity_providers` (an IdP attached in the
console). All three are now in that resource's `ignore_changes`.

This is not hypothetical. On 2026-09-10 the live client carried
`["COGNITO", "Microsoft365"]` while the SSM value Terraform builds the list
from still said `"COGNITO"` -- the next apply of the proxy stack would have
removed the Microsoft button and locked out every federated user. The plan
line would have looked like a harmless one-word change.

The lesson generalises: if something other than Terraform legitimately
writes a field at runtime, `ignore_changes` it and say why, or an apply
made for an unrelated reason will silently undo it. Terraform owns the
resource's existence; the runtime owns that field.

**PowerShell 5.1's `Compress-Archive` writes BACKSLASH path separators**,
which the ZIP spec forbids (APPNOTE 4.4.17.1 requires forward slashes).
Read on Linux, `Inputs\cpi.csv` is not a file inside a directory -- it is
one file whose *name* contains a backslash, so `Inputs/` never exists and
an R app dies at RUNTIME on `read.csv("Inputs/cpi.csv")`, long after any
validation passed. It is inconsistent about it, too: the same command on
the same folder produced backslashes once and forward slashes the next
time, so "I tested it" proves nothing.

The platform's `buildspec/validate.py` normalises these on extraction and
is covered by `tests/test_buildspec_validate.py`, so an uploaded bundle is
safe either way. Anything else that builds a zip on Windows is not: use
Explorer's "Send to > Compressed (zipped) folder" (which is correct), or
Python's `zipfile` with `Path.as_posix()` names. Note Python's `ZipInfo`
silently rewrites `os.sep` to `/` when WRITING, so you cannot reproduce the
bad archive with `writestr` -- flip the separators on the ZipInfo objects
after opening if you need to test this path.

**CodeBuild runs buildspec commands with `/bin/sh`, not bash.** On the
Ubuntu standard images `/bin/sh` is dash, which has no `pipefail`, so a
block opening `set -euo pipefail` dies immediately with

    script.sh: 4: set: Illegal option -o pipefail

and the phase fails in seconds having done nothing -- which reads like a
broken bundle, not a broken shell. Put `shell: bash` under `env:` in the
buildspec (buildspec 0.2 supports it) if you use any bashism at all.

Note `bash -n` on the script locally does NOT catch this: it checks bash
syntax using bash. The only shell that matters is the one CodeBuild picks,
and it is not the one you tested with.

**A presigned S3 PUT that works with curl and 403s in a browser is a
signature-version problem.** boto3's default for `generate_presigned_url`
against the global S3 endpoint is legacy SigV2, which folds `Content-Type`
into the string-to-sign. Browsers always send a Content-Type for a File
(Chrome picks `application/x-zip-compressed` for a .zip); the presigner
signed an empty one; S3 answers 403 `SignatureDoesNotMatch`. curl sends no
Content-Type, so the identical URL returns 200 from a terminal and the bug
looks like it is in the front end.

Build the client with `Config(signature_version="s3v4")`: SigV4 signs only
`host`, so the browser's Content-Type is irrelevant. `Boto3Uploads.presign`
now refuses to return a non-SigV4 URL rather than hand out one that will
fail. Both facts are covered by tests in `tests/test_provision.py`.

**The conditional put on `host` is no longer the same-key mutex.** It was,
while a host was exactly `<key>.tools.stratevi.com`: two people creating
`model` at the same moment raced for one partition key and one lost. Since
hostnames gained a random suffix (2026-09-11) two racers no longer collide —
both rows get written, both name their resources `shiny-model`, and they end
up sharing one ECR repository and one ECS service while the second build
overwrites the first app's image at the same tag, with nothing logged.

`Provisioner._claim_key` now carries that guarantee, right after the reserve
and before any AWS call. If you change how hostnames or keys are allocated,
that function is what you have to keep honest — the put will not catch it
for you. It is also deliberately weaker than the put was (it reads through
an eventually-consistent Scan); the full fix is a `__key__<key>` guard row
written in the same `TransactWriteItems` as the app row, deferred to P2b.
