# Builds the backend and frontend images with CodeBuild and (re)deploys the platform to K3s.
# Prerequisites: scripts/up.ps1 and scripts/get-kubeconfig.ps1 have been run.
param(
    [switch]$SkipBuild,       # re-apply manifests with the images from the previous run
    [string]$BackendImage,
    [string]$FrontendImage
)
. "$PSScriptRoot/common.ps1"
Require-Tool terraform
Require-Tool aws
Require-Tool kubectl

if (-not (Test-Path $KubeconfigPath)) { throw "No kubeconfig at $KubeconfigPath. Run scripts/get-kubeconfig.ps1 first." }
$env:KUBECONFIG = $KubeconfigPath

$region = Get-TfOutput region
$suffix = Get-TfOutput app_host_suffix
$platformHost = "platform.$suffix"
$lastImagesFile = Join-Path $RepoRoot ".kube/platform-images.json"

if ($SkipBuild) {
    if (-not ($BackendImage -and $FrontendImage)) {
        if (-not (Test-Path $lastImagesFile)) { throw "-SkipBuild needs a previous build or -BackendImage/-FrontendImage" }
        $last = Get-Content $lastImagesFile -Raw | ConvertFrom-Json
        if (-not $BackendImage) { $BackendImage = $last.backend }
        if (-not $FrontendImage) { $FrontendImage = $last.frontend }
    }
} else {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $BackendImage = & "$PSScriptRoot/build-image.ps1" -Context backend -Tag "platform-backend-$stamp" | Select-Object -Last 1
    $FrontendImage = & "$PSScriptRoot/build-image.ps1" -Context frontend -Tag "platform-frontend-$stamp" | Select-Object -Last 1
    @{ backend = $BackendImage; frontend = $FrontendImage } | ConvertTo-Json | Set-Content $lastImagesFile -Encoding ascii
}

function Apply-Yaml([string]$Yaml) {
    $Yaml | kubectl apply -f -
    if ($LASTEXITCODE -ne 0) { throw "kubectl apply failed" }
}

kubectl create namespace platform --dry-run=client -o yaml | kubectl apply -f -
if ($LASTEXITCODE -ne 0) { throw "cannot reach the cluster; is it started? (scripts/start.ps1)" }

# Configuration from Terraform outputs.
$configYaml = kubectl create configmap platform-config -n platform --dry-run=client -o yaml `
    "--from-literal=AWS_REGION=$region" `
    "--from-literal=AWS_DEFAULT_REGION=$region" `
    "--from-literal=ECR_REPO_URI=$(Get-TfOutput ecr_repository_url)" `
    "--from-literal=SOURCE_BUCKET=$(Get-TfOutput source_bucket)" `
    "--from-literal=CODEBUILD_PROJECT=$(Get-TfOutput codebuild_project)" `
    "--from-literal=BUILDER=codebuild" `
    "--from-literal=APP_HOST_SUFFIX=$suffix" `
    "--from-literal=APPS_NAMESPACE=apps" `
    "--from-literal=PUBLIC_BASE_URL=http://$platformHost"
Apply-Yaml ($configYaml -join "`n")

# API token: keep the existing one so dashboard logins and scripts keep working.
# (A missing secret makes kubectl write to stderr, which "Stop" would turn into a terminating error.)
$ErrorActionPreference = "Continue"
$existing = kubectl get secret platform-secrets -n platform -o "jsonpath={.data.API_TOKEN}" 2>$null
$found = $LASTEXITCODE -eq 0 -and $existing
$ErrorActionPreference = "Stop"
if ($found) {
    $token = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($existing))
} else {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = -join ($bytes | ForEach-Object { $_.ToString("x2") })
    $secretYaml = kubectl create secret generic platform-secrets -n platform --dry-run=client -o yaml "--from-literal=API_TOKEN=$token"
    Apply-Yaml ($secretYaml -join "`n")
}

$manifest = (Get-Content (Join-Path $RepoRoot "k8s/platform/platform.yaml") -Raw).
    Replace("__BACKEND_IMAGE__", $BackendImage).
    Replace("__FRONTEND_IMAGE__", $FrontendImage).
    Replace("__PLATFORM_HOST__", $platformHost)
Apply-Yaml $manifest

kubectl rollout status deployment/platform-backend -n platform --timeout=300s
if ($LASTEXITCODE -ne 0) { throw "backend did not become ready: kubectl -n platform logs deploy/platform-backend" }
kubectl rollout status deployment/platform-frontend -n platform --timeout=180s
if ($LASTEXITCODE -ne 0) { throw "frontend did not become ready: kubectl -n platform describe deploy/platform-frontend" }

Write-Host ""
Write-Host "Dashboard:  http://$platformHost" -ForegroundColor Green
Write-Host "API token:  $token" -ForegroundColor Green
Write-Host "Webhook:    http://$platformHost/api/webhooks/github"
