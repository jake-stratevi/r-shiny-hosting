# ---------------------------------------------------------------------------
# Bootstrap chicken-and-egg (ADR-0009): this stack creates the S3 bucket that
# every other stack's remote state will live in, so it cannot itself use that
# bucket as a backend -- there is nothing to point at until this applies for
# the first time. State for this one stack stays LOCAL, deliberately.
#
# That local .tfstate never leaves this machine and is not committed (see
# .gitignore: *.tfstate). That's an acceptable risk here specifically because
# this stack is nearly inert once applied -- one bucket, versioning, an
# encryption default and a lifecycle rule, none of which change often. If it's
# ever lost, the bucket can be re-imported (`terraform import`) rather than
# re-created, since re-creating it with the same name would fail anyway (S3
# bucket names are globally unique and this one isn't deleted).
# ---------------------------------------------------------------------------

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # No backend block here. See the header comment above.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      Stack     = "state-backend"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
