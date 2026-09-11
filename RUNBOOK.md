# Runbook: Tarpeyo dashboard on AWS

End to end, from "I need a subdomain" to "a client can log in and use it."

Two phases. Phase 1 gets you a delegated subdomain — mostly waiting on other
people, so start it today. Phase 2 is the deployment, roughly a half day of
your own time once the DNS is in place.

**Realistic timeline:** 1–5 business days for Phase 1 (IT response time
dominates), then 3–4 hours for Phase 2.

---

# Phase 1 — Get a delegated subdomain

## The chicken-and-egg problem

You cannot ask IT for delegation before creating the Route 53 hosted zone,
because the thing you're asking them to add is the list of nameservers that
zone generates. So: create the empty zone in AWS first, then hand IT the four
nameservers it produces.

Creating the zone changes nothing about your existing DNS. Until IT adds the
NS records, the zone simply sits there answering nobody. It costs $0.50/month.

## Step 1.1 — Pick the subdomain

Something that reads well to an external client, because they will see it in
the address bar:

- `tools.stratevi.com` — good, general purpose, room to grow
- `apps.stratevi.com` — equally fine
- `heor-tools.stratevi.com` — narrower; you'll regret it when a non-HEOR app
  needs a home

Pick one. Everything below assumes `tools.stratevi.com` — substitute yours.

Your apps will live at `dashboard.tools.stratevi.com` and
`model.tools.stratevi.com`.

## Step 1.2 — Create the hosted zone

```bash
aws route53 create-hosted-zone \
  --name tools.stratevi.com \
  --caller-reference "tools-delegation-$(date +%s)" \
  --hosted-zone-config Comment="Delegated subdomain for Shiny app platform"
```

Save the zone ID from the output — it looks like `/hostedzone/Z0123456789ABC`.
You want just the `Z0123456789ABC` part.

Now get the four nameservers:

```bash
aws route53 get-hosted-zone \
  --id Z0123456789ABC \
  --query 'DelegationSet.NameServers' \
  --output table
```

You'll get something like:

```
ns-1234.awsdns-56.org
ns-789.awsdns-12.net
ns-345.awsdns-67.co.uk
ns-901.awsdns-23.com
```

Yours will differ. The mixed TLDs are normal and intentional on AWS's part.

## Step 1.3 — Send the request to IT

Draft below. Adjust the tone to your org, but keep the four NS records and the
exact record name — those are what actually matters, and getting either wrong
means a second round trip.

> **Subject:** DNS request — delegate `tools.stratevi.com` to AWS Route 53
>
> Hi [team],
>
> I'm standing up a small internal web app platform on our AWS account and need
> a subdomain delegated so the platform can manage its own DNS records and TLS
> certificates automatically.
>
> **What I'm asking for:** four NS records on `stratevi.com` for the subdomain
> `tools`, pointing at AWS Route 53.
>
> | Name | Type | TTL | Value |
> |---|---|---|---|
> | `tools.stratevi.com` | NS | 3600 | `ns-1234.awsdns-56.org.` |
> | `tools.stratevi.com` | NS | 3600 | `ns-789.awsdns-12.net.` |
> | `tools.stratevi.com` | NS | 3600 | `ns-345.awsdns-67.co.uk.` |
> | `tools.stratevi.com` | NS | 3600 | `ns-901.awsdns-23.com.` |
>
> (Some DNS panels want the trailing dot, some add it themselves. Four separate
> NS records on the same name, not one record with four values, unless your
> tool models it that way.)
>
> **Scope:** this delegates only `tools.stratevi.com` and anything beneath it.
> It does not affect `stratevi.com`, `www`, mail, or any existing record. MX,
> SPF, DKIM and DMARC on the apex are untouched.
>
> **Reversible:** deleting these four records revokes the delegation
> immediately. Nothing on the AWS side can create records outside this
> subdomain.
>
> **Why delegation rather than individual records:** the platform issues and
> auto-renews its own TLS certificates, which requires it to write short-lived
> validation records. Delegation means that happens automatically instead of
> raising a ticket with you every 13 months, and every time we add an app.
>
> Happy to walk through it if useful.
>
> Thanks,
> [you]

## Step 1.4 — Questions IT will probably ask

