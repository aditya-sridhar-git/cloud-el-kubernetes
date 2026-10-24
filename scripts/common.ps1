# Shared helpers, dot-sourced by the other scripts.
$ErrorActionPreference = "Stop"

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:TfDir = Join-Path $RepoRoot "infra/terraform"
$script:KubeconfigPath = Join-Path $RepoRoot ".kube/config"

function Require-Tool([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "'$Name' is not installed or not on PATH."
    }
}

function Get-TfOutput([string]$Name) {
    $value = terraform "-chdir=$TfDir" output -raw $Name
    if ($LASTEXITCODE -ne 0) { throw "terraform output '$Name' failed. Has 'scripts/up.ps1' been run?" }
    return $value
}

function Get-InstanceIds {
    $json = terraform "-chdir=$TfDir" output -json instance_ids
    if ($LASTEXITCODE -ne 0) { throw "terraform output 'instance_ids' failed. Has 'scripts/up.ps1' been run?" }
    return @($json | ConvertFrom-Json)
}
