variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "ap-south-1"
}

variable "name" {
  description = "Prefix for resource names and the Project tag used by the start/stop scripts."
  type        = string
  default     = "k8s-platform"
}

variable "admin_cidrs" {
  description = "CIDRs allowed to reach the Kubernetes API (6443) and SSH (22), e.g. [\"203.0.113.7/32\"]."
  type        = list(string)
}

variable "budget_email" {
  description = "Email address that receives the AWS Budget alerts."
  type        = string
}

variable "budget_limit_usd" {
  description = "Monthly budget limit. This only sends alerts, it does not stop spending."
  type        = number
  default     = 5
}

variable "server_instance_type" {
  description = "Instance type for the K3s server node. t3.small is the smallest that fits K3s plus the platform; t3.micro (1 GB) does not. Use t3.medium for load experiments."
  type        = string
  default     = "t3.small"
}

variable "worker_count" {
  description = "Number of optional K3s agent nodes (0 or 1 for this project)."
  type        = number
  default     = 0
}

variable "worker_instance_type" {
  type    = string
  default = "t3.small"
}

variable "cpu_credits" {
  description = "\"standard\" or \"unlimited\". Use unlimited during experiments so burst credits do not skew results (may add cost)."
  type        = string
  default     = "standard"
}

variable "root_volume_gb" {
  type    = number
  default = 30
}

variable "k3s_channel" {
  description = "K3s release channel installed on the nodes."
  type        = string
  default     = "stable"
}

variable "ecr_credential_provider_version" {
  description = "cloud-provider-aws release that ships the kubelet ecr-credential-provider binary."
  type        = string
  default     = "v1.31.0"
}

variable "ssh_key_name" {
  description = "Optional existing EC2 key pair for SSH. Leave null to use SSM Session Manager only."
  type        = string
  default     = null
}
