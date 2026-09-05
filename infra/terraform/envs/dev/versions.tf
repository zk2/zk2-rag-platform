terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State lives in S3 with DynamoDB locking in a real deployment. Left
  # unconfigured here so the example can be validated without an AWS account:
  #   terraform init -backend-config=backend.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "zk2-rag-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