**"Can't we just add a CNAME instead?"** For pointing one hostname at the load
balancer, yes. But ACM certificate validation and renewal need records written
into the zone on AWS's schedule, and each new app needs another record. You'd
be filing tickets indefinitely. Delegation is the one-time version.

**"What's the security exposure?"** Whoever controls the Route 53 zone can
create hostnames under `tools.stratevi.com` and obtain certificates for them.
That's it — no ability to touch the parent domain, mail routing, or anything
outside the subdomain. It's the same trust boundary as giving someone a folder
on a shared drive.

**"Does this affect email?"** No. Mail routing is determined by MX records on
`stratevi.com`, which are unchanged. A subdomain delegation cannot influence
them.

**"What if we want it back?"** Delete the four NS records. Delegation ends
within the TTL, an hour at most.

**"Who's paying for it?"** The hosted zone is $0.50/month on the AWS account
you already have.

## Step 1.5 — Verify delegation actually worked

Don't take IT's word for it — check. From any machine:

```bash
dig NS tools.stratevi.com +short
```

Success looks like the same four `awsdns` nameservers you sent them. Empty
output or a different set means it isn't live yet.

Propagation is usually minutes but can take up to the parent zone's TTL. If
it's been a few hours and you still get nothing, ask IT to confirm the records
saved — the most common failure is a DNS panel that silently appended the
domain, producing `tools.stratevi.com.stratevi.com`.

Also confirm the zone resolves as authoritative:

```bash
dig SOA tools.stratevi.com +short
```

Once both return sensible answers, Phase 1 is done. **Do not start Phase 2
until `dig NS` returns the AWS nameservers** — the certificate step will hang
indefinitely without it.

---

# Phase 2 — Deploy

## Step 2.0 — Prerequisites

### Tooling

```bash
terraform version   # need >= 1.6, and >= 1.10 if you use the S3 backend below
aws --version       # v2
docker --version    # daemon running
dig -v              # or use nslookup
```

Install anything missing before you go further.

### AWS credentials

```bash
aws sts get-caller-identity
```

Confirm the account ID is the one you intend to deploy into. If your org uses
SSO, run `aws sso login` first.

### IAM permissions

This is the most common hard blocker, and worth checking before you start
rather than discovering it halfway through an apply. You need permission to
create resources in:

`ec2` (VPC, subnets, security groups) · `elasticloadbalancing` · `ecs` · `ecr` ·
`lambda` · `logs` · `events` · `cloudwatch` · `cognito-idp` · `ssm` ·
`route53` · `acm` · `budgets` · **`iam`**

`iam:CreateRole` and `iam:AttachRolePolicy` are frequently restricted, and the
platform stack creates three roles. If you're not an account admin, get this
confirmed up front — a mid-apply permission failure leaves you with a partial
stack to clean up.

### Region

Pick one and use it everywhere. `us-east-1` is the default in the tfvars and is
the cheapest. The ACM certificate must be in the **same region as the ALB**
(unlike CloudFront, which requires us-east-1), and the Terraform handles that
automatically as long as you don't mix regions between stacks.

Being in LA is not a reason to use `us-west-2` — the latency difference is
tens of milliseconds on an app people open a few times a day.

### Unpack

```bash
mkdir -p ~/shiny-platform && cd ~/shiny-platform
unzip platform.zip      -d platform
unzip dashboard.zip     -d dashboard
unzip dashboard-app.zip            # extracts a dashboard-app/ folder
ls
# dashboard  dashboard-app  platform
```

## Step 2.1 — Optional: remote state

Skip this if you're the only person who will ever run Terraform. Do it if
anyone else might, or if you care about not losing state with your laptop.

```bash
aws s3api create-bucket \
  --bucket stratevi-tf-state-$(aws sts get-caller-identity --query Account --output text) \
  --region us-east-1

aws s3api put-bucket-versioning \
  --bucket stratevi-tf-state-<account-id> \
  --versioning-configuration Status=Enabled
```

Then uncomment the `backend "s3"` block in `platform/versions.tf` and
`dashboard/versions.tf`, filling in the bucket name. The `use_lockfile = true`
line requires **Terraform 1.10 or later**; on older versions, replace it with
`dynamodb_table = "<a lock table you create>"`.

Do this before the first `apply`. Migrating state afterward works but is an
extra step you don't need.

## Step 2.2 — Configure the platform stack

```bash
cd platform
cp terraform.tfvars.example terraform.tfvars
```

