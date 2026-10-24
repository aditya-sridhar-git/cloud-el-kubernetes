# Starts the stopped K3s node(s) and waits until they are running. The Elastic IP is
# unchanged, so the existing kubeconfig keeps working once K3s is back (about a minute).
. "$PSScriptRoot/common.ps1"
Require-Tool terraform
Require-Tool aws

$region = Get-TfOutput region
$ids = Get-InstanceIds
aws ec2 start-instances --region $region --instance-ids @ids --output table
if ($LASTEXITCODE -ne 0) { throw "start-instances failed" }

aws ec2 wait instance-running --region $region --instance-ids @ids
if ($LASTEXITCODE -ne 0) { throw "instances did not reach running state" }

Write-Host "Running. Platform: $(Get-TfOutput platform_url)" -ForegroundColor Green
