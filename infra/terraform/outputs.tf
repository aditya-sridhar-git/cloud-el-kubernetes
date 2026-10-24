output "server_public_ip" {
  description = "Elastic IP of the K3s server (kubeconfig and nip.io hosts use this)."
  value       = aws_eip.server.public_ip
}

output "platform_url" {
  value = "http://platform.${local.nip_base}"
}

output "app_host_suffix" {
  description = "Apps are served at <app>.<this value>."
  value       = local.nip_base
}

output "instance_ids" {
  description = "All K3s node instance IDs, used by scripts/start.ps1 and stop.ps1."
  value       = concat([aws_instance.server.id], aws_instance.worker[*].id)
}

output "region" {
  value = var.region
}

output "ecr_repository_url" {
  value = aws_ecr_repository.apps.repository_url
}

output "source_bucket" {
  value = aws_s3_bucket.sources.bucket
}

output "kubeconfig_s3_uri" {
  value = "s3://${aws_s3_bucket.sources.bucket}/cluster/kubeconfig.yaml"
}

output "codebuild_project" {
  value = aws_codebuild_project.builder.name
}
