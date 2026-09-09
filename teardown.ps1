# ---------------------------------------------------------------------------
# teardown.ps1 — tear down the Shiny platform
#
#   .\teardown.ps1 -Check            # what exists right now, destroys nothing
#   .\teardown.ps1 -StopCompute      # emergency brake: kill Fargate spend, keep infra
#   .\teardown.ps1 -Destroy          # full teardown, prompts for confirmation
#   .\teardown.ps1 -Destroy -Force   # no prompt (CI / you are very sure)
#
# WHY THIS EXISTS RATHER THAN JUST `terraform destroy`
#
# Three things in this architecture break a naive destroy:
#
#   1. The sleeper Lambda runs every 5 minutes and, inside a warm window, will
#      scale an ECS service back to 1 — potentially while Terraform is trying
#      to tear it down. The EventBridge rules must be disabled FIRST.
#
#   2. ECR repositories are created with force_delete = false, so a repo with
#      images in it refuses to delete. Images must be purged first.
#
#   3. The app stacks' listener rules attach to the platform's listener, so
#      the platform must be destroyed LAST.
#
# WHAT THIS DOES NOT DELETE (deliberately):
#   - The Route 53 hosted zone (you created it by hand; -DeleteHostedZone opts in)
#   - The ShinyPlatformDeploy IAM policy (you need it to run this script)
#   - Any Terraform state bucket
#   - Local .tfstate files
# ---------------------------------------------------------------------------

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$StopCompute,
    [switch]$Destroy,
    [switch]$Force,
    [switch]$DeleteHostedZone,

    [string]$Project      = "shiny",
    [string]$Region       = "us-east-1",
    [string]$StacksRoot   = ".",
    [string]$HostedZoneId = ""
)

$ErrorActionPreference = "Continue"

# App stacks are destroyed in this order, platform always last.
$AppStacks = @("dashboard", "model")
$Cluster   = "$Project-cluster"

