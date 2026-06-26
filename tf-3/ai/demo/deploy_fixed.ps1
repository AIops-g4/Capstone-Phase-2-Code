<#
.SYNOPSIS
Deploys the AI Engine Dummy API to AWS using Terraform and Docker.
#>

$ErrorActionPreference = "Stop"

$REGION = "us-east-1"
$REPO_NAME = "tf-3-ai-engine"
$TERRAFORM_DIR = ".\terraform"
$APP_DIR = ".\app"

Write-Host "========================================="
Write-Host "1. Initializing Terraform..."
Write-Host "========================================="
Set-Location $TERRAFORM_DIR
terraform init

Write-Host "========================================="
Write-Host "2. Creating ECR Repository..."
Write-Host "========================================="
# Fixed the target argument for PowerShell
terraform apply -target="aws_ecr_repository.repo" -auto-approve

Write-Host "========================================="
Write-Host "3. Fetching ECR Details..."
Write-Host "========================================="
$ACCOUNT_ID = (aws sts get-caller-identity --query Account --output text)
$ECR_URI = "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

Write-Host "Account ID: $ACCOUNT_ID"
Write-Host "ECR URI: $ECR_URI"

Write-Host "========================================="
Write-Host "4. Building and Pushing Docker Image..."
Write-Host "========================================="
Set-Location ..\$APP_DIR

# Fixed Docker login to avoid PowerShell pipeline newline bugs
$pass = aws ecr get-login-password --region $REGION
docker login --username AWS --password $pass "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Build Image
docker build -t $REPO_NAME .

# Tag Image
docker tag "${REPO_NAME}:latest" "${ECR_URI}:latest"

# Push Image
docker push "${ECR_URI}:latest"

Write-Host "========================================="
Write-Host "5. Deploying the rest of the Infrastructure..."
Write-Host "========================================="
Set-Location ..\$TERRAFORM_DIR
terraform apply -auto-approve

Write-Host "========================================="
Write-Host "DEPLOYMENT COMPLETE!"
Write-Host "========================================="
$ALB_DNS = (terraform output -raw alb_dns_name)
Write-Host "The API will be available at: http://${ALB_DNS}:8080/v1/detect"
Write-Host "Wait 2-3 minutes for the ECS Fargate tasks to reach Steady State."
