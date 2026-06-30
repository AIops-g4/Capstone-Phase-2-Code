from typing import Any, Dict, List, Optional
from fastapi import Body, FastAPI, Header
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ConfigDict

from .engine import AIOpsEngine
from .config import API_HOST, API_PORT

# Initialize FastAPI App
app = FastAPI(
    title="AIOps AI Engine Service",
    description="Detect-only anomaly detection and root cause analysis benchmark service.",
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
    Ingests telemetry, runs BOCPD anomaly detection, and diagnoses root causes with BARO RCA.
    """
    request = DetectRequest(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        dry_run_mode=dry_run_mode,
        telemetry_window=telemetry_window
    )
    res = aiops_engine.detect_anomalies(request.telemetry_window, request.correlation_id)
    return DetectResponse(**res)



# =====================================================================
#                           SERVER RUNNER
# =====================================================================

if __name__ == "__main__":
    import uvicorn
    print(f"Starting AIOps FastAPI Server on {API_HOST}:{API_PORT}...")
    uvicorn.run("src.server:app", host=API_HOST, port=API_PORT, reload=False)
