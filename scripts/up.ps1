# Provisions (or updates) all AWS infrastructure: EC2 + K3s, ECR, S3, CodeBuild, Budget.
# Requires infra/terraform/terraform.tfvars (copy from terraform.tfvars.example).
. "$PSScriptRoot/common.ps1"
Require-Tool terraform
Require-Tool aws

if (-not (Test-Path (Join-Path $TfDir "terraform.tfvars"))) {
    throw "Missing infra/terraform/terraform.tfvars. Copy terraform.tfvars.example and fill it in."
}

terraform "-chdir=$TfDir" init -input=false
if ($LASTEXITCODE -ne 0) { throw "terraform init failed" }

terraform "-chdir=$TfDir" apply
if ($LASTEXITCODE -ne 0) { throw "terraform apply failed" }

Write-Host "`nInfrastructure is up. K3s takes a few minutes to bootstrap on first boot." -ForegroundColor Green
Write-Host "Then run: scripts/get-kubeconfig.ps1"