Edit four values:

```hcl
region  = "us-east-1"
project = "shiny"                    # keep this consistent across all stacks

route53_zone_id = "Z0123456789ABC"   # from Step 1.2, no /hostedzone/ prefix
domain_name     = "tools.stratevi.com"

cognito_domain_prefix = "stratevi-shiny-auth"

budget_alert_emails = ["you@stratevi.com"]
```

Two constraints worth knowing:

- `cognito_domain_prefix` must be **globally unique across all of AWS**,
  lowercase alphanumeric and hyphens only, and cannot contain the strings
  `aws`, `amazon`, or `cognito`. If apply fails here, add a suffix.
- `project` is how the dashboard stack finds the platform's SSM parameters. If
  the two don't match, the dashboard apply fails with a parameter-not-found
  error that doesn't obviously point at the cause.

Leave `oidc_provider` commented out. Get Cognito-native login working first;
Entra federation is a separate change once the basics are proven.

## Step 2.3 — Apply the platform

```bash
terraform init
terraform plan     # skim it; ~40 resources, no deletions
terraform apply
```

**Expect this to pause for several minutes** on
`aws_acm_certificate_validation.wildcard`. That's Terraform waiting for AWS to
verify the DNS record it just wrote. Normal duration is 2–5 minutes.

If it's still going after 15, kill it and check the delegation:

```bash
dig NS tools.stratevi.com +short
```

Empty means Phase 1 didn't finish. Fix that, then re-run `apply` — it's safe to
resume.

Save the outputs:

```bash
terraform output
```

From here you are paying roughly **$20/month** whether or not anything is
deployed on top. That's the ALB.

## Step 2.4 — Build and test the image locally

Do this before pushing anything. A broken image caught locally costs five
minutes; caught in ECS it costs an hour of log spelunking.

```bash
cd ../dashboard-app
docker build --platform linux/amd64 -t tarpeyo-dashboard .
```

First build takes 5–10 minutes, mostly installing plotly's dependency tree.
Subsequent builds hit Docker's layer cache and take seconds unless you change
the Dockerfile.

`--platform linux/amd64` is **mandatory on an Apple Silicon Mac**. The task
definition specifies `X86_64`; an arm64 image pushes fine, then fails to start
with `exec format error`, which is an unhelpful message to debug.

```bash
docker run --rm -p 3838:3838 tarpeyo-dashboard
```

Open `http://localhost:3838` and check three things:

1. The Sankey renders and the sliders/filters respond.
2. Open browser devtools → Network. Within a minute you should see a `HEAD`
   request to `/?heartbeat=...`. **This is the check that matters most.**
   Without it, the deployed app will work perfectly and then drop users after
   20 minutes of a "quiet" session, which is the single most annoying failure
   mode in this architecture.
3. No errors in the container's console output.

Stop the container when satisfied.

## Step 2.5 — Apply the dashboard stack

```bash
cd ../dashboard
```

Open `terraform.tfvars` and confirm `project = "shiny"` matches the platform.
Everything else is already set sensibly. Then:

```bash
terraform init
terraform apply
```

This is quick — about 2 minutes. It creates the ECR repository, the Cognito
client, both target groups, the listener rule, the ECS service at zero tasks,
and the two Lambdas.

The service starting with an empty ECR repository is expected and fine. It has
zero desired tasks, so there's nothing to pull yet.

## Step 2.6 — Push the image

```bash
terraform output docker_push_commands
```

That prints four commands with your account ID and repository URL filled in:
ECR login, build, push, and a `force-new-deployment`. Run them from the
`dashboard-app/` directory:

```bash
cd ../dashboard-app
# paste the four commands
```

The push moves roughly 1.2–1.5 GB, so give it a few minutes on a normal
connection.

The `force-new-deployment` at the end is a no-op right now (desired count is
zero), but running it keeps the habit for future updates, where it's the step
that actually ships your change.

## Step 2.7 — Create your user

```bash
cd ../platform

aws cognito-idp admin-create-user \
  --user-pool-id $(terraform output -raw cognito_user_pool_id) \
  --username you@stratevi.com \
  --user-attributes Name=email,Value=you@stratevi.com Name=email_verified,Value=true
```

You'll get a temporary password by email. The pool is invite-only by default,
which is what you want for anything client-facing.

## Step 2.8 — First login

