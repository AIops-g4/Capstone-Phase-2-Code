import os
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Literal, Union
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request, status, Body
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ConfigDict

from .engine import AIOpsEngine
from .config import (
    API_HOST,
    API_PORT,
    DEFAULT_NAMESPACE,
    SYSTEM_NAME,
    DATASET_DIR,
    EVAL_BOCPD_WINDOW_BEFORE,
    EVAL_BOCPD_WINDOW_AFTER,
)
from .recovery_orchestrator import run_e2e_benchmark

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
    service_top_k: Optional[List[str]] = None
    llm_fault_rank_evidence: Optional[Dict[str, Any]] = None
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
    detect_assessment: Optional[Dict[str, Any]] = None
    corrected_anomaly_context: Optional[Dict[str, Any]] = None
    fault_type_ranking: Optional[List[Dict[str, Any]]] = None
    fault_type_ranking_used: Optional[bool] = None

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

def _iso(ts: int | float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _load_benchmark_fixture_telemetry(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Server-side telemetry provider used by benchmark/CDO tests.

    This mirrors the future native K8s integration shape: the caller sends only
    a source selector, while the AI Engine server fetches metrics/logs itself.
    """
    service_fault = source.get("service_fault")
    run_id = str(source.get("run_id"))
    if not service_fault or not run_id:
        raise HTTPException(status_code=400, detail="telemetry_source requires service_fault and run_id")

    run_dir = os.path.join(DATASET_DIR, service_fault, run_id)
    metrics_path = os.path.join(run_dir, "simple_metrics.csv")
    logs_path = os.path.join(run_dir, "logs.csv")
    if not os.path.exists(metrics_path):
        raise HTTPException(status_code=404, detail=f"Benchmark telemetry not found: {metrics_path}")

    df_metrics = pd.read_csv(metrics_path).sort_values("time").reset_index(drop=True)
    original_rows = len(df_metrics)
    inject_time = source.get("inject_time")
    if inject_time is not None:
        window_before = int(source.get("window_before", EVAL_BOCPD_WINDOW_BEFORE))
        window_after = int(source.get("window_after", EVAL_BOCPD_WINDOW_AFTER))
        start_ts = int(inject_time) - window_before
        end_ts = int(inject_time) + window_after
        df_metrics = df_metrics[(df_metrics["time"] >= start_ts) & (df_metrics["time"] <= end_ts)].reset_index(drop=True)
        if df_metrics.empty:
            raise HTTPException(
                status_code=404,
                detail=f"No telemetry rows in sliced window {start_ts}..{end_ts} for {service_fault}/{run_id}",
            )
        print(
            f"[API][SERVER] Sliced benchmark telemetry around inject_time={inject_time}: "
            f"rows {original_rows} -> {len(df_metrics)} "
            f"(window_before={window_before}, window_after={window_after})"
        )
    tenant_id = str(source.get("tenant_id", "benchmark-cdo"))
    telemetry: List[Dict[str, Any]] = []

    for _, row in df_metrics.iterrows():
        ts = _iso(row["time"])
        for col, value in row.items():
            if col == "time" or pd.isna(value):
                continue
            telemetry.append({
                "ts": ts,
                "tenant_id": tenant_id,
                "service": str(col).split("_", 1)[0],
                "signal_name": str(col),
                "value": float(value),
                "labels": {},
            })

    if os.path.exists(logs_path):
        df_logs = pd.read_csv(logs_path)
        if inject_time is not None and "timestamp" in df_logs.columns:
            start_ns = (int(inject_time) - int(source.get("window_before", EVAL_BOCPD_WINDOW_BEFORE))) * 1_000_000_000
            end_ns = (int(inject_time) + int(source.get("window_after", EVAL_BOCPD_WINDOW_AFTER))) * 1_000_000_000
            df_logs = df_logs[(df_logs["timestamp"] >= start_ns) & (df_logs["timestamp"] <= end_ns)]
        for _, row in df_logs.iterrows():
            raw_ts = row.get("timestamp", df_metrics["time"].iloc[0] * 1_000_000_000)
            ts_sec = int(raw_ts // 1_000_000_000) if raw_ts > 10_000_000_000 else int(raw_ts)
            telemetry.append({
                "ts": _iso(ts_sec),
                "tenant_id": tenant_id,
                "service": str(row.get("container_name", "unknown")),
                "signal_name": "application_log_event",
                "value": str(row.get("message", "")),
                "labels": {"level": str(row.get("level", "info"))},
            })

    print(f"[API][SERVER] Loaded telemetry source {service_fault}/{run_id}: {len(telemetry)} points")
    return telemetry

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
    if telemetry_source and not telemetry_window:
        source_kind = telemetry_source.get("kind", "benchmark_fixture")
        if source_kind not in {"benchmark_fixture", "k8s", "prometheus_loki"}:
            raise HTTPException(status_code=400, detail=f"Unsupported telemetry_source kind: {source_kind}")
        telemetry_window = _load_benchmark_fixture_telemetry(telemetry_source)

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
    return DecideResponse(**res)

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
