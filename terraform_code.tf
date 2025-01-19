# Provider configuration
provider "aws" {
  access_key = "AKIA2RP6H7VO5IJS57PT"
  secret_key = "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"
  region     = "us-west-2"
}

# Resource block
resource "aws_s3_bucket" "my-bucket" {
  bucket = "tryryyry"
}