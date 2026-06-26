from fastapi import FastAPI, Header, Body, HTTPException
from typing import Optional, Dict, Any, List
import uuid

app = FastAPI(title="Dummy AI Engine API")

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": "2026-06-25T10:00:00Z"}

@app.get("/ready")
def readiness_check():
    return {
        "status": "ready",
        "dependencies": {
            "bedrock": "connected",
            "dynamodb_lock": "connected",
            "s3_audit_trail": "connected"
        }
    }

from fastapi.responses import PlainTextResponse
@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """
    Dummy Prometheus metrics endpoint.
    """
    return """# HELP ai_engine_requests_total Total requests
# TYPE ai_engine_requests_total counter
ai_engine_requests_total{endpoint="/v1/detect"} 42
ai_engine_requests_total{endpoint="/v1/decide"} 12
ai_engine_requests_total{endpoint="/v1/verify"} 8
# HELP ai_engine_cpu_usage CPU usage
# TYPE ai_engine_cpu_usage gauge
ai_engine_cpu_usage 0.15
"""

@app.post("/v1/detect")
def detect(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: str = Header(None, alias="Authorization"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
    idempotency_key: str = Body(...),
    dry_run_mode: bool = Body(...),
    telemetry_window: List[Dict[str, Any]] = Body(...),
    correlation_id: Optional[str] = Body(None)
):
    """
    Dummy detect endpoint. Always returns a fixed anomaly.
    """
    return {
        "anomaly_detected": True,
        "severity": 0.85,
        "anomaly_context": {
            "target_service": "order-service",
            "suspected_fault_type": "database_connection_failure",
            "system": "E-COMMERCE",
            "namespace": "production",
            "deployment": "order-service",
            "trigger_metric": "service_error_rate",
            "trigger_value": 0.15
        },
        "confidence": 0.92,
        "reasoning": "Tỷ lệ lỗi của order-service (15%) vượt ngưỡng an toàn 5% đồng thời xuất hiện lỗi NullPointerException liên tục trong stack trace.",
        "correlation_id": correlation_id or "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o"
    }

@app.post("/v1/decide")
def decide(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: str = Header(None, alias="Authorization"),
    x_correlation_id: str = Header(..., alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
    idempotency_key: str = Body(...),
    correlation_id: str = Body(...),
    anomaly_context: Dict[str, Any] = Body(...),
    dry_run_mode: bool = Body(...)
):
    """
    Dummy decide endpoint. Returns an action plan.
    """
    return {
        "matched_runbook": "DatabaseConnectionRecoveryRunbook",
        "pattern_type": "urgent",
        "action_plan": [
            {
                "step": 1,
                "action": "PATCH_MEMORY_LIMIT",
                "target": "deployment/order-service",
                "params": {
                    "namespace": "production",
                    "container": "main",
                    "memory_request_mb": 512,
                    "memory_limit_mb": 768
                }
            }
        ],
        "blast_radius_config": {
            "max_pod_impact_pct": 25,
            "circuit_breaker_error_rate": 0.20,
            "allowed_namespaces": ["production"]
        },
        "verify_policy": {
            "window_seconds": 120,
            "success_conditions": [
                "pod_ready == true",
                "restart_count_no_increase == true",
                "container_memory_usage_pct < 80"
            ]
        },
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "dry_run_mode": dry_run_mode
    }

@app.post("/v1/verify")
def verify(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: str = Header(None, alias="Authorization"),
    x_correlation_id: str = Header(..., alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
    idempotency_key: str = Body(...),
    correlation_id: str = Body(...),
    dry_run_mode: bool = Body(...),
    action_executed: Dict[str, Any] = Body(...),
    post_telemetry_window: List[Dict[str, Any]] = Body(...)
):
    """
    Dummy verify endpoint. Always returns success.
    """
    return {
        "success": True,
        "regression_detected": False,
        "next_action": "DONE"
    }

# Start server: uvicorn main:app --host 0.0.0.0 --port 8080
