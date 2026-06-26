# Capstone Phase 2: TF3 AI Engine Skeleton (Demo)

This repository contains the Week 11 "Dummy API" skeleton for **Task Force 3 (Self-Heal Engine)**. It provides a fully working, contract-compliant JSON API that the CDO team can use to test their infrastructure integration while the real AI logic is built in Week 12.

## 📂 Repository Structure

```text
test/demo/
├── app/
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── terraform/
│   ├── provider.tf
│   ├── vpc.tf
│   ├── ecr.tf
│   ├── iam.tf
│   ├── alb.tf
│   └── ecs.tf
├── deploy_fixed.ps1
├── destroy.ps1
└── readme.md
```

- `/app`: The Python FastAPI application. Contains the 3 required endpoints (`/v1/detect`, `/v1/decide`, `/v1/verify`) returning hardcoded dummy data matching the AI API contracts.
- `/terraform`: The AWS Infrastructure as Code (IaC) required by the Deployment Contract. Includes VPC, ECR, ECS (Fargate), internal ALB, and scoped IAM roles.
- `deploy_fixed.ps1`: An automated script to create the ECR repo, build & push the Docker image, and provision the ECS services.
- `destroy.ps1`: An automated script to wipe all Terraform-created AWS resources.

---

## 🚀 How to Deploy

**Prerequisites:**
1. AWS CLI installed and configured (`aws configure`).
2. Terraform installed.
3. **Docker Desktop installed and RUNNING**.

**Steps:**
1. Open a PowerShell terminal.
2. Navigate to this directory: `..\..\demo`
3. Run the deployment script:
   ```powershell
   .\deploy_fixed.ps1
   ```
4. Wait 2-3 minutes for the Fargate tasks to spin up. The script will output the Internal ALB DNS name.

---

## 🧪 How to Test the API

There are two main ways to test the endpoints.

### Option 1: Test Locally (Fastest)
You can run the API locally on your laptop without touching AWS.

1. Open a terminal in the `app` folder and start the server:
   ```powershell
   cd app
   pip install -r requirements.txt
   uvicorn main:app --host 127.0.0.1 --port 8080
   ```
2. Open a **new** PowerShell terminal and run the test commands below. *(Ensure you copy the entire block!)*

**Test `/v1/detect`:**
```powershell
$headers = @{ "X-Tenant-Id" = "tnt-re2-simulation"; "Content-Type" = "application/json"; "Authorization" = "AWS4-HMAC-SHA256 fake"; "Idempotency-Key" = "test-idem-key-123"; "X-Dry-Run-Mode" = "true" }
$body = @{ 
    idempotency_key = "123e4567-e89b-12d3-a456-426614174001"
    correlation_id = "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o"
    dry_run_mode = $true
    telemetry_window = @( @{ ts="2026-06-25T10:00:00.123Z"; signal_name="istio_request_error_rate"; value=0.45 } ) 
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/detect" -Method POST -Headers $headers -Body $body
```

**Test `/v1/decide`:**
```powershell
$headers = @{ "X-Tenant-Id" = "tnt-re2-simulation"; "Content-Type" = "application/json"; "Authorization" = "AWS4-HMAC-SHA256 fake"; "X-Correlation-Id" = "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o"; "Idempotency-Key" = "test-idem-key-123"; "X-Dry-Run-Mode" = "true" }
$body = @{ 
    idempotency_key = "test-idem-key-123"
    correlation_id = "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o"
    dry_run_mode = $true
    anomaly_context = @{ target_service="order-service"; suspected_fault_type="database_connection_failure"; system="E-COMMERCE"; namespace="production"; deployment="order-service"; trigger_metric="service_error_rate"; trigger_value=0.15 } 
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/decide" -Method POST -Headers $headers -Body $body
```

**Test `/v1/verify`:**
```powershell
$headers = @{ "X-Tenant-Id" = "tnt-re2-simulation"; "Content-Type" = "application/json"; "Authorization" = "AWS4-HMAC-SHA256 fake"; "X-Correlation-Id" = "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o"; "Idempotency-Key" = "test-idem-key-456"; "X-Dry-Run-Mode" = "true" }
$body = @{ 
    idempotency_key = "test-idem-key-456"
    correlation_id = "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o"
    dry_run_mode = $true
    action_executed = @{ action="RESTART_DEPLOYMENT"; target="deployment/order-service"; status="COMPLETED"; execution_time_seconds=45 }
    post_telemetry_window = @( @{ ts="2026-06-25T10:02:00.000Z"; tenant_id="d3b07384-d113-495f-9f58-20d18d357d75"; service="order-service"; signal_name="service_error_rate"; value=0.00; labels=@{ system="E-COMMERCE"; namespace="production"; deployment="order-service" } } )
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/verify" -Method POST -Headers $headers -Body $body
```


### Option 2: Test via the AWS Internal ALB
By contract, the AI Engine is secured behind an **Internal Application Load Balancer**. This means you cannot test it directly from your personal laptop over the public internet.

To test the live AWS infrastructure:
1. Provide the Internal ALB DNS name (e.g., `internal-tf-3-ai-engine-alb-XXX.us-east-1.elb.amazonaws.com`) to the CDO team. They will hit the endpoint from their EC2/EKS instances located inside the same VPC.
2. Alternatively, if you want to test it yourself, launch a tiny Amazon Linux EC2 instance (Bastion Host) into the Public Subnet of your `tf3-demo-vpc`. Connect to the EC2 via the AWS Console, and run `curl` commands from inside the VPC.

---

## 🧹 Cleanup
To avoid AWS charges, destroy the infrastructure when done:
```powershell
.\destroy.ps1
```
