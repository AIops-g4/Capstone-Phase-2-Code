import os
import uuid
import json
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .anomaly_detector import run_metric_anomaly_detection, IsolationForestDetector, EWMAAnomalyDetector
from .log_parser import Drain3LogParser
from .correlation_analyzer import CorrelationAnalyzer
from .self_healer import SelfHealer

app = FastAPI(
    title="AIOps AI Engine Service",
    description="Generic Multi-Tenant Self-Heal Platform AI Engine complying with TF-3 Contracts.",
    version="1.0.0"
)

from .config import RUNBOOKS_PATH

# Initialize paths and modules

healer = SelfHealer(RUNBOOKS_PATH)
log_parser = Drain3LogParser(service_aware=True)
correlation_analyzer = CorrelationAnalyzer(correlation_threshold=0.5)

# --- Pydantic Models for Schema Validation ---

# 1. Telemetry Point Schema (telemetry-contract)
class TelemetryPoint(BaseModel):
    ts: str = Field(..., description="ISO 8601 timestamp")
    tenant_id: str = Field(..., description="Tenant UUID")
    service: str = Field(..., description="Service name")
    signal_name: str = Field(..., description="Metric or event name")
    value: Any = Field(..., description="Numerical value or log string message")
    labels: Optional[Dict[str, Any]] = Field(default=None, description="Optional labels")

# 2. Detect API Schemas
class DetectRequest(BaseModel):
    correlation_id: Optional[str] = Field(None, description="UUID v4 tracing correlation ID")
    idempotency_key: str = Field(..., description="UUID v4 idempotency key")
    dry_run_mode: bool = Field(..., description="Dry-run flag")
    telemetry_window: List[TelemetryPoint] = Field(..., description="Telemetry data window")

class AnomalyContext(BaseModel):
    target_service: str = Field(..., description="Identified faulty service")
    suspected_fault_type: str = Field(..., description="Identified fault type")
    system: str = Field(default="E-COMMERCE", description="System name")
    namespace: Optional[str] = Field(default="production", description="Kubernetes namespace")
    deployment: Optional[str] = Field(None, description="Kubernetes deployment")
    trigger_metric: Optional[str] = Field(None, description="Metric triggering the alert")
    trigger_value: Optional[float] = Field(None, description="Metric value triggering the alert")

class DetectResponse(BaseModel):
    anomaly_detected: bool
    severity: float = Field(..., ge=0.0, le=1.0)
    anomaly_context: Optional[AnomalyContext] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., max_length=300)
    correlation_id: str

# 3. Decide API Schemas
class DecideRequest(BaseModel):
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    anomaly_context: AnomalyContext

class ActionPlanStep(BaseModel):
    step: int
    action: str = Field(..., description="RESTART_DEPLOYMENT, PATCH_MEMORY_LIMIT, SCALE_REPLICAS, etc.")
    target: str
    params: Dict[str, Any]

class BlastRadiusConfig(BaseModel):
    max_pod_impact_pct: int
    circuit_breaker_error_rate: float
    allowed_namespaces: List[str]

class VerifyPolicy(BaseModel):
    window_seconds: int
    success_conditions: Optional[List[str]] = None

class DecideResponse(BaseModel):
    matched_runbook: str
    pattern_type: str = Field(..., description="urgent or deferred")
    action_plan: List[ActionPlanStep]
    blast_radius_config: BlastRadiusConfig
    verify_policy: VerifyPolicy
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    cost_cap_exceeded: bool = False

# 4. Verify API Schemas
class ActionExecuted(BaseModel):
    action: str
    target: str
    status: str = Field(..., description="COMPLETED or FAILED")
    execution_time_seconds: Optional[int] = None

class VerifyRequest(BaseModel):
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    action_executed: ActionExecuted
    post_telemetry_window: List[TelemetryPoint]

class EscalationBundle(BaseModel):
    reason: Optional[str] = None
    logs: Optional[List[str]] = None
    metrics: Optional[Dict[str, Any]] = None

class VerifyResponse(BaseModel):
    success: bool
    regression_detected: bool
    next_action: str = Field(..., description="DONE, RETRY, ROLLBACK, or ESCALATE")
    escalation_bundle: Optional[EscalationBundle] = None


# --- Endpoints ---

