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

.PARAMETER Target
  Which deployment to style, naming a file in targets/ (without the .json):
  shiny-platform, amcp-dossier. Mandatory and deliberately undefaulted -- a
  defaulted destination is how you restyle the wrong pool by accident.

.PARAMETER DryRun
  Build and validate the request, write it to a temp file, print the command,
  and stop without calling AWS.

.EXAMPLE
  ./apply.ps1 -Target shiny-platform -DryRun
  ./apply.ps1 -Target amcp-dossier
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $Target,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Definition

# --- 0. resolve the target ---------------------------------------------------
# Cognito has no account-level or cross-pool style: a managed-login style
# belongs to exactly one (user pool, app client) pair. So the repo holds one
# style document and targets/ holds every place it is installed.
$targetPath = Join-Path $here (Join-Path 'targets' "$Target.json")
if (-not (Test-Path $targetPath)) {
    $known = (Get-ChildItem (Join-Path $here 'targets') -Filter *.json |
              ForEach-Object BaseName) -join ', '
    throw "no target '$Target' -- known targets: $known"
}
$t = Get-Content -LiteralPath $targetPath -Raw | ConvertFrom-Json

$UserPoolId      = $t.userPoolId
$ClientId        = $t.clientId
$Region          = $t.region
$IdpResourceName = $t.idpResourceName

Write-Host ""
Write-Host "TARGET  $Target" -ForegroundColor Cyan
Write-Host "  pool    $UserPoolId"
Write-Host "  client  $ClientId"
Write-Host "  idp     $IdpResourceName"
Write-Host ""

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

# The identity provider's name in the pool is the key Cognito matches the
# custom IdP-button treatment on, and it differs per pool (Microsoft365 on the
# platform, Microsoft in stratevi-dossier). settings.json carries the platform
# literal; rename the key for any other target. Get a pool's real name from
# `aws cognito-idp list-identity-providers --user-pool-id <pool>`.
#
# The guard matters more than the rename: point this at a name the pool does
# not have and Cognito does NOT error -- the button silently falls back to the
# grey `standard` treatment and you ship a correct-looking page with the wrong
# button.
$custom = $settings.components.idpButton.custom
if ($custom.PSObject.Properties.Name -notcontains 'Microsoft365') {
    throw "settings.json no longer has components.idpButton.custom.Microsoft365 -- update this block"
}
if ($IdpResourceName -ne 'Microsoft365') {
    $custom | Add-Member -NotePropertyName $IdpResourceName -NotePropertyValue $custom.Microsoft365
    $custom.PSObject.Properties.Remove('Microsoft365')
}

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
    # IDP_BUTTON_ICON. Omit it everywhere else. The manifest's resourceId is
    # now just a flag -- "this asset is an IdP icon" -- and the value comes
    # from the target, same as the settings key above. The filenames still say
    # microsoft365; it is the Microsoft logo either way.
    if ($a.PSObject.Properties.Name -contains 'resourceId' -and $a.resourceId) {
        $entry.ResourceId = $IdpResourceName
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

$redirect = [uri]::EscapeDataString($t.redirectUri)
Write-Host "Applied to $Target. Preview the result at:"
Write-Host "  https://$($t.hostedUiDomain)/login?client_id=$ClientId&response_type=code&scope=openid+email+profile&redirect_uri=$redirect"
Write-Host ""
Write-Host "NOTE: that URL renders the page but CANNOT complete a sign-in -- the"
Write-Host "      relying party rejects a callback it did not start. To actually"
Write-Host "      sign in, start from $($t.entryPoint)."
