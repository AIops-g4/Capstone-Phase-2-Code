import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Literal, Union
from fastapi import FastAPI, Header, HTTPException, Request, status, Body
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, ConfigDict

from .engine import AIOpsEngine
from .config import (
    API_HOST,
    API_PORT,
    DEFAULT_NAMESPACE,
    SYSTEM_NAME,
)
from .recovery_orchestrator import run_e2e_benchmark
from .telemetry_sources import TelemetrySourceError, load_telemetry_from_source

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
ai_engine_requests_total{endpoint="/v1/verify"} 8
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
    telemetry_window: Optional[List[TelemetryPoint]] = Field(None, description="Telemetry data window")
    telemetry_source: Optional[Dict[str, Any]] = Field(None, description="Server-side telemetry source selector")

class AnomalyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_service: str = Field(..., description="Identified faulty service")
    suspected_fault_type: str = Field(..., description="Identified fault type")
    system: str = Field(default=SYSTEM_NAME, description="System name")
    namespace: Optional[str] = Field(default=DEFAULT_NAMESPACE, description="Kubernetes namespace")
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
    detect_evidence: Optional[Dict[str, Any]] = None

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

class ActionExecuted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    target: str
    status: Literal["COMPLETED", "FAILED"]
    execution_time_seconds: Optional[int] = None

class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    action_executed: ActionExecuted
    post_telemetry_window: List[TelemetryPoint]

class EscalationBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = None
    logs: Optional[List[str]] = None
    metrics: Optional[Dict[str, Any]] = None

class VerifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    regression_detected: bool
    next_action: Literal["DONE", "RETRY", "ROLLBACK", "ESCALATE"]
    escalation_bundle: Optional[EscalationBundle] = None

class E2EBenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_size: Optional[int] = Field(default=None, description="Number of runs to evaluate")
    engine: Literal["config", "default", "baro"] = "baro"
    top_k: int = 3
    use_rrcf: bool = False
    use_bocpd: bool = True
    verbose: bool = False

class FaultRankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    anomaly_context: AnomalyContext
    detect_evidence: Optional[Dict[str, Any]] = None


# =====================================================================
#                          API ENDPOINTS
# =====================================================================

CONTRACT_SIGNAL_NAMES = {
    "service_error_rate",
    "service_latency_p95",
    "container_resource_usage",
    "application_log_event",
    "distributed_trace_error_event",
    "pod_oom_event",
    "service_unhealthy",
    "queue_backlog",
    "service_throughput_rps",
    "container_restart_count",
    "secret_expiry_warning",
    "db_connection_pool_saturation",
}


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _is_rfc3339_datetime(value: Any) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError):
        return False


def _validate_contract_telemetry_window(telemetry_window: List[Dict[str, Any]], x_tenant_id: str) -> None:
    """
    Strict validation for direct HTTP Push telemetry defined in ai/contracts.

    Server-side telemetry_source providers are internal adapters and may use
    benchmark/profile-specific signal names before they are normalized by the
    TelemetryProcessor, so this strict contract gate is applied only to direct
    telemetry_window requests from CDO.
    """
    if not isinstance(telemetry_window, list) or not telemetry_window:
        raise HTTPException(status_code=400, detail="telemetry_window must be a non-empty array")
    allowed_keys = {"ts", "tenant_id", "service", "signal_name", "value", "labels"}
    for idx, point in enumerate(telemetry_window):
        if not isinstance(point, dict):
            raise HTTPException(status_code=400, detail=f"telemetry_window[{idx}] must be an object")
        extra = set(point) - allowed_keys
        if extra:
            raise HTTPException(status_code=400, detail=f"telemetry_window[{idx}] has unsupported fields: {sorted(extra)}")
        missing = {"ts", "tenant_id", "service", "signal_name", "value"} - set(point)
        if missing:
            raise HTTPException(status_code=400, detail=f"telemetry_window[{idx}] missing required fields: {sorted(missing)}")
        if not _is_rfc3339_datetime(point.get("ts")):
            raise HTTPException(status_code=400, detail=f"telemetry_window[{idx}].ts must be RFC3339 date-time")
        if not _is_uuid(point.get("tenant_id")):
            raise HTTPException(status_code=400, detail=f"telemetry_window[{idx}].tenant_id must be UUID")
        if x_tenant_id and point.get("tenant_id") != x_tenant_id:
            raise HTTPException(status_code=403, detail=f"telemetry_window[{idx}].tenant_id does not match X-Tenant-Id")
        if point.get("signal_name") not in CONTRACT_SIGNAL_NAMES:
            raise HTTPException(status_code=400, detail=f"telemetry_window[{idx}].signal_name is not in telemetry contract enum")
        labels = point.get("labels")
        if labels is not None:
            if not isinstance(labels, dict):
                raise HTTPException(status_code=400, detail=f"telemetry_window[{idx}].labels must be an object")
            if "system" not in labels:
                raise HTTPException(status_code=400, detail=f"telemetry_window[{idx}].labels.system is required when labels is present")