@app.post("/v1/detect", response_model=DetectResponse)
async def detect_anomalies(request: DetectRequest):
    """
    Endpoint: POST /v1/detect
    Parses incoming telemetry window (both metrics and logs), runs anomaly detection,
    correlates metrics with logs using a Pearson correlation matrix, and returns anomalies.
    """
    corr_id = request.correlation_id or str(uuid.uuid4())
    
    # 1. Reconstruct metrics and logs from the telemetry window
    metrics_records = {}
    log_messages = []
    
    for point in request.telemetry_window:
        ts_sec = int(pd.to_datetime(point.ts).timestamp())
        
        # Check if it is a log event
        if point.signal_name == "application_log_event":
            log_messages.append({
                "timestamp": ts_sec * 1000000000,  # Convert to nanoseconds
                "container_name": point.service,
                "message": str(point.value),
                "level": point.labels.get("level", "info") if point.labels else "info"
            })
        else:
            # It's a metric point
            if ts_sec not in metrics_records:
                metrics_records[ts_sec] = {"time": ts_sec}
            
            # The column name in simple_metrics is typically <service>_<metric_name>
            # If the signal_name already has service prefix, use it, otherwise join
            col_name = point.signal_name
            if not any(point.signal_name.startswith(s) for s in ["checkout", "currency", "email", "product", "recommendation"]):
                col_name = f"{point.service}_{point.signal_name}"
                
            metrics_records[ts_sec][col_name] = float(point.value)
            
    if not metrics_records:
        return DetectResponse(
            anomaly_detected=False,
            severity=0.0,
            confidence=1.0,
            reasoning="No metrics data found in telemetry window.",
            correlation_id=corr_id
        )
        
    # Create DataFrames
    df_metrics = pd.DataFrame(list(metrics_records.values())).sort_values("time").reset_index(drop=True)
    df_logs = pd.DataFrame(log_messages)
    
    # Ensure time starts and ends correctly
    time_start = int(df_metrics["time"].min())
    time_end = int(df_metrics["time"].max())
    
    # Parse logs using Drain3
    df_log_ts, temp_info = log_parser.parse_logs(df_logs, time_start, time_end)
    
    # Fill missing values in metrics (forward fill then zero fill)
    df_metrics = df_metrics.ffill().fillna(0)
    
    # 2. Run Anomaly Detection
    # For a real-time window, we can fit our Isolation Forest on the first 80% of the data
    # (assuming it represents baseline) and predict on the rest.
    baseline_len = max(10, int(len(df_metrics) * 0.8))
    detection_results = run_metric_anomaly_detection(df_metrics, baseline_len)
    
    mif_anoms = detection_results["multivariate"]["anomalies"]
    mif_scores = detection_results["multivariate"]["scores"]
    
    # Find if there is an anomaly in the last 10 seconds of the window
    anomaly_detected = False
    anomaly_idx = -1
    
    # Check multivariate anomalies in the last 10 rows
    check_window = 10
    start_check = max(0, len(df_metrics) - check_window)
    for i in range(start_check, len(df_metrics)):
        if mif_anoms[i]:
            anomaly_detected = True
            anomaly_idx = i
            break
            
    # Also check if any key service error or latency has EWMA anomalies in the last 10 rows
    for col, results in detection_results["ewma"].items():
        ewma_anoms = results["anomalies"]
        for i in range(start_check, len(df_metrics)):
            if ewma_anoms[i]:
                anomaly_detected = True
                if anomaly_idx == -1:
                    anomaly_idx = i
                break
                
    if not anomaly_detected:
        return DetectResponse(
            anomaly_detected=False,
            severity=0.0,
            confidence=0.90,
            reasoning="No anomalies detected in the current telemetry window.",
            correlation_id=corr_id
        )
        
    # 3. Anomaly detected! Run correlation analysis to localize root cause
    if anomaly_idx == -1:
        anomaly_idx = len(df_metrics) - 1
        
    target_service, suspected_fault_type, reasoning, confidence = correlation_analyzer.analyze(
        df_metrics=df_metrics,
        df_logs=df_log_ts,
        template_info=temp_info,
        anomaly_idx=anomaly_idx,
        window_size=120
    )
    
    # Compute severity based on anomaly score or metric deviation
    raw_severity = mif_scores[anomaly_idx]
    # Map score to [0.0, 1.0]
    severity = float(np.clip(abs(raw_severity) * 2.0, 0.4, 0.95))
    
    # Extract trigger metric details
    trigger_metric = None
    trigger_val = None
    
    # Look for the metric that deviates the most of target_service
    max_dev = 0.0
    for col in df_metrics.columns:
        if col.startswith(target_service) and col != "time":
            baseline_mean = df_metrics[col].iloc[:baseline_len].mean()
            baseline_std = df_metrics[col].iloc[:baseline_len].std()
            curr_val = df_metrics[col].iloc[anomaly_idx]
            if baseline_std > 0:
                dev = abs(curr_val - baseline_mean) / baseline_std
                if dev > max_dev:
                    max_dev = dev
                    trigger_metric = col
                    trigger_val = float(curr_val)
                    
    context = AnomalyContext(
        target_service=target_service,
        suspected_fault_type=suspected_fault_type,
        system="E-COMMERCE",
        namespace="production",
        deployment=target_service,
        trigger_metric=trigger_metric,
        trigger_value=trigger_val
    )
    
    return DetectResponse(
        anomaly_detected=True,
        severity=severity,
        anomaly_context=context,
        confidence=confidence,
        reasoning=reasoning,
        correlation_id=corr_id
    )

