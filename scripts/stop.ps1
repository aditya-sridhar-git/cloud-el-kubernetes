# Stops the K3s node(s) to save cost. The Elastic IP, EBS volumes and cluster state are kept
# (EBS and the Elastic IP are still billed while stopped).
. "$PSScriptRoot/common.ps1"
Require-Tool terraform
Require-Tool aws

$region = Get-TfOutput region
$ids = Get-InstanceIds
aws ec2 stop-instances --region $region --instance-ids @ids --output table
if ($LASTEXITCODE -ne 0) { throw "stop-instances failed" }
Write-Host "Stopping $($ids -join ', ')" -ForegroundColor Yellow
