terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # backend "s3" {
  #   bucket       = "your-tf-state-bucket"
  #   key          = "shiny/dashboard.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      Stack     = var.app_key
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
