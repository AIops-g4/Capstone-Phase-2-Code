terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = {
      Project = "TF3-Self-Heal-Engine"
      Team    = "AI"
    }
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
