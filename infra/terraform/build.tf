resource "aws_ecr_repository" "apps" {
  name                 = "${var.name}-apps"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = false
  }
}

resource "aws_ecr_lifecycle_policy" "apps" {
  repository = aws_ecr_repository.apps.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the most recent 50 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 50
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_s3_bucket" "sources" {
  bucket        = "${var.name}-src-${local.account}-${var.region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "sources" {
  bucket                  = aws_s3_bucket.sources.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sources" {
  bucket = aws_s3_bucket.sources.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Source zips are only needed for the duration of a build. The cluster/ prefix (kubeconfig) is kept.
resource "aws_s3_bucket_lifecycle_configuration" "sources" {
  bucket = aws_s3_bucket.sources.id

  rule {
    id     = "expire-source-zips"
    status = "Enabled"
    filter {
      prefix = "sources/"
    }
    expiration {
      days = 7
    }
  }
}

data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codebuild" {
  name               = "${var.name}-codebuild"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json
}

data "aws_iam_policy_document" "codebuild" {
  statement {
    sid       = "ReadSources"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.sources.arn}/sources/*"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/codebuild/${var.name}-builder*"]
  }

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
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.apps.arn]
  }
}

resource "aws_iam_role_policy" "codebuild" {
  name   = "build"
  role   = aws_iam_role.codebuild.id
  policy = data.aws_iam_policy_document.codebuild.json
}

# The source location is a placeholder; the backend passes sourceLocationOverride and an
# IMAGE_TAG environment variable on every StartBuild.
resource "aws_codebuild_project" "builder" {
  name         = "${var.name}-builder"
  service_role = aws_iam_role.codebuild.arn

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.sources.bucket}/sources/placeholder.zip"
    buildspec = file("${path.module}/../codebuild/buildspec.yml")
  }

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/standard:7.0"
    privileged_mode = true # required for docker build

    environment_variable {
      name  = "ECR_REPO_URI"
      value = aws_ecr_repository.apps.repository_url
    }
    environment_variable {
      name  = "AWS_REGION"
      value = var.region
    }
  }

  build_timeout = 15
}
