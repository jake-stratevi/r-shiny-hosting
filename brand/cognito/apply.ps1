<#
.SYNOPSIS
  Applies the Stratevi managed-login branding (settings.json + assets/) to the
  Cognito app client that serves the portal and every proxied app.

.DESCRIPTION
  Terraform cannot own this -- the AWS provider 5.x has no
  aws_cognito_managed_login_branding resource (see proxy/cognito.tf's banner
  and docs/GOTCHAS.md). This script is the substitute: the repo holds the
  desired state, and re-running the script makes the account match it.

  Idempotent. UpdateManagedLoginBranding is a full replace of the Settings
  document and an upsert of each (Category, ColorMode, ResourceId) asset, so
  running it twice is a no-op.

.PARAMETER DryRun
  Build and validate the request, write it to a temp file, print the command,
  and stop without calling AWS.

.EXAMPLE
  ./apply.ps1 -DryRun
  ./apply.ps1
#>
[CmdletBinding()]
param(
    [string] $UserPoolId = 'us-east-1_LI3CZpwAF',
    [string] $ClientId   = '1senki56hh2ngv7neuqhot6gv8',
    [string] $Region     = 'us-east-1',
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition

$settingsPath = Join-Path $here 'settings.json'
$manifestPath = Join-Path $here 'assets\manifest.json'
foreach ($p in @($settingsPath, $manifestPath)) {
    if (-not (Test-Path $p)) { throw "missing $p -- run `node build-assets.mjs` first" }
}

# --- 1. resolve the branding association from the client ---------------------
# Deliberately NOT hardcoded: the association is recreated whenever the client
# is recreated, and it gets a new id each time.
Write-Host "Resolving branding style for client $ClientId in $UserPoolId ..."
$brandingId = $null
try {
    $brandingId = aws cognito-idp describe-managed-login-branding-by-client `
        --user-pool-id $UserPoolId --client-id $ClientId --region $Region `
        --query 'ManagedLoginBranding.ManagedLoginBrandingId' --output text
}
catch {
    $brandingId = $null
}
if ([string]::IsNullOrWhiteSpace($brandingId) -or $brandingId -eq 'None') {
    throw @"
No managed-login branding style is associated with client $ClientId.
Create the defaults-only association first (this is also the fix for
"Login pages unavailable" -- see docs/GOTCHAS.md), then re-run:

  aws cognito-idp create-managed-login-branding ``
    --user-pool-id $UserPoolId --client-id $ClientId ``
    --use-cognito-provided-values
"@
}
$brandingId = $brandingId.Trim()
Write-Host "  style $brandingId"

# --- 2. assemble the request -------------------------------------------------
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

$assets = @()
foreach ($a in $manifest) {
    $file = Join-Path $here (Join-Path 'assets' $a.file)
    if (-not (Test-Path $file)) { throw "manifest references a missing file: $file" }
    $bytes = [System.IO.File]::ReadAllBytes($file)
    if ($bytes.Length -gt 1000000) { throw "$($a.file) is $($bytes.Length) bytes; the AssetBytes limit is 1,000,000" }

    $entry = [ordered]@{
        Category  = $a.category
        ColorMode = $a.colorMode
        Extension = $a.extension
        Bytes     = [System.Convert]::ToBase64String($bytes)
    }
    # ResourceId names the identity provider, and is meaningful only for
    # IDP_BUTTON_ICON. Omit it everywhere else.
    if ($a.PSObject.Properties.Name -contains 'resourceId' -and $a.resourceId) {
        $entry.ResourceId = $a.resourceId
    }
    $assets += [pscustomobject]$entry
    Write-Host ("  asset {0,-18} {1,-5} {2}" -f $a.category, $a.colorMode, $a.file)
}

$payload = [ordered]@{
    UserPoolId             = $UserPoolId
    ManagedLoginBrandingId = $brandingId
    Settings               = $settings
    Assets                 = $assets
}

$json = $payload | ConvertTo-Json -Depth 40
# UTF-8 with NO byte-order mark: the AWS CLI feeds file:// straight to a JSON
# parser and a BOM makes it fail with an unhelpful parse error.
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("shiny-branding-{0}.json" -f ([guid]::NewGuid()))
[System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))

$sizeMb = [math]::Round(([System.Text.Encoding]::UTF8.GetByteCount($json) / 1MB), 3)
Write-Host "Request body: $sizeMb MB across $($assets.Count) assets"
if ($sizeMb -gt 2) {
    throw "request exceeds the 2 MB limit for create/update-managed-login-branding -- split the assets across two calls"
}

# --- 3. apply ----------------------------------------------------------------
$cmd = "aws cognito-idp update-managed-login-branding --region $Region --cli-input-json file://$tmp"
if ($DryRun) {
    Write-Host ""
    Write-Host "DRY RUN -- nothing sent. The request body is at:"
    Write-Host "  $tmp"
    Write-Host "Run it with:"
    Write-Host "  $cmd"
    return
}

Write-Host ""
Write-Host "Applying ..."
aws cognito-idp update-managed-login-branding --region $Region --cli-input-json "file://$tmp" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "update-managed-login-branding failed with exit code $LASTEXITCODE" }
Remove-Item -LiteralPath $tmp -Force

Write-Host "Applied. Preview the result at:"
Write-Host "  https://stratevi-shinyplatform.auth.$Region.amazoncognito.com/login?client_id=$ClientId&response_type=code&scope=openid+email+profile&redirect_uri=https%3A%2F%2Fshinyplatform.tools.stratevi.com%2Foauth2%2Fidpresponse"
Write-Host ""
Write-Host "NOTE: that URL renders the page but CANNOT complete a sign-in -- the ALB"
Write-Host "      rejects a callback it did not start (AuthMissingStateParam). To"
Write-Host "      actually sign in, start from https://shinyplatform.tools.stratevi.com."