```bash
cd ../dashboard && terraform output -raw url
```

Visit it. The sequence you should see:

1. Redirect to the Cognito hosted login page.
2. Sign in with the temporary password; you'll be forced to set a new one.
3. Redirect back, then a **"Starting Treatment Pathway Dashboard"** holding
   page with a progress bar.
4. After 30–45 seconds, the page refreshes into your app.

That holding page is the **proxy** waking the task. Seeing it once is
confirmation the whole mechanism works. (It used to be a waker Lambda; those
were retired 2026-09-11 with the last legacy stack.)

## Step 2.9 — Verify the sleep cycle

This is the step people skip, and it's the one that determines whether you
actually get the cost savings. Set aside 45 minutes and don't touch the app.

The proxy decides this now, server-side — there is no heartbeat, no warm
window and no CloudWatch alarm involved. What you want to see:

- The app's row in `shiny-proxy-apps` shows `state = running` while you use it
- Roughly `idle_minutes` after you close the browser (20 for dashboards, 10
  for models), the proxy sleeps it and the row goes to `stopped`
- Revisiting the URL shows the starting page again, then the app

Check the proxy's reasoning:

```bash
aws logs tail /ecs/shiny/proxy --since 1h --filter-pattern sleep
```

Every wake, allow, deny and sleep is also a row in `shiny-proxy-audit`, and
the portal's Costs page turns the awake intervals into money. If the app never
sleeps, something is holding a websocket or polling the URL — an uptime
monitor, a browser tab left open, a bookmark preview.

## Step 2.10 — Invite real users

```bash
cd ../platform

for EMAIL in colleague@stratevi.com client@example.com; do
  aws cognito-idp admin-create-user \
    --user-pool-id $(terraform output -raw cognito_user_pool_id) \
    --username "$EMAIL" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true
done
```

Tell them about the cold start before they hit it. "It sleeps when idle, so the
first load in the morning takes about a minute" prevents the support message.

---

# Operations

## Shipping an app change

```bash
cd dashboard-app
# edit app.R
docker build --platform linux/amd64 -t <ecr-url>:latest .
docker push <ecr-url>:latest
aws ecs update-service --cluster shiny-cluster \
  --service shiny-dashboard --force-new-deployment
```

No Terraform needed for content changes. Terraform only comes back in when you
change infrastructure — sizing, schedule, domains.

## Adjusting the sleep schedule

Not Terraform any more. Edit the app's settings in the portal's admin area
(`idle_minutes`, `max_session_hours`) — the change is audited and takes effect
on the next request. Warm windows no longer exist: the first visitor of the
morning sees the ~30–60s starting page, which is the trade that removed
~$7.60/month of pre-warming.

## Checking what it actually cost

After the first full month:

Use the portal's **Costs** page first — it meters awake seconds per app
against that app's task size, so it attributes cost the way you'd actually
ask the question. Cost Explorer (group by Tag: Project = shiny) is the
cross-check on the total, not the per-app source: its tags aren't
retroactive, lag a day, and can't split shared cost.

Sanity check: ALB around $16–20, the proxy task ~$9, per-app Fargate a few
dollars, everything else near zero. If an app's awake hours are much higher
than expected, it isn't sleeping — go back to Step 2.9.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `apply` hangs on certificate validation | Delegation not live | `dig NS tools.stratevi.com` — if empty, Phase 1 is incomplete |
| ALB returns 503 | Proxy task unhealthy — it fronts every app | `aws ecs describe-services --cluster shiny-cluster --services shiny-proxy`; check `/ecs/shiny/proxy` logs |
| Task starts then dies repeatedly | Health check failing | `aws logs tail /ecs/shiny/dashboard --since 15m` |
| `exec format error` in task logs | arm64 image on an x86 task | Rebuild with `--platform linux/amd64` |
| Cognito redirect loop | Callback URL mismatch | Confirm the app client's callback is `https://<fqdn>/oauth2/idpresponse` |
| App never sleeps | Something is holding a websocket or polling the URL | Check `/ecs/shiny/proxy` logs and `shiny-proxy-audit` for who is requesting it |
| Session dies after ~20 min of use | Was a missing heartbeat on the legacy path; can't happen now — the proxy counts open websockets | If you see it, the proxy's websocket accounting is wrong: capture the audit rows |
| `parameter not found` on dashboard apply | `project` differs between stacks | Make them match, re-apply |
| Cognito domain apply fails | Prefix already taken globally | Add a suffix and re-apply |

