terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Same bucket as every other stack, own state key. See docs/adr/0009.
  backend "s3" {
    bucket       = "stratevi-tf-state-652063276768"
    key          = "shiny/proxy.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      Stack     = "proxy"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
