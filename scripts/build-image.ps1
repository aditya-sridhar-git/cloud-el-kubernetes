# Builds a Docker image from a local folder with the platform's CodeBuild project and pushes it to
# ECR, so no local Docker daemon is needed. Prints and returns the image URI.
#   scripts/build-image.ps1 -Context backend -Tag platform-backend-20261024-1200
param(
    [Parameter(Mandatory)] [string]$Context,
    [Parameter(Mandatory)] [string]$Tag
)
. "$PSScriptRoot/common.ps1"
Require-Tool terraform
Require-Tool aws

$region = Get-TfOutput region
$bucket = Get-TfOutput source_bucket
$project = Get-TfOutput codebuild_project
$repo = Get-TfOutput ecr_repository_url

$root = (Resolve-Path (Join-Path $RepoRoot $Context)).Path
if (-not (Test-Path (Join-Path $root "Dockerfile"))) { throw "$root has no Dockerfile" }

# Zip the folder, skipping local-only directories (Compress-Archive cannot exclude paths).
$excluded = @("node_modules", ".venv", "dist", "__pycache__", ".pytest_cache", "data", ".git")
$zip = Join-Path ([System.IO.Path]::GetTempPath()) "$Tag.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::Open($zip, "Create")
try {
    Get-ChildItem -Path $root -Recurse -File -Force | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length + 1).Replace("\", "/")
        $parts = $relative.Split("/")
        if (-not ($parts | Where-Object { $excluded -contains $_ })) {
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $_.FullName, $relative) | Out-Null
        }
    }
} finally {
    $archive.Dispose()
}

$key = "sources/platform/$Tag.zip"
aws s3 cp $zip "s3://$bucket/$key" --region $region --only-show-errors
if ($LASTEXITCODE -ne 0) { throw "upload to S3 failed" }
Remove-Item $zip -Force

$buildId = aws codebuild start-build --region $region --project-name $project `
    --source-type-override S3 --source-location-override "$bucket/$key" `
    --environment-variables-override "name=IMAGE_TAG,value=$Tag,type=PLAINTEXT" `
    --query "build.id" --output text
if ($LASTEXITCODE -ne 0) { throw "codebuild start-build failed" }
Write-Host "CodeBuild $buildId building $repo`:$Tag ..."

while ($true) {
    Start-Sleep -Seconds 10
    $info = aws codebuild batch-get-builds --region $region --ids $buildId `
        --query "builds[0].[buildStatus,currentPhase]" --output text
    $status, $phase = $info -split "\s+"
    Write-Host "  $status / $phase"
    if ($status -eq "SUCCEEDED") { break }
    if ($status -ne "IN_PROGRESS") {
        throw "Build $buildId $status. Logs: AWS console > CodeBuild > $project > build history"
    }
}

$image = "$repo`:$Tag"
Write-Host "Pushed $image" -ForegroundColor Green
return $image
