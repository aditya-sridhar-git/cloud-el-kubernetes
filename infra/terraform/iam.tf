# One role shared by all K3s nodes. Kubelet uses it to pull from ECR (via the credential
# provider) and the backend pod uses it for S3/CodeBuild/CloudWatch (via IMDS).
# Acceptable for a demo; split into per-workload roles for anything production-like.
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name}-node"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "node_ecr_read" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy_attachment" "node_ssm" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "node_backend" {
  statement {
    sid       = "SourceBucketObjects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.sources.arn}/*"]
  }

  statement {
    sid       = "SourceBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.sources.arn]
  }

  statement {
    sid       = "StartAndPollBuilds"
    actions   = ["codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:StopBuild"]
    resources = [aws_codebuild_project.builder.arn]
  }

  statement {
    sid       = "ReadBuildLogs"
    actions   = ["logs:GetLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/codebuild/${var.name}-builder:*"]
  }

  # Only needed by the Docker SDK dev builder; harmless once builds move to CodeBuild.
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:DescribeImages",
      "ecr:ListImages",
    ]
    resources = [aws_ecr_repository.apps.arn]
  }
}

resource "aws_iam_role_policy" "node_backend" {
  name   = "backend"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.node_backend.json
}

resource "aws_iam_instance_profile" "node" {
  name = "${var.name}-node"
  role = aws_iam_role.node.name
}
