# ---------------------------------------------------------------------------
# Preflight permission check (Windows / PowerShell) — v2
#
#   powershell -ExecutionPolicy Bypass -File preflight.ps1
#
# v2 fixes two bugs in v1:
#   1. `& aws @Args 2>&1` wraps native stderr into PowerShell ErrorRecord
#      objects, so the AccessDenied text was never matched and every call fell
#      through to UNCLEAR. This version shells the redirect out to cmd.exe,
#      which returns plain strings.
#   2. UNCLEAR results were counted as passes, which is how v1 reported
#      "all 17 checks passed" on 17 unparseable results. They are now counted
#      and reported separately, and a run with any UNCLEAR exits non-zero.
#
# A PASS proves the service is reachable, not that you can write to it. But
# "service missing from the policy entirely" is the failure that actually
# happens, and this catches it.
# ---------------------------------------------------------------------------

param(
    [string]$Region = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" })
)

$script:Pass    = 0
$script:Denied  = @()
$script:Unclear = @()

function Test-Permission {
    param([string]$Label, [string]$Command)

    # cmd.exe merges stderr into stdout as plain text. PowerShell's own 2>&1
    # hands back ErrorRecord objects instead, which is what broke v1.
    $text = (cmd /c "$Command 2>&1" | Out-String)
    $code = $LASTEXITCODE

    if ($code -eq 0) {
        Write-Host "  PASS     $Label" -ForegroundColor Green
        $script:Pass++
    }
    elseif ($text -match 'AccessDenied|UnauthorizedOperation|not authorized|AccessDeniedException') {
        Write-Host "  DENIED   $Label" -ForegroundColor Red
        $script:Denied += $Label
    }
    else {
        Write-Host "  UNCLEAR  $Label" -ForegroundColor Yellow
        $snippet = $text.Trim() -replace '\s+', ' '
        if ($snippet.Length -gt 130) { $snippet = $snippet.Substring(0, 130) + "..." }
        Write-Host "           $snippet" -ForegroundColor DarkGray
        $script:Unclear += $Label
    }
}

Write-Host ""
Write-Host "Preflight permission check (v2)" -ForegroundColor Cyan
Write-Host "Region: $Region"

$identityRaw = (cmd /c "aws sts get-caller-identity --output json 2>&1" | Out-String)
if ($LASTEXITCODE -ne 0) {
    Write-Host "Cannot call sts:GetCallerIdentity. Credentials are not configured." -ForegroundColor Red
    Write-Host $identityRaw
    exit 1
}
$identity = $identityRaw | ConvertFrom-Json
Write-Host "Account:  $($identity.Account)"
Write-Host "Identity: $($identity.Arn)"
Write-Host ""

Write-Host "DNS and certificates"
Test-Permission "route53:ListHostedZones" "aws route53 list-hosted-zones --max-items 1"
Test-Permission "acm:ListCertificates"    "aws acm list-certificates --region $Region --max-items 1"

Write-Host ""
Write-Host "Networking"
Test-Permission "ec2:DescribeVpcs"              "aws ec2 describe-vpcs --region $Region --max-items 1"
Test-Permission "ec2:DescribeSubnets"           "aws ec2 describe-subnets --region $Region --max-items 1"
Test-Permission "ec2:DescribeSecurityGroups"    "aws ec2 describe-security-groups --region $Region --max-items 1"
Test-Permission "ec2:DescribeAvailabilityZones" "aws ec2 describe-availability-zones --region $Region"

Write-Host ""
Write-Host "Load balancing"
Test-Permission "elasticloadbalancing:DescribeLoadBalancers" "aws elbv2 describe-load-balancers --region $Region --page-size 1"

Write-Host ""
Write-Host "Containers"
Test-Permission "ecs:ListClusters"         "aws ecs list-clusters --region $Region --max-items 1"
Test-Permission "ecr:DescribeRepositories" "aws ecr describe-repositories --region $Region --max-items 1"

Write-Host ""
Write-Host "Scaler"
Test-Permission "lambda:ListFunctions" "aws lambda list-functions --region $Region --max-items 1"
Test-Permission "events:ListRules"     "aws events list-rules --region $Region --limit 1"

Write-Host ""
Write-Host "Observability and config"
Test-Permission "logs:DescribeLogGroups"    "aws logs describe-log-groups --region $Region --limit 1"
Test-Permission "cloudwatch:ListDashboards" "aws cloudwatch list-dashboards --region $Region"
Test-Permission "ssm:DescribeParameters"    "aws ssm describe-parameters --region $Region --max-items 1"

Write-Host ""
Write-Host "Authentication"
Test-Permission "cognito-idp:ListUserPools" "aws cognito-idp list-user-pools --region $Region --max-results 1"

Write-Host ""
Write-Host "IAM (the one most often missing)"
Test-Permission "iam:ListRoles" "aws iam list-roles --max-items 1"

Write-Host ""
Write-Host "Cost"
Test-Permission "budgets:DescribeBudgets" "aws budgets describe-budgets --account-id $($identity.Account) --max-results 1"

$total = $script:Pass + $script:Denied.Count + $script:Unclear.Count

Write-Host ""
Write-Host "-----------------------------------------"
Write-Host "Passed:  $($script:Pass) / $total"
Write-Host "Denied:  $($script:Denied.Count)"
Write-Host "Unclear: $($script:Unclear.Count)"
Write-Host ""

if ($script:Denied.Count -gt 0) {
    Write-Host "Denied - these need policy changes:" -ForegroundColor Red
    foreach ($d in $script:Denied) { Write-Host "    - $d" }
    Write-Host ""
}

if ($script:Unclear.Count -gt 0) {
    Write-Host "Unclear - investigate before applying:" -ForegroundColor Yellow
    foreach ($u in $script:Unclear) { Write-Host "    - $u" }
    Write-Host ""
}

if ($script:Denied.Count -eq 0 -and $script:Unclear.Count -eq 0) {
    Write-Host "All checks passed. Clear to run terraform apply." -ForegroundColor Green
    Write-Host ""
    Write-Host "Note: read access does not prove write access. If apply fails on a" -ForegroundColor DarkGray
    Write-Host "Create* action, the policy is scoped read-only for that service." -ForegroundColor DarkGray
}
else {
    Write-Host "Not clear to apply. Send shiny-platform-deploy-policy.json to your"
    Write-Host "AWS administrator with the denied list above."
    exit 1
}