@app.post("/v1/decide", response_model=DecideResponse)
async def decide_action_plan(request: DecideRequest):
    """
    Endpoint: POST /v1/decide
    Determines the self-healing action plan based on the anomaly context.
    """
    ctx = request.anomaly_context
    target_service = ctx.target_service
    suspected_fault_type = ctx.suspected_fault_type
    
    # Run the self-healing decision engine
    decision = healer.decide(target_service, suspected_fault_type)
    
    return DecideResponse(
        matched_runbook=decision["matched_runbook"],
        pattern_type=decision["pattern_type"],
        action_plan=decision["action_plan"],
        blast_radius_config=decision["blast_radius_config"],
        verify_policy=decision["verify_policy"],
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        dry_run_mode=request.dry_run_mode,
        cost_cap_exceeded=False
    )

@app.post("/v1/verify", response_model=VerifyResponse)
async def verify_healing(request: VerifyRequest):
    """
    Endpoint: POST /v1/verify
    Analyzes post-healing telemetry window to check if healing succeeded.
    """
    action = request.action_executed
    
    # If the action failed from CDO side, we escalate
    if action.status == "FAILED":
        return VerifyResponse(
            success=False,
            regression_detected=False,
            next_action="RETRY",
            escalation_bundle=EscalationBundle(
                reason=f"Healing action '{action.action}' on '{action.target}' failed to execute on CDO executor."
            )
        )
        
    # Analyze post-healing telemetry window
    # We look at the metrics to see if the values have returned to normal
    # For example, if there is no error rate or latency spike anymore.
    success = True
    regression_detected = False
    reasons = []
    
    # Map target deployment to service name
    target_service = action.target.split("/")[-1]
    
    # Filter telemetry for target service
    service_points = [p for p in request.post_telemetry_window if p.service == target_service]
    
    for p in service_points:
        if "error" in p.signal_name and float(p.value) > 0.05:
            success = False
            reasons.append(f"High error rate detected: {p.signal_name} = {p.value}")
        if "latency" in p.signal_name and float(p.value) > 0.5:  # e.g. latency > 500ms
            success = False
            reasons.append(f"High latency detected: {p.signal_name} = {p.value}")
            
    # Check if there's any other service showing regression (new errors)
    other_points = [p for p in request.post_telemetry_window if p.service != target_service]
    for p in other_points:
        if "error" in p.signal_name and float(p.value) > 0.10:
            regression_detected = True
            reasons.append(f"Regression detected in other service '{p.service}': {p.signal_name} = {p.value}")
            
    if success and not regression_detected:
        return VerifyResponse(
            success=True,
            regression_detected=False,
            next_action="DONE"
        )
    elif regression_detected:
        return VerifyResponse(
            success=False,
            regression_detected=True,
            next_action="ROLLBACK",
            escalation_bundle=EscalationBundle(
                reason=f"Healing action caused regression: {'; '.join(reasons)}"
            )
        )
    else:
        return VerifyResponse(
            success=False,
            regression_detected=False,
            # If it failed to recover within the window, escalate to manual SRE
            next_action="ESCALATE",
            escalation_bundle=EscalationBundle(
                reason=f"Healing executed successfully but indicators failed to recover: {'; '.join(reasons)}"
            )
        )