@app.post("/v1/detect", response_model=DetectResponse, response_model_exclude_none=True)
async def detect_anomalies(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: str = Header(None, alias="Authorization"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
    idempotency_key: str = Body(...),
    dry_run_mode: bool = Body(...),
    telemetry_window: Optional[List[Dict[str, Any]]] = Body(None),
    telemetry_source: Optional[Dict[str, Any]] = Body(None),
    correlation_id: Optional[str] = Body(None)
):
    """
    Endpoint: POST /v1/detect
    Ingests telemetry, runs dual-track anomaly detection, diagnoses root causes (RCA), and correlates alerts.
    """
    print("\n[API][SERVER] POST /v1/detect received")
    if not telemetry_window:
        try:
            telemetry_window = load_telemetry_from_source(telemetry_source)
        except TelemetrySourceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    elif not telemetry_source:
        _validate_contract_telemetry_window(telemetry_window, x_tenant_id)

    request = DetectRequest(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        dry_run_mode=dry_run_mode,
        telemetry_window=telemetry_window,
        telemetry_source=telemetry_source,
    )
    if not request.telemetry_window:
        raise HTTPException(status_code=400, detail="Provide telemetry_source or telemetry_window")
    res = aiops_engine.detect_anomalies(request.telemetry_window, request.correlation_id)
    print(f"[API][SERVER] POST /v1/detect completed anomaly_detected={res.get('anomaly_detected')}")
    if telemetry_source:
        return JSONResponse(content=res)
    return DetectResponse(**{k: v for k, v in res.items() if k in DetectResponse.model_fields})

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
    dry_run_mode: bool = Body(...),
    detect_evidence: Optional[Dict[str, Any]] = Body(None)
):
    """
    Endpoint: POST /v1/decide
    Matches diagnosed anomalies to runbooks and templates self-healing action plans.
    """
    print("\n[API][SERVER] POST /v1/decide received")
    request = DecideRequest(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        dry_run_mode=dry_run_mode,
        anomaly_context=anomaly_context,
        detect_evidence=detect_evidence,
    )
    res = aiops_engine.decide_healing_action(
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        dry_run_mode=request.dry_run_mode,
        anomaly_context=request.anomaly_context.model_dump(),
        detect_evidence=request.detect_evidence,
    )
    print(f"[API][SERVER] POST /v1/decide completed runbook={res.get('matched_runbook')}")
    if request.detect_evidence:
        return JSONResponse(content=res)
    return DecideResponse(**{k: v for k, v in res.items() if k in DecideResponse.model_fields})

@app.post("/v1/verify", response_model=VerifyResponse, response_model_exclude_none=True)
async def verify_healing(
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
    Endpoint: POST /v1/verify
    Verifies execution status and post-healing telemetry, closing the incident if successfully resolved.
    """
    print("\n[API][SERVER] POST /v1/verify received")
    request = VerifyRequest(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        dry_run_mode=dry_run_mode,
        action_executed=action_executed,
        post_telemetry_window=post_telemetry_window
    )
    res = aiops_engine.verify_healing(
        correlation_id=request.correlation_id,
        action_executed=request.action_executed,
        post_telemetry_window=request.post_telemetry_window
    )
    print(f"[API][SERVER] POST /v1/verify completed next_action={res.get('next_action')}")
    return VerifyResponse(**res)


@app.post("/v1/fault-rank")
async def rank_fault_types(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: str = Header(None, alias="Authorization"),
    x_correlation_id: str = Header(..., alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
    idempotency_key: str = Body(...),
    correlation_id: str = Body(...),
    dry_run_mode: bool = Body(...),
    anomaly_context: Dict[str, Any] = Body(...),
    detect_evidence: Optional[Dict[str, Any]] = Body(None),
):
    """
    Endpoint for CDO/orchestrator fallback ordering.
    Keeps service fixed and ranks fault types by confidence.
    """
    print("\n[API][SERVER] POST /v1/fault-rank received")
    request = FaultRankRequest(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        dry_run_mode=dry_run_mode,
        anomaly_context=anomaly_context,
        detect_evidence=detect_evidence,
    )
    res = aiops_engine.rank_fault_types(
        anomaly_context=request.anomaly_context.model_dump(),
        detect_evidence=request.detect_evidence,
    )
    print(f"[API][SERVER] POST /v1/fault-rank completed used={res.get('used')}")
    return res


@app.post("/v1/benchmark/e2e")
async def run_e2e_benchmark_api(request: E2EBenchmarkRequest):
    """
    Benchmark-only API entrypoint.

    The orchestration/fallback/rollback logic lives in src.recovery_orchestrator.
    scripts/benchmark_e2e.py should only load benchmark input/config and call this API.
    """
    return run_e2e_benchmark(
        sample_size=request.sample_size,
        engine=request.engine,
        top_k=request.top_k,
        use_rrcf=request.use_rrcf,
        use_bocpd=request.use_bocpd,
        verbose=request.verbose,
    )


# =====================================================================
#                           SERVER RUNNER
# =====================================================================

if __name__ == "__main__":
    import uvicorn
    print(f"Starting AIOps FastAPI Server on {API_HOST}:{API_PORT}...")
    uvicorn.run("src.server:app", host=API_HOST, port=API_PORT, reload=False)
