# Destroys ALL infrastructure, including the ECR images, the S3 bucket and the cluster state.
# Use stop.ps1 for routine breaks between sessions.
. "$PSScriptRoot/common.ps1"
Require-Tool terraform

terraform "-chdir=$TfDir" destroy
if ($LASTEXITCODE -ne 0) { throw "terraform destroy failed" }
