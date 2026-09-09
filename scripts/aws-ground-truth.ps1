# Read-only AWS ground-truth enumeration for the r-shiny-hosting recovery.
# Nothing here creates, modifies, or deletes anything -- only describe-*/list-*/get-*
# calls. Run from anywhere; writes docs\aws-ground-truth.json relative to the
# current directory, so run this from the repo root:
#   cd C:\Users\JakePistotnik\Desktop\r-shiny-hosting
#   .\scripts\aws-ground-truth.ps1

$ErrorActionPreference = "Continue"
$result = [ordered]@{}
$result.generated_at = (Get-Date).ToUniversalTime().ToString("o")

function Try-Aws($block, $key) {
    try { $result[$key] = & $block }
    catch { $result[$key] = @{ error = $_.Exception.Message } }
}

Try-Aws { aws sts get-caller-identity --output json | ConvertFrom-Json } "caller_identity"

# ALB / VPC / ECS
Try-Aws { aws elbv2 describe-load-balancers --output json | ConvertFrom-Json } "load_balancers"
Try-Aws { aws elbv2 describe-target-groups --output json | ConvertFrom-Json } "target_groups"
Try-Aws { aws ec2 describe-vpcs --filters "Name=tag:Project,Values=shiny" --output json | ConvertFrom-Json } "vpcs"
Try-Aws { aws ec2 describe-subnets --filters "Name=tag:Project,Values=shiny" --output json | ConvertFrom-Json } "subnets"
Try-Aws { aws ecs describe-clusters --clusters shiny-cluster --include TAGS --output json | ConvertFrom-Json } "ecs_cluster"
Try-Aws {
    $arns = (aws ecs list-services --cluster shiny-cluster --output json | ConvertFrom-Json).serviceArns
    if ($arns) { aws ecs describe-services --cluster shiny-cluster --services $arns --output json | ConvertFrom-Json }
    else { @{ note = "no services" } }
} "ecs_services"

# ECR
Try-Aws { aws ecr describe-repositories --output json | ConvertFrom-Json } "ecr_repositories"
$result.ecr_images = @{}
if ($result.ecr_repositories -and $result.ecr_repositories.repositories) {
    foreach ($repo in $result.ecr_repositories.repositories) {
        try { $result.ecr_images[$repo.repositoryName] = aws ecr describe-images --repository-name $repo.repositoryName --output json | ConvertFrom-Json }
        catch { $result.ecr_images[$repo.repositoryName] = @{ error = $_.Exception.Message } }
    }
}

# Cognito -- includes the safety check CHANGES-hub-pool-and-allowlist.md requires
# before the pool swap: destroying the self-created pool is only safe if it has
# zero users.
Try-Aws { aws cognito-idp list-user-pools --max-results 60 --output json | ConvertFrom-Json } "cognito_pools"
$result.cognito_pool_details = @{}
$result.cognito_app_clients  = @{}
$result.cognito_user_counts  = @{}
if ($result.cognito_pools -and $result.cognito_pools.UserPools) {
    foreach ($p in $result.cognito_pools.UserPools) {
        try { $result.cognito_pool_details[$p.Id] = aws cognito-idp describe-user-pool --user-pool-id $p.Id --output json | ConvertFrom-Json } catch {}
        try { $result.cognito_app_clients[$p.Id]  = aws cognito-idp list-user-pool-clients --user-pool-id $p.Id --output json | ConvertFrom-Json } catch {}
        try { $result.cognito_user_counts[$p.Id]  = (aws cognito-idp list-users --user-pool-id $p.Id --output json | ConvertFrom-Json).Users.Count } catch {}
    }
}

# Lambda / SSM / Route 53
Try-Aws { aws lambda list-functions --output json | ConvertFrom-Json } "lambdas"
Try-Aws { aws ssm get-parameters-by-path --path "/shiny/platform" --max-items 100 --output json | ConvertFrom-Json } "ssm_parameters"
Try-Aws { aws route53 list-resource-record-sets --hosted-zone-id Z07112442ZAIA7CFJKV72 --output json | ConvertFrom-Json } "route53_records"

# Cost Explorer -- month to date, tag Project=shiny
#
# NOTE: --filter takes a JSON string, and passing that JSON inline as a
# PowerShell argument to a native exe strips the double quotes on Windows
# (the same class of bug docs/GOTCHAS.md already catalogs for --query and
# stderr redirection). Writing the filter to a file and passing
# --filter file://... sidesteps the quoting entirely.
$start = (Get-Date -Day 1).ToString("yyyy-MM-dd")
$end   = (Get-Date).ToString("yyyy-MM-dd")
Try-Aws {
    $filterPath = Join-Path $env:TEMP "shiny-ce-filter.json"
    '{"Tags":{"Key":"Project","Values":["shiny"]}}' | Set-Content -Path $filterPath -Encoding ascii -NoNewline
    aws ce get-cost-and-usage `
        --time-period Start=$start,End=$end `
        --granularity MONTHLY `
        --metrics "UnblendedCost" `
        --filter "file://$filterPath" `
        --output json | ConvertFrom-Json
} "cost_explorer_mtd"

$outDir = "docs"
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$result | ConvertTo-Json -Depth 12 | Out-File -Encoding utf8 (Join-Path $outDir "aws-ground-truth.json")
Write-Host "Wrote docs\aws-ground-truth.json"