## Migrating existing stacks to S3 state (ADR-0009)

Skip this once it's done — it's a one-time move per stack, not something you
repeat on every deploy. Read [ADR-0009](docs/adr/0009-remote-state.md) first
if the account facts below have changed.

At the time this was written, `platform`, `dashboard`, `portal` and `model`
are all already applied with local `.tfstate` files on one machine (see
`docs/STATUS.md`). Migrating state does **not** touch the actual AWS
resources — it moves where Terraform's record of them lives. It's safe to do
while `dashboard` is serving real traffic.

Order matters less here than in a fresh deploy (nothing depends on state
location), but do it in this order anyway, so a mistake surfaces on the stack
you understand best first: **platform → dashboard → model → portal**.

### One-time: create the bucket

```powershell
cd state-backend
terraform init
terraform plan -no-color -out state-backend.tfplan | Tee-Object -FilePath plan.txt
Select-String -Path plan.txt -Pattern "^Plan:"
```

Skim the plan — one bucket plus four sub-resources (versioning, encryption,
public-access block, lifecycle rule), no deletions. Then:

```powershell
terraform apply state-backend.tfplan
terraform output bucket_name
```

Confirm the output is exactly `stratevi-tf-state-652063276768`. The IAM
policy's `ProjectBuckets` statement names this bucket by its literal ARN
(its other pattern, `shiny-*`, doesn't match this name), so a different
suffix here breaks every later `terraform init` with an access-denied error
that looks like a permissions problem rather than a naming one. Note the
updated policy JSON must be pushed to AWS as a new policy version (an
admin action -- `Stratevi_Testing` cannot update its own policy) before any
of this runs.

### Per stack: platform, then dashboard, then model, then portal

Each stack's `versions.tf` already has its `backend "s3"` block uncommented
and filled in (bucket above, key `shiny/<stack>.tfstate`, `us-east-1`,
`encrypt = true`, `use_lockfile = true`). Repeat the following for each stack
directory, in order:

```powershell
cd ..\platform      # then ..\dashboard, ..\model, ..\portal in turn

# Snapshot what Terraform currently thinks exists, to diff against after the
# migration. Count, don't eyeball it -- a silently dropped resource looks
# identical to a correct migration until the next apply.
terraform state list | Tee-Object -FilePath state-before.txt
(Get-Content state-before.txt | Measure-Object -Line).Lines

# Back the local state up before touching anything. The .bak suffix still
# matches .gitignore's `*.tfstate.*` rule, so this can't get committed either.
Copy-Item terraform.tfstate terraform.tfstate.bak
if (Test-Path terraform.tfstate.backup) {
  Copy-Item terraform.tfstate.backup terraform.tfstate.backup.bak
}

terraform init -migrate-state
```

This prompts:

```
Do you want to copy existing state to the new backend?
  ...
  Enter a value:
```

Type `yes` and press Enter. For a non-interactive run (CI, or a script),
skip the prompt entirely with `-force-copy` instead of `-migrate-state`'s
default confirmation:

```powershell
terraform init -migrate-state -force-copy
```

Then verify nothing was lost or duplicated:

```powershell
terraform state list | Tee-Object -FilePath state-after.txt
(Get-Content state-after.txt | Measure-Object -Line).Lines
Compare-Object (Get-Content state-before.txt) (Get-Content state-after.txt)
```

The line count must match exactly, and `Compare-Object` should print
nothing — same resource addresses, not just the same count. Then confirm the
plan is still clean:

```powershell
terraform plan -no-color -out post-migrate.tfplan | Tee-Object -FilePath plan.txt
Select-String -Path plan.txt -Pattern "^Plan:"
```

`Plan: 0 to add, 0 to change, 0 to destroy` is what you want — including on
`platform`, where the `ignore_changes` lifecycle blocks on the listener rule
and the ECS service should mean the plan stays clean even though something
mutates both of those at runtime. Anything else: stop and investigate before
moving to the next stack. Do not apply it.

### If the migration is interrupted

`use_lockfile = true` puts the lock in a companion object next to the state
object in S3, not in a separate DynamoDB table. If `init -migrate-state` dies
mid-way (closed laptop lid, killed terminal), the next `terraform init` or
`plan` on that stack reports the state is locked. Force-unlock with the lock
ID it prints, after confirming nobody else is actually running Terraform
against that stack:

```powershell
terraform force-unlock <LOCK_ID>
```

### Cleaning up the local state files afterward

Once a stack's migration is verified (state list count matches,
`Compare-Object` is silent, plan is clean), the local files are redundant —
but don't delete them immediately. Keep the `.bak` copies until you've done
at least one more `terraform plan` against the S3 backend on a different day,
so you know the migration held rather than masking a problem that only shows
up on the next real apply.