function Say  { param($m) Write-Host $m }
function Ok   { param($m) Write-Host "  OK    $m" -ForegroundColor Green }
function Warn { param($m) Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Bad  { param($m) Write-Host "  FAIL  $m" -ForegroundColor Red }
function Head { param($m) Write-Host ""; Write-Host $m -ForegroundColor Cyan }

function Invoke-Aws {
    # cmd.exe merges stderr as plain text; PowerShell's 2>&1 gives ErrorRecords.
    param([string]$Command)
    $text = (cmd /c "$Command 2>&1" | Out-String)
    return [pscustomobject]@{ Ok = ($LASTEXITCODE -eq 0); Text = $text.Trim() }
}

function Test-StackHasState {
    param([string]$Dir)
    if (-not (Test-Path $Dir)) { return $false }
    Push-Location $Dir
    $out = (cmd /c "terraform state list 2>&1" | Out-String)
    $hasState = ($LASTEXITCODE -eq 0) -and ($out.Trim().Length -gt 0)
    Pop-Location
    return $hasState
}

# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

function Show-Inventory {
    Head "What currently exists"

    $alb = Invoke-Aws "aws elbv2 describe-load-balancers --names $Project-alb --region $Region --query ""LoadBalancers[0].LoadBalancerName"" --output text"
    if ($alb.Ok -and $alb.Text -ne "None") { Warn "ALB $($alb.Text) - billing ~`$16.43/mo" }
    else { Ok "No ALB" }

    foreach ($app in $AppStacks) {
        $svc = Invoke-Aws "aws ecs describe-services --cluster $Cluster --services $Project-$app --region $Region --query ""services[0].[status,desiredCount,runningCount]"" --output text"
        if ($svc.Ok -and $svc.Text -notmatch "None" -and $svc.Text.Trim()) {
            $parts = $svc.Text -split "\s+"
            if ($parts[0] -eq "ACTIVE") {
                if ([int]$parts[1] -gt 0) { Warn "ECS $Project-$app - desired $($parts[1]), running $($parts[2]) - BILLING" }
                else { Ok "ECS $Project-$app - scaled to zero" }
            }
        } else { Ok "No ECS service $Project-$app" }
    }

    foreach ($app in $AppStacks) {
        $repo = Invoke-Aws "aws ecr describe-repositories --repository-names $Project-$app --region $Region --query ""repositories[0].repositoryName"" --output text"
        if ($repo.Ok -and $repo.Text -ne "None") {
            $imgs = Invoke-Aws "aws ecr list-images --repository-name $Project-$app --region $Region --query ""length(imageIds)"" --output text"
            Warn "ECR $Project-$app - $($imgs.Text) image(s)"
        }
    }

    $pools = Invoke-Aws "aws cognito-idp list-user-pools --max-results 60 --region $Region --query ""UserPools[?Name=='$Project-users'].Id"" --output text"
    if ($pools.Ok -and $pools.Text.Trim()) { Warn "Cognito user pool $($pools.Text)" } else { Ok "No Cognito user pool" }

    $vpc = Invoke-Aws "aws ec2 describe-vpcs --region $Region --filters ""Name=tag:Name,Values=$Project-vpc"" --query ""Vpcs[0].VpcId"" --output text"
    if ($vpc.Ok -and $vpc.Text -ne "None") { Warn "VPC $($vpc.Text)" } else { Ok "No VPC" }

    Head "Terraform state"
    foreach ($s in ($AppStacks + @("platform"))) {
        $dir = Join-Path $StacksRoot $s
        if (Test-StackHasState $dir) {
            Push-Location $dir
            $n = ((cmd /c "terraform state list 2>&1") | Measure-Object -Line).Lines
            Pop-Location
            Warn "$s - $n resources in state"
        } else { Ok "$s - no state" }
    }
}

# ---------------------------------------------------------------------------
# Step 1: stop the scaler from fighting us
# ---------------------------------------------------------------------------

function Disable-Scalers {
    Head "Disabling sleeper schedules"
    foreach ($app in $AppStacks) {
        $r = Invoke-Aws "aws events disable-rule --name $Project-$app-sleeper --region $Region"
        if ($r.Ok) { Ok "$Project-$app-sleeper disabled" }
        elseif ($r.Text -match "ResourceNotFound") { Ok "$Project-$app-sleeper does not exist" }
        else { Warn "$Project-$app-sleeper - $($r.Text)" }
    }
}

# ---------------------------------------------------------------------------
# Step 2: stop compute (this is what actually stops the money burning)
# ---------------------------------------------------------------------------

function Stop-Compute {
    Head "Scaling services to zero"
    foreach ($app in $AppStacks) {
        $r = Invoke-Aws "aws ecs update-service --cluster $Cluster --service $Project-$app --desired-count 0 --region $Region"
        if ($r.Ok) { Ok "$Project-$app scaled to 0" }
        elseif ($r.Text -match "ServiceNotFound|ClusterNotFound") { Ok "$Project-$app does not exist" }
        else { Warn "$Project-$app - $($r.Text)" }
    }

    Say ""
    Say "  Waiting up to 3 minutes for tasks to drain..."
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        $still = 0
        foreach ($app in $AppStacks) {
            $r = Invoke-Aws "aws ecs describe-services --cluster $Cluster --services $Project-$app --region $Region --query ""services[0].runningCount"" --output text"
            if ($r.Ok -and $r.Text -match '^\d+$') { $still += [int]$r.Text }
        }
        if ($still -eq 0) { Ok "All tasks stopped"; return }
        Start-Sleep -Seconds 10
    }
    Warn "Tasks still draining; continuing anyway"
}

# ---------------------------------------------------------------------------
# Step 3: empty ECR (force_delete is false, so a repo with images blocks destroy)
# ---------------------------------------------------------------------------

function Clear-EcrRepositories {
    Head "Purging ECR images"
    foreach ($app in $AppStacks) {
        $repo = "$Project-$app"
        $list = Invoke-Aws "aws ecr list-images --repository-name $repo --region $Region --query ""imageIds"" --output json"
        if (-not $list.Ok) {
            if ($list.Text -match "RepositoryNotFound") { Ok "$repo does not exist" } else { Warn "$repo - $($list.Text)" }
            continue
        }
        if ($list.Text -match '^\s*\[\s*\]\s*$') { Ok "$repo already empty"; continue }

        $tmp = Join-Path $env:TEMP "ecr-$repo.json"
        $list.Text | Out-File -FilePath $tmp -Encoding ascii
        $del = Invoke-Aws "aws ecr batch-delete-image --repository-name $repo --region $Region --image-ids file://$tmp"
        Remove-Item $tmp -ErrorAction SilentlyContinue
        if ($del.Ok) { Ok "$repo emptied" } else { Warn "$repo - $($del.Text)" }
    }
}

