resource "aws_ecr_repository" "repo" {
  name                 = "tf-3-ai-engine"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}