After that, delete them. They're not just clutter: a Terraform state file
holds resource IDs, ARNs, and in places values the AWS provider marks
sensitive (Cognito app client secrets, for one) in plain text. A stale copy
on a laptop is exactly the kind of single point of failure ADR-0009 exists to
retire.

```powershell
Remove-Item terraform.tfstate.bak
if (Test-Path terraform.tfstate.backup.bak) { Remove-Item terraform.tfstate.backup.bak }
```

Leave the now-empty `terraform.tfstate` / `terraform.tfstate.backup` that
Terraform itself leaves behind after moving to a remote backend — harmless,
and already excluded from commits by `.gitignore`.

## Proxy operations

The proxy is the ADR-0014 control plane piece: one always-on service that
terminates every app hostname, checks entitlement, and wakes the right ECS
service. See [docs/design/proxy.md](docs/design/proxy.md) for how it decides
what to do with a request — this section is just the day-to-day commands.

### Build and push the proxy image

**Build the UI first.** The React bundle is baked into this image at build
time (`proxy-app/Dockerfile` COPYs `./portal-dist/`), and Node never enters
the runtime image. Skipping these two lines is NOT an error -- the directory
is committed with a `.gitkeep`, so the COPY succeeds and you get an image
carrying whatever was in `portal-dist/` last time, or a "not built yet" page
if it was never populated. Either way the API works and the UI is a version
behind, with nothing in any log to say so.

```powershell
cd portal-ui
npm ci; npm run build
Remove-Item -Recurse -Force ..\proxy-app\portal-dist\* -ErrorAction SilentlyContinue
Copy-Item -Recurse dist\* ..\proxy-app\portal-dist\
```

Then the image:

```powershell
cd proxy-app
docker build --platform linux/amd64 -t shiny-proxy .

cmd /c "aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 652063276768.dkr.ecr.us-east-1.amazonaws.com"

docker tag shiny-proxy:latest 652063276768.dkr.ecr.us-east-1.amazonaws.com/shiny-proxy:latest
docker push 652063276768.dkr.ecr.us-east-1.amazonaws.com/shiny-proxy:latest

aws ecs update-service --cluster shiny-cluster --service shiny-proxy --force-new-deployment
```

The ECR login has to run exactly as written — see `docs/GOTCHAS.md` on why
`docker login --password-stdin` still 400s if PowerShell touches the piped
password at any point. The whole pipe has to stay inside `cmd`.

### Seed an app into the proxy's table

`proxy-app/seed.py` writes the routing row the proxy reads to decide which ECS
service and target group a hostname belongs to. Always dry-run first — a bad
row means the proxy silently 404s or wakes the wrong service, and the audit
table is a much worse place to discover that than a diff:

```powershell
python proxy-app/seed.py --dry-run --table shiny-proxy-apps
# review the printed row, then actually write it:
python proxy-app/seed.py --table shiny-proxy-apps
```

### Migrating an app to the proxy

Checklist, in order — full rationale for each step is in
[docs/design/proxy.md](docs/design/proxy.md):

1. Register the app's callback URL on the shared Cognito client. **From P2a
   this is a CLI step, not a Terraform one** — `proxy/cognito.tf` now carries
   `ignore_changes = [callback_urls, logout_urls]` because the portal adds a
   URL per app it creates, so editing `app_hosts` in `proxy/terraform.tfvars`
   no longer has any effect on its own. (Still add the hostname there: it
   keeps the file an honest record of what should be registered, and it is
   what a rebuild from scratch would use.)

   Read the current lists, append the two URLs, and write the whole client
   back — `update-user-pool-client` REPLACES the entire configuration, so
   every other field must be resent or it is silently wiped:

   ```powershell
   $pool = "us-east-1_LI3CZpwAF"
   $client = "1senki56hh2ngv7neuqhot6gv8"
   aws cognito-idp describe-user-pool-client --user-pool-id $pool --client-id $client `
     --query "UserPoolClient.{cb:CallbackURLs,lo:LogoutURLs}" --output json
   ```

   Then either add them through the Cognito console (far safer by hand — it
   preserves the other fields for you), or let the portal do it, which is
   the whole point of the wizard. Verify afterwards that the pre-existing
   URLs are all still present.
2. Seed the app's row (above) — `--dry-run` first, then for real.
3. Set `proxied = true` in the app's own `terraform.tfvars`, then plan and
   apply that stack. Do this while the app is asleep: the ECS service gets
   recreated as part of the cutover, at desired count 0, so there's no
   traffic to drop.
4. Verify the full cycle through the proxy — hit the hostname cold (wake),
   use the app (stays up), then leave it and confirm it sleeps again — and
   check `shiny-proxy-audit` shows the requests you'd expect for each phase.

Rollback is the same lever in reverse: set `proxied = false` in the app's
`terraform.tfvars` and apply. That's a single flag, not a re-migration.

## User administration

Pool `us-east-1_LI3CZpwAF`, invite-only. Two populations (ADR-0015):

**Staff** (`@stratevi.com` only since 2026-09-11 -- the
`@assembledintelligence.co.uk` aliases are gone from the pool, and
`staff_domains` in `__config__` names one domain) need no account
created — they sign in with the **Microsoft365** button and Cognito
provisions them on first sign-in. **Never create a native Cognito user for
an address Entra emits:** email is the pool's username, so it collides with
their federated identity and Cognito refuses it (`AliasExistsException`).
The same rule is why `platform/cognito.tf` declares no users; don't add any.

Granting someone access is a separate step from them having an account —
edit the app's allowlist in the portal admin UI (or the `allowed_emails` set
on its `shiny-proxy-apps` row). The address must match what the IdP emits
exactly; Entra returns `JAKE@STRATEVI.COM`-style capitals, which the proxy
lowercases before matching, so store lowercase.

**External clients** get a native account:

```powershell
aws cognito-idp admin-create-user `
  --user-pool-id us-east-1_LI3CZpwAF `
  --username client@example.com `
  --user-attributes Name=email,Value=client@example.com Name=email_verified,Value=true `
  --desired-delivery-mediums EMAIL
```

They receive an invite with a temporary password and sign in with the
email/password form below the Microsoft button. Then add their address to
the app's allowlist. To revoke: remove them from the allowlist (immediate,
and the audit trail records it), and `admin-disable-user` if they should
lose the account entirely.

### Break-glass: federation is down and no staff can sign in

There is deliberately no standing native admin account (ADR-0015). Recovery,
from the AWS console or CLI with admin credentials:

1. Create a native user at an address **Entra will never emit** — not a
   mailbox in the tenant, or you recreate the collision above.
2. Add that address to `admin_emails` on the `__config__` row of
   `shiny-proxy-apps`. Fastest is editing the item in the DynamoDB console;
   note that `proxy/portal.tf` owns that row, so a later `proxy/` apply will
   revert it — which is the desired cleanup, not a problem.
3. Sign in, do what's needed.
4. Afterwards: `admin-delete-user`, and re-apply `proxy/` to restore the
   admin list.

## Teardown

Reverse order — the dashboard's listener rule attaches to the platform's
listener:

```bash
cd dashboard && terraform destroy
cd ../platform && terraform destroy
```

The ECR repository has `force_delete = false`, so delete images first if you
want it to go cleanly. The Route 53 hosted zone is a separate resource you
created by hand; delete it manually, and ask IT to remove the NS records if
you're done for good.

---

# What comes after

This all happened. The ALB, Cognito, the wake/sleep cycle and the deployment
loop are proven, and the microsimulation model went through the **portal**
rather than a second Terraform stack — uploaded as a zip, built by CodeBuild,
provisioned by the SDK. That is the motion for every app from here: nobody
should be writing a Terraform stack per app again. See
docs/design/portal-p2a.md.

What an R app still owes the pipeline: read `SHINY_CPU_WORKERS` rather than
calling `detectCores()`, ship a `renv.lock` that actually covers every
`library()` call, and cap anything user-supplied that drives run time.