# ---------------------------------------------------------------------------
# Step 4: terraform destroy, apps before platform
# ---------------------------------------------------------------------------

function Invoke-StackDestroy {
    param([string]$Name)

    $dir = Join-Path $StacksRoot $Name
    if (-not (Test-StackHasState $dir)) {
        Ok "$Name - no state, skipping"
        return $true
    }

    Say ""
    Say "  Destroying $Name..." 
    Push-Location $dir
    cmd /c "terraform destroy -auto-approve -no-color"
    $code = $LASTEXITCODE
    Pop-Location

    if ($code -eq 0) { Ok "$Name destroyed"; return $true }

    Bad "$Name destroy failed (exit $code)"
    Warn "Retrying once - transient dependency ordering is common here"
    Push-Location $dir
    cmd /c "terraform destroy -auto-approve -no-color"
    $code = $LASTEXITCODE
    Pop-Location

    if ($code -eq 0) { Ok "$Name destroyed on retry"; return $true }
    Bad "$Name still failing. Resolve manually before destroying platform."
    return $false
}

function Remove-Everything {
    Disable-Scalers
    Stop-Compute
    Clear-EcrRepositories

    Head "Destroying app stacks"
    $allOk = $true
    foreach ($app in $AppStacks) {
        if (-not (Invoke-StackDestroy $app)) { $allOk = $false }
    }

    if (-not $allOk) {
        Say ""
        Bad "One or more app stacks failed. NOT destroying platform."
        Bad "The platform owns the listener their rules attach to."
        exit 1
    }

    Head "Destroying platform stack"
    Invoke-StackDestroy "platform" | Out-Null

    if ($DeleteHostedZone -and $HostedZoneId) {
        Head "Deleting hosted zone"
        Warn "This breaks the DNS delegation. IT will need to remove the NS records."
        $r = Invoke-Aws "aws route53 delete-hosted-zone --id $HostedZoneId"
        if ($r.Ok) { Ok "Hosted zone deleted" } else { Warn $r.Text }
    }

    Show-Inventory

    Head "Left behind on purpose"
    Say "  - Route 53 hosted zone (unless -DeleteHostedZone)"
    Say "  - ShinyPlatformDeploy IAM policy"
    Say "  - Local .tfstate files and .terraform directories"
    Say "  - Any Terraform state S3 bucket"
    Say ""
    Say "  Confirm spend has stopped in 24h: Billing > Cost Explorer, filter Tag Project=$Project"
}

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

Say ""
Say "Shiny platform teardown" 
Say "Project: $Project   Region: $Region   Stacks: $((Resolve-Path $StacksRoot).Path)"

$id = Invoke-Aws "aws sts get-caller-identity --query Account --output text"
if (-not $id.Ok) { Bad "No AWS credentials."; exit 1 }
Say "Account: $($id.Text)"

if ($Check) { Show-Inventory; exit 0 }

if ($StopCompute) {
    Disable-Scalers
    Stop-Compute
    Head "Compute stopped"
    Say "  Fargate charges have ceased. The ALB still bills ~`$16.43/mo."
    Say "  Re-enable later:  aws events enable-rule --name $Project-dashboard-sleeper --region $Region"
    exit 0
}

if (-not $Destroy) {
    Say ""
    Say "Nothing specified. Use one of:"
    Say "  -Check         show what exists"
    Say "  -StopCompute   stop Fargate spend, keep infrastructure"
    Say "  -Destroy       full teardown"
    exit 0
}

Show-Inventory

if (-not $Force) {
    Say ""
    Write-Host "This permanently destroys the resources listed above." -ForegroundColor Red
    $answer = Read-Host "Type DESTROY to proceed"
    if ($answer -ne "DESTROY") { Say "Aborted."; exit 0 }
}

Remove-Everything
