# Downloads the cluster kubeconfig (published to S3 by the server's bootstrap script) and points
# it at the server's Elastic IP. Writes .kube/config in the repo (git-ignored).
. "$PSScriptRoot/common.ps1"
Require-Tool terraform
Require-Tool aws

$uri = Get-TfOutput kubeconfig_s3_uri
$ip = Get-TfOutput server_public_ip
$region = Get-TfOutput region

New-Item -ItemType Directory -Force (Split-Path $KubeconfigPath) | Out-Null
aws s3 cp $uri $KubeconfigPath --region $region
if ($LASTEXITCODE -ne 0) {
    throw "Kubeconfig not in S3 yet. K3s may still be bootstrapping; check /var/log/k3s-bootstrap.log on the server (SSM Session Manager) and retry."
}

(Get-Content $KubeconfigPath -Raw).Replace("https://127.0.0.1:6443", "https://${ip}:6443") |
    Set-Content -Path $KubeconfigPath -Encoding ascii -NoNewline

Write-Host "Wrote $KubeconfigPath" -ForegroundColor Green
Write-Host "Use it with:  `$env:KUBECONFIG = '$KubeconfigPath'; kubectl get nodes"
