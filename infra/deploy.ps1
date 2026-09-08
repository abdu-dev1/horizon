<#
Deploy Horizon to Azure. Run from the repo root:

    ./infra/deploy.ps1 -ResourceGroup horizon-rg -EntraClientId <guid> -EntraClientSecret <secret> `
                       -AdminEmails "you@crumdalespecialty.com"

Prerequisites (none of which this script installs):
  * Azure CLI          winget install Microsoft.AzureCLI      then: az login
  * Docker Desktop     winget install Docker.DockerDesktop
  * An Entra app registration for the site. Create it once:
        az ad app create --display-name "Horizon" --sign-in-audience AzureADMyOrg
        az ad app credential reset --id <appId>          # gives you the secret
    then add the redirect URI once the site name is known:
        https://<siteName>.azurewebsites.net/.auth/login/aad/callback

What it does, in order (the order matters):
  1. Provision infrastructure (ACR must exist before we can push to it).
  2. Build and push the image.
  3. Restart the site so it pulls the new tag.

Data is NOT deployed. The image is code only; data and models live on the
mounted Azure Files shares and are installed through the app's Admin page from
a bundle built by publish.py. See DEPLOYMENT_PLAN.md.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ResourceGroup,
    [Parameter(Mandatory = $true)][string]$EntraClientId,
    [Parameter(Mandatory = $true)][string]$EntraClientSecret,
    [string]$AdminEmails = '',
    [string]$Location = 'eastus',
    [string]$NamePrefix = 'horizon',
    [string]$PlanSku = 'B2',
    [string]$ImageTag = (Get-Date -Format 'yyyyMMdd-HHmm')
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

function Step($n, $msg) { Write-Host "`n== $n  $msg" -ForegroundColor Cyan }

# Docker Desktop's per-user installer does NOT add its CLI to PATH (observed
# on this project's own dev machine: Docker Desktop running, docker.exe present
# under %LOCALAPPDATA%, and `Get-Command docker` still failing). Look there
# before giving up, so a working install is not reported as missing.
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $dockerBin = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
    if (Test-Path (Join-Path $dockerBin 'docker.exe')) {
        Write-Host "Using Docker CLI from $dockerBin (not on PATH)" -ForegroundColor Yellow
        $env:Path = "$dockerBin;$env:Path"
    }
}

# `az acr build` does the image build server-side, so Docker is not strictly
# required for a deploy -- only for building/running the image locally. Warn
# rather than block.
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI (az) is not installed or not on PATH. See the header of this script.'
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'Docker not found. Not fatal: the image is built by `az acr build` ' +
               'server-side. You just cannot test the image locally.' -ForegroundColor Yellow
}

Step '1/4' 'ensure the resource group exists'
az group create --name $ResourceGroup --location $Location --output none

Step '2/4' 'provision infrastructure (ACR, storage + shares, plan, site, Easy Auth)'
# imageTag is passed now so the site is created pointing at the tag we are
# about to push; it will be unhealthy until step 3 completes, which is expected.
$deployJson = az deployment group create `
    --resource-group $ResourceGroup `
    --template-file (Join-Path $PSScriptRoot 'main.bicep') `
    --parameters namePrefix=$NamePrefix location=$Location planSku=$PlanSku `
                 imageTag=$ImageTag entraClientId=$EntraClientId `
                 entraClientSecret=$EntraClientSecret adminEmails=$AdminEmails `
    --query properties.outputs --output json
if ($LASTEXITCODE -ne 0) { throw 'infrastructure deployment failed' }

$out = $deployJson | ConvertFrom-Json
$acrLogin = $out.acrLoginServer.value
$acrName = $out.acrNameOut.value
$siteUrl = $out.siteUrl.value

Step '3/4' "build and push $acrLogin/horizon:$ImageTag"
# Built by ACR rather than locally: it keeps the ~1 GB of layers off a laptop
# uplink and guarantees a linux/amd64 image regardless of the dev machine.
az acr build --registry $acrName --image "horizon:$ImageTag" --image 'horizon:latest' `
    --file (Join-Path $repoRoot 'Dockerfile') $repoRoot
if ($LASTEXITCODE -ne 0) { throw 'image build failed' }

Step '4/4' 'restart the site so it pulls the new image'
az webapp restart --resource-group $ResourceGroup --name "$NamePrefix-app" --output none

Write-Host "`nDeployed: $siteUrl" -ForegroundColor Green
Write-Host @"

Next, in order:

  1. Add this redirect URI to the Entra app registration (once, first deploy):
       $siteUrl/.auth/login/aad/callback

  2. The site has NO DATA yet, so /healthz will report degraded and the
     dashboards will not load. That is correct -- serving zeros would be worse.
     Build and install a bundle for each product:
       python publish.py
     then sign in as an admin and upload each zip on that product's page:
       Renewals    -> Model Maintenance
       New Business -> Model Performance

  3. Verify before telling anyone it is live:
       curl -s -o /dev/null -w '%{http_code}' $siteUrl/api/summary    # expect 401 anonymous
       curl -s $siteUrl/healthz                                        # expect 200 once data is in

Watch the container come up:
  az webapp log tail --resource-group $ResourceGroup --name $NamePrefix-app
"@
