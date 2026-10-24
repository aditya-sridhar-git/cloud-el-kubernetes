data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Latest Ubuntu 24.04 LTS AMI, resolved through Canonical's public SSM parameter.
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

locals {
  subnet_id = sort(data.aws_subnets.default.ids)[0]
  account   = data.aws_caller_identity.current.account_id
  nip_base  = "${aws_eip.server.public_ip}.nip.io"
}

resource "random_password" "k3s_token" {
  length  = 40
  special = false
}

resource "aws_security_group" "nodes" {
  name        = "${var.name}-nodes"
  description = "K3s nodes: public HTTP(S), admin-only API and SSH"
  vpc_id      = data.aws_vpc.default.id

  # Webhooks and the dashboard come in over the Traefik ingress.
  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Kubernetes API"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = var.admin_cidrs
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.admin_cidrs
  }

  ingress {
    description = "Node to node (API, flannel, kubelet)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_eip" "server" {
  domain = "vpc"
  tags   = { Name = "${var.name}-server" }
}

resource "aws_instance" "server" {
  ami                    = nonsensitive(data.aws_ssm_parameter.ubuntu.value)
  instance_type          = var.server_instance_type
  subnet_id              = local.subnet_id
  vpc_security_group_ids = [aws_security_group.nodes.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  key_name               = var.ssh_key_name

  credit_specification {
    cpu_credits = var.cpu_credits
  }

  # hop limit 2 lets pods (the backend) reach the instance profile credentials.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    role                 = "server"
    k3s_channel          = var.k3s_channel
    k3s_token            = random_password.k3s_token.result
    server_ip            = ""
    public_ip            = aws_eip.server.public_ip
    region               = var.region
    kubeconfig_s3_uri    = "s3://${aws_s3_bucket.sources.bucket}/cluster/kubeconfig.yaml"
    ecr_provider_version = var.ecr_credential_provider_version
  })

  tags = { Name = "${var.name}-server", Role = "k3s-server" }

  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_eip_association" "server" {
  instance_id   = aws_instance.server.id
  allocation_id = aws_eip.server.id
}

resource "aws_instance" "worker" {
  count                  = var.worker_count
  ami                    = nonsensitive(data.aws_ssm_parameter.ubuntu.value)
  instance_type          = var.worker_instance_type
  subnet_id              = local.subnet_id
  vpc_security_group_ids = [aws_security_group.nodes.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name
  key_name               = var.ssh_key_name

  credit_specification {
    cpu_credits = var.cpu_credits
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    role                 = "agent"
    k3s_channel          = var.k3s_channel
    k3s_token            = random_password.k3s_token.result
    server_ip            = aws_instance.server.private_ip
    public_ip            = ""
    region               = var.region
    kubeconfig_s3_uri    = ""
    ecr_provider_version = var.ecr_credential_provider_version
  })

  tags = { Name = "${var.name}-worker-${count.index + 1}", Role = "k3s-agent" }

  lifecycle {
    ignore_changes = [ami]
  }
}
