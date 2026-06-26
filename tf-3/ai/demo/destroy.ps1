<#
.SYNOPSIS
Destroys all AWS infrastructure created by Terraform.
#>

$ErrorActionPreference = "Stop"
$TERRAFORM_DIR = ".\terraform"

Write-Host "========================================="
Write-Host "WARNING: Destroying all Terraform resources..."
Write-Host "========================================="
Set-Location $TERRAFORM_DIR

# Run the destroy command
terraform destroy -auto-approve

Write-Host "========================================="
Write-Host "CLEANUP COMPLETE!"
Write-Host "========================================="
