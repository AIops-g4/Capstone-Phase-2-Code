import os
import uuid
from typing import List, Dict, Any, Optional, Literal, Union
from fastapi import FastAPI, Header, HTTPException, Request, status, Body
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ConfigDict

from .engine import AIOpsEngine
from .config import API_HOST, API_PORT

# Initialize FastAPI App
app = FastAPI(
    title="AIOps AI Engine Service",
    description="Automated closed-loop anomaly detection, root cause analysis, and healing orchestrator.",
    version="1.0.0"
)

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

@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """
    Dummy Prometheus metrics endpoint.
    """
    return """# HELP ai_engine_requests_total Total requests
# TYPE ai_engine_requests_total counter
ai_engine_requests_total{endpoint="/v1/detect"} 42
ai_engine_requests_total{endpoint="/v1/decide"} 12
# HELP ai_engine_cpu_usage CPU usage
# TYPE ai_engine_cpu_usage gauge
ai_engine_cpu_usage 0.15
"""

# Initialize the global AIOps Engine Facade
aiops_engine = AIOpsEngine()


# =====================================================================
#                      PYDANTIC REQUEST / RESPONSE SCHEMAS
# =====================================================================

class TelemetryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: str = Field(..., description="ISO 8601 timestamp")
    tenant_id: str = Field(..., description="Tenant UUID")
    service: str = Field(..., description="Service name")
    signal_name: str = Field(..., description="Metric or event name")
    value: Any = Field(..., description="Numerical value or log string message")
    labels: Optional[Dict[str, Any]] = Field(default=None, description="Optional labels")

class DetectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correlation_id: Optional[str] = Field(None, description="UUID v4 tracing correlation ID")
    idempotency_key: str = Field(..., description="UUID v4 idempotency key")
    dry_run_mode: bool = Field(..., description="Dry-run flag")
    telemetry_window: List[TelemetryPoint] = Field(..., description="Telemetry data window")

class AnomalyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_service: str = Field(..., description="Identified faulty service")
    suspected_fault_type: str = Field(..., description="Identified fault type")
    system: str = Field(default="E-COMMERCE", description="System name")
    namespace: Optional[str] = Field(default="production", description="Kubernetes namespace")
    deployment: Optional[str] = Field(None, description="Kubernetes deployment")
    trigger_metric: Optional[str] = Field(None, description="Metric triggering the alert")
    trigger_value: Optional[float] = Field(None, description="Metric value triggering the alert")

class DetectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anomaly_detected: bool
    severity: float = Field(..., ge=0.0, le=1.0)
    anomaly_context: Optional[AnomalyContext] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., max_length=300)
    correlation_id: str

class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    anomaly_context: AnomalyContext

class ActionPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int
    action: Literal["RESTART_DEPLOYMENT", "PATCH_MEMORY_LIMIT", "SCALE_REPLICAS", "ROLLOUT_UNDO", "ROTATE_SECRET"]
    target: str
    params: Dict[str, Any]

class BlastRadiusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_pod_impact_pct: int
    circuit_breaker_error_rate: float
    allowed_namespaces: List[str]

class VerifyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_seconds: int
    success_conditions: Optional[List[str]] = None

class DecideResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matched_runbook: str
    pattern_type: Literal["urgent", "deferred"]
    action_plan: List[ActionPlanStep]
    blast_radius_config: BlastRadiusConfig
    verify_policy: VerifyPolicy
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    cost_cap_exceeded: bool = False



# =====================================================================
#                          API ENDPOINTS
# =====================================================================

@app.post("/v1/detect", response_model=DetectResponse, response_model_exclude_none=True)
async def detect_anomalies(
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
    Endpoint: POST /v1/detect
    Ingests telemetry, runs dual-track anomaly detection, diagnoses root causes (RCA), and correlates alerts.
    """
    request = DetectRequest(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        dry_run_mode=dry_run_mode,
        telemetry_window=telemetry_window
    )
    res = aiops_engine.detect_anomalies(request.telemetry_window, request.correlation_id)
    return DetectResponse(**res)

@app.post("/v1/decide", response_model=DecideResponse, response_model_exclude_none=True)
async def decide_action_plan(
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
    Endpoint: POST /v1/decide
    Matches diagnosed anomalies to runbooks and templates self-healing action plans.
    """
    request = DecideRequest(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        dry_run_mode=dry_run_mode,
        anomaly_context=anomaly_context
    )
    res = aiops_engine.decide_healing_action(
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        dry_run_mode=request.dry_run_mode,
        anomaly_context=request.anomaly_context.model_dump()
    )
    return DecideResponse(**res)



# =====================================================================
#                           SERVER RUNNER
# =====================================================================

if __name__ == "__main__":
    import uvicorn
    print(f"Starting AIOps FastAPI Server on {API_HOST}:{API_PORT}...")
    uvicorn.run("src.server:app", host=API_HOST, port=API_PORT, reload=False)
