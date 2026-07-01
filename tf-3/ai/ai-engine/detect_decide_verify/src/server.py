import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, Literal, Union
from fastapi import FastAPI, Header, HTTPException, Request, status, Body
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

from .engine import AIOpsEngine
from .config import (
    API_HOST,
    API_PORT,
    CDO_PUSH_TELEMETRY_SOURCE_KIND,
    DEFAULT_NAMESPACE,
    SYSTEM_NAME,
    TELEMETRY_RUNTIME_MODE,
    TELEMETRY_SIGNAL_NAMES,
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

# Contract-defined signal_name enum loaded from TELEMETRY_SIGNAL_NAMES_PATH (adr/telemetry_signal_names.json).
# The path is configured in .env (TELEMETRY_SIGNAL_NAMES_PATH).
_CONTRACT_SIGNAL_NAMES: set[str] = set(TELEMETRY_SIGNAL_NAMES)


# Signal names whose value must be a log/event string (per contracts/telemetry-contract.md §4)
_LOG_SIGNAL_NAMES: set[str] = {"application_log_event", "distributed_trace_error_event"}


class TelemetryPoint(BaseModel):
    """
    Single telemetry data point per contracts/telemetry-contract.md.
    extra='forbid' enforces additionalProperties: false.

    value type rules (§3 JSON Schema – "type": ["number", "string"]):
      - signal_name in LOG_SIGNAL_NAMES → value must be a string (log / event message)
      - all other signal_names           → value must be a number (float metric)
    """
    model_config = ConfigDict(extra="forbid")
    ts: str = Field(..., description="ISO 8601 / RFC3339 timestamp")
    tenant_id: str = Field(..., description="Tenant UUID v4")
    service: str = Field(..., description="Service name")
    signal_name: str = Field(..., description="Signal name from telemetry contract enum")
    value: Union[float, str] = Field(
        ...,
        description=(
            "Metric value (number) for numeric signals, "
            "or log/event text (string) for application_log_event / distributed_trace_error_event"
        ),
    )
    labels: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional labels dict; labels.system is required when present",
    )

    @field_validator("ts")
    @classmethod
    def ts_must_be_rfc3339(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ValueError("ts must be RFC3339 / ISO 8601 date-time")
        return v

    @field_validator("tenant_id")
    @classmethod
    def tenant_id_must_be_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(str(v))
        except (TypeError, ValueError):
            raise ValueError("tenant_id must be UUID v4")
        return v

    @field_validator("signal_name")
    @classmethod
    def signal_name_must_be_in_contract(cls, v: str) -> str:
        if v not in _CONTRACT_SIGNAL_NAMES:
            raise ValueError(
                f"signal_name '{v}' is not in telemetry contract enum. "
                f"Allowed: {sorted(_CONTRACT_SIGNAL_NAMES)}"
            )
        return v

    @field_validator("labels")
    @classmethod
    def labels_system_required(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is not None and "system" not in v:
            raise ValueError("labels.system is required when labels is present")
        return v

    @model_validator(mode="after")
    def value_type_must_match_signal(self) -> "TelemetryPoint":
        """
        Cross-field validation (contracts/telemetry-contract.md §3 and §4):
          - Log/event signals (application_log_event, distributed_trace_error_event)
            must carry a string value (the log message / trace event text).
          - All metric signals must carry a numeric (float) value.
        """
        if self.signal_name in _LOG_SIGNAL_NAMES:
            if not isinstance(self.value, str):
                raise ValueError(
                    f"signal_name='{self.signal_name}' requires value to be a string "
                    f"(log or event message), got {type(self.value).__name__}"
                )
        else:
            if not isinstance(self.value, (int, float)):
                raise ValueError(
                    f"signal_name='{self.signal_name}' requires value to be a number "
                    f"(metric measurement), got {type(self.value).__name__}"
                )
        return self

class DetectRequest(BaseModel):
    """
    Request body for POST /v1/detect per contracts/ai-api-contract.md §3.1.
    additionalProperties: false enforced by extra='forbid'.
    telemetry_source is a server-side internal extension for bench/production mode only;
    it is not part of the CDO-facing contract and must not be sent by CDO in push mode.
    """
    model_config = ConfigDict(extra="forbid")
    correlation_id: Optional[str] = Field(None, description="UUID v4 tracing correlation ID (optional)")
    idempotency_key: str = Field(..., description="UUID v4 idempotency key")
    dry_run_mode: bool = Field(..., description="Dry-run flag")
    telemetry_window: Optional[List[TelemetryPoint]] = Field(None, description="Telemetry data window per telemetry-contract.md")
    telemetry_source: Optional[Dict[str, Any]] = Field(None, description="[Internal] Server-side telemetry source selector for bench/production mode")

class AnomalyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_service: Union[str, List[str]] = Field(..., description="Identified faulty service")
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


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _parse_header_bool(value: str, header_name: str) -> bool:
    lowered = str(value).strip().lower()
    if lowered not in {"true", "false"}:
        raise HTTPException(status_code=400, detail=f"{header_name} must be 'true' or 'false'")
    return lowered == "true"


def _validate_detect_contract_headers_and_body(
    *,
    x_tenant_id: str,
    x_correlation_id: Optional[str],
    idempotency_key_header: str,
    x_dry_run_mode: str,
    idempotency_key: str,
    dry_run_mode: bool,
    correlation_id: Optional[str],
) -> None:
    if not _is_uuid(x_tenant_id):
        raise HTTPException(status_code=400, detail="X-Tenant-Id must be UUID")
    if x_correlation_id and not _is_uuid(x_correlation_id):
        raise HTTPException(status_code=400, detail="X-Correlation-Id must be UUID when provided")
    if not _is_uuid(idempotency_key_header):
        raise HTTPException(status_code=400, detail="Idempotency-Key header must be UUID")
    if not _is_uuid(idempotency_key):
        raise HTTPException(status_code=400, detail="idempotency_key body must be UUID")
    if idempotency_key_header != idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header must match idempotency_key body")
    dry_run_header = _parse_header_bool(x_dry_run_mode, "X-Dry-Run-Mode")
    if dry_run_header != dry_run_mode:
        raise HTTPException(status_code=400, detail="X-Dry-Run-Mode header must match dry_run_mode body")
    if x_correlation_id and correlation_id and x_correlation_id != correlation_id:
        raise HTTPException(status_code=400, detail="X-Correlation-Id header must match correlation_id body when both are provided")


def _validate_header_and_body_uuid_cross_check(
    *,
    x_tenant_id: str,
    x_correlation_id: str,
    idempotency_key_header: str,
    x_dry_run_mode: str,
    idempotency_key: str,
    dry_run_mode: bool,
    correlation_id: str,
) -> None:
    if not _is_uuid(x_tenant_id):
        raise HTTPException(status_code=400, detail="X-Tenant-Id must be UUID")
    if not _is_uuid(x_correlation_id):
        raise HTTPException(status_code=400, detail="X-Correlation-Id must be UUID")
    if not _is_uuid(idempotency_key_header):
        raise HTTPException(status_code=400, detail="Idempotency-Key header must be UUID")
    if not _is_uuid(idempotency_key):
        raise HTTPException(status_code=400, detail="idempotency_key body must be UUID")
    if idempotency_key_header != idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header must match idempotency_key body")
    dry_run_header = _parse_header_bool(x_dry_run_mode, "X-Dry-Run-Mode")
    if dry_run_header != dry_run_mode:
        raise HTTPException(status_code=400, detail="X-Dry-Run-Mode header must match dry_run_mode body")
    if x_correlation_id != correlation_id:
        raise HTTPException(status_code=400, detail="X-Correlation-Id header must match correlation_id body")


@app.post("/v1/detect", response_model=DetectResponse, response_model_exclude_none=True)
async def detect_anomalies(
    body: DetectRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_correlation_id: Optional[str] = Header(None, alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
):
    """
    POST /v1/detect — per contracts/ai-api-contract.md §3.1.

    CDO pushes telemetry_window directly. telemetry_source is an internal server
    extension for bench/production mode only and must not be sent in CDO push mode.
    FastAPI automatically validates the request body against DetectRequest Pydantic schema
    (additionalProperties: false, required fields, TelemetryPoint enum constraints).
    """
    print("\n[API][SERVER] POST /v1/detect received")
    _validate_detect_contract_headers_and_body(
        x_tenant_id=x_tenant_id,
        x_correlation_id=x_correlation_id,
        idempotency_key_header=idempotency_key_header,
        x_dry_run_mode=x_dry_run_mode,
        idempotency_key=body.idempotency_key,
        dry_run_mode=body.dry_run_mode,
        correlation_id=body.correlation_id,
    )

    source_kind = (body.telemetry_source or {}).get("kind") if body.telemetry_source else None
    is_cdo_push_mode = TELEMETRY_RUNTIME_MODE == "cdo_push" or source_kind == CDO_PUSH_TELEMETRY_SOURCE_KIND

    if is_cdo_push_mode and not body.telemetry_window:
        raise HTTPException(status_code=400, detail="CDO push mode requires telemetry_window in request body")
    if is_cdo_push_mode and body.telemetry_source:
        raise HTTPException(status_code=400, detail="CDO push mode does not accept telemetry_source; push telemetry_window directly")

    # Cross-check tenant_id in each telemetry point against X-Tenant-Id header.
    if body.telemetry_window and not body.telemetry_source:
        for idx, point in enumerate(body.telemetry_window):
            if point.tenant_id != x_tenant_id:
                raise HTTPException(
                    status_code=403,
                    detail=f"telemetry_window[{idx}].tenant_id does not match X-Tenant-Id",
                )

    telemetry_window = body.telemetry_window
    if not telemetry_window:
        try:
            telemetry_window = load_telemetry_from_source(body.telemetry_source)
        except TelemetrySourceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    if not telemetry_window:
        raise HTTPException(status_code=400, detail="Provide telemetry_source or telemetry_window")

    res = aiops_engine.detect_anomalies(telemetry_window, body.correlation_id)
    print(f"[API][SERVER] POST /v1/detect completed anomaly_detected={res.get('anomaly_detected')}")
    if body.telemetry_source:
        return JSONResponse(content=res)
    return DetectResponse(**{k: v for k, v in res.items() if k in DetectResponse.model_fields})

@app.post("/v1/decide", response_model=DecideResponse, response_model_exclude_none=True)
async def decide_action_plan(
    body: DecideRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_correlation_id: str = Header(..., alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
):
    """
    POST /v1/decide — per contracts/ai-api-contract.md §3.2.

    FastAPI automatically validates the request body against DecideRequest Pydantic schema
    (additionalProperties: false, required fields, AnomalyContext constraints).
    """
    print("\n[API][SERVER] POST /v1/decide received")
    _validate_header_and_body_uuid_cross_check(
        x_tenant_id=x_tenant_id,
        x_correlation_id=x_correlation_id,
        idempotency_key_header=idempotency_key_header,
        x_dry_run_mode=x_dry_run_mode,
        idempotency_key=body.idempotency_key,
        dry_run_mode=body.dry_run_mode,
        correlation_id=body.correlation_id,
    )
    res = aiops_engine.decide_healing_action(
        correlation_id=body.correlation_id,
        idempotency_key=body.idempotency_key,
        dry_run_mode=body.dry_run_mode,
        anomaly_context=body.anomaly_context.model_dump(),
        detect_evidence=body.detect_evidence,
        tenant_id=x_tenant_id,
    )
    print(f"[API][SERVER] POST /v1/decide completed runbook={res.get('matched_runbook')}")
    if body.detect_evidence:
        return JSONResponse(content=res)
    return DecideResponse(**{k: v for k, v in res.items() if k in DecideResponse.model_fields})

@app.post("/v1/verify", response_model=VerifyResponse, response_model_exclude_none=True)
async def verify_healing(
    body: VerifyRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_correlation_id: str = Header(..., alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
):
    """
    POST /v1/verify — per contracts/ai-api-contract.md §3.3.

    FastAPI automatically validates the request body against VerifyRequest Pydantic schema
    (additionalProperties: false, required fields, TelemetryPoint constraints for
    post_telemetry_window).
    """
    print("\n[API][SERVER] POST /v1/verify received")
    _validate_header_and_body_uuid_cross_check(
        x_tenant_id=x_tenant_id,
        x_correlation_id=x_correlation_id,
        idempotency_key_header=idempotency_key_header,
        x_dry_run_mode=x_dry_run_mode,
        idempotency_key=body.idempotency_key,
        dry_run_mode=body.dry_run_mode,
        correlation_id=body.correlation_id,
    )
    for idx, point in enumerate(body.post_telemetry_window):
        if point.tenant_id != x_tenant_id:
            raise HTTPException(
                status_code=403,
                detail=f"post_telemetry_window[{idx}].tenant_id does not match X-Tenant-Id",
            )
    res = aiops_engine.verify_healing(
        correlation_id=body.correlation_id,
        action_executed=body.action_executed,
        post_telemetry_window=body.post_telemetry_window,
    )
    print(f"[API][SERVER] POST /v1/verify completed next_action={res.get('next_action')}")
    return VerifyResponse(**res)


@app.post("/v1/fault-rank")
async def rank_fault_types(
    body: FaultRankRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_correlation_id: str = Header(..., alias="X-Correlation-Id"),
    idempotency_key_header: str = Header(..., alias="Idempotency-Key"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode"),
):
    """
    POST /v1/fault-rank — CDO/orchestrator fallback ordering.
    Keeps service fixed and ranks fault types by confidence.
    FastAPI validates body against FaultRankRequest Pydantic schema.
    """
    print("\n[API][SERVER] POST /v1/fault-rank received")
    _validate_header_and_body_uuid_cross_check(
        x_tenant_id=x_tenant_id,
        x_correlation_id=x_correlation_id,
        idempotency_key_header=idempotency_key_header,
        x_dry_run_mode=x_dry_run_mode,
        idempotency_key=body.idempotency_key,
        dry_run_mode=body.dry_run_mode,
        correlation_id=body.correlation_id,
    )
    res = aiops_engine.rank_fault_types(
        anomaly_context=body.anomaly_context.model_dump(),
        detect_evidence=body.detect_evidence,
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
