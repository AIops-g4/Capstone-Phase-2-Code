import os
import uuid
import json
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Literal, Union
from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, ConfigDict

from .anomaly_detector import run_metric_anomaly_detection, IsolationForestDetector, EWMAAnomalyDetector
from .log_parser import Drain3LogParser
from .correlation_analyzer import CorrelationAnalyzer
from .self_healer import SelfHealer
from .config import (
    RUNBOOKS_PATH, 
    API_HOST, 
    API_PORT, 
    ANALYSIS_WINDOW_SIZE,
    CORRELATION_THRESHOLD,
    ALERT_HEALING_WINDOW_SECONDS,
    VERIFY_ERROR_THRESHOLD,
    VERIFY_LATENCY_THRESHOLD,
    VERIFY_REGRESSION_ERROR_THRESHOLD
)

app = FastAPI(
    title="AIOps AI Engine Service",
    description="Generic Multi-Tenant Self-Heal Platform AI Engine with Alert Correlation & Deduplication.",
    version="1.1.0"
)

# --- Alert Correlation & Deduplication Engine ---

class AlertCorrelationEngine:
    """
    Correlates concurrent alerts across the service dependency graph
    to deduplicate alarms and prevent redundant self-healing loops.
    """
    def __init__(self, healing_window_seconds=ALERT_HEALING_WINDOW_SECONDS):
        self.healing_window_seconds = healing_window_seconds
        self.active_incidents = {}  # correlation_id -> incident_dict
        
        # Microservices dependency graph (downstream service -> list of direct/indirect upstream dependencies)
        self.dependency_graph = {
            "frontend": ["checkoutservice", "recommendationservice", "productcatalogservice", "cartservice", "shippingservice", "currencyservice", "adservice", "paymentservice", "emailservice"],
            "checkoutservice": ["shippingservice", "emailservice", "paymentservice", "cartservice", "currencyservice", "productcatalogservice"]
        }

    def correlate(self, target_service: str, fault_type: str, current_time: int):
        """
        Correlates a newly detected service anomaly against active incidents.
        Returns (correlation_id, is_symptom_or_duplicate).
        """
        self._cleanup_expired(current_time)
        
        # 1. Check if there is an active incident on the exact same root-cause service
        for corr_id, inc in self.active_incidents.items():
            if inc["root_cause_service"] == target_service:
                print(f"  [ALERT CORRELATION] Alert on {target_service} correlated as DUPLICATE of active incident {corr_id}.")
                return corr_id, True
                
        # 2. Check if the newly flagged service is a downstream symptom of an active upstream incident
        for corr_id, inc in self.active_incidents.items():
            upstream_service = inc["root_cause_service"]
            
            # If target_service is downstream of the upstream_service, it's a symptom
            if target_service in self.dependency_graph and upstream_service in self.dependency_graph[target_service]:
                inc["symptoms"].append(target_service)
                print(f"  [ALERT CORRELATION] Alert on downstream {target_service} correlated as SYMPTOM of upstream incident {corr_id} ({upstream_service}).")
                return corr_id, True
                
        # 3. No correlation found -> Create new primary incident
        new_corr_id = str(uuid.uuid4())
        self.active_incidents[new_corr_id] = {
            "root_cause_service": target_service,
            "fault_type": fault_type,
            "start_time": current_time,
            "symptoms": [],
            "status": "HEALING",
            "decided": False
        }
        print(f"  [ALERT CORRELATION] Created new primary incident {new_corr_id} for root cause service {target_service} ({fault_type}).")
        return new_corr_id, False

    def close_incident(self, correlation_id: str):
        if correlation_id in self.active_incidents:
            print(f"  [ALERT CORRELATION] Closing active incident {correlation_id}.")
            del self.active_incidents[correlation_id]

    def _cleanup_expired(self, current_time: int):
        expired = []
        for corr_id, inc in self.active_incidents.items():
            # Auto-expire after healing window + 60s buffer
            if current_time - inc["start_time"] > self.healing_window_seconds + 60:
                expired.append(corr_id)
        for corr_id in expired:
            print(f"  [ALERT CORRELATION] Expired active incident {corr_id} from registry.")
            del self.active_incidents[corr_id]


# Initialize modules
healer = SelfHealer(RUNBOOKS_PATH)
log_parser = Drain3LogParser(service_aware=True)
correlation_analyzer = CorrelationAnalyzer(correlation_threshold=CORRELATION_THRESHOLD)
alert_correlator = AlertCorrelationEngine(healing_window_seconds=ALERT_HEALING_WINDOW_SECONDS)


# --- Pydantic Models for Schema Validation ---

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
    target_service: Union[str, List[str]] = Field(..., description="Identified faulty service or top 5 services")
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


# --- Endpoints ---

@app.post("/v1/detect", response_model=DetectResponse, response_model_exclude_none=True)
async def detect_anomalies(request: DetectRequest):
    """
    Endpoint: POST /v1/detect
    Detects anomalies, correlates alerts across dependencies, and deduplicates.
    """
    # 1. Reconstruct metrics and logs
    metrics_records = {}
    log_messages = []
    
    for point in request.telemetry_window:
        ts_sec = int(pd.to_datetime(point.ts).timestamp())
        
        if point.signal_name == "application_log_event":
            log_messages.append({
                "timestamp": ts_sec * 1000000000,
                "container_name": point.service,
                "message": str(point.value),
                "level": point.labels.get("level", "info") if point.labels else "info"
            })
        else:
            if ts_sec not in metrics_records:
                metrics_records[ts_sec] = {"time": ts_sec}
            
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
            correlation_id=request.correlation_id or str(uuid.uuid4())
        )
        
    df_metrics = pd.DataFrame(list(metrics_records.values())).sort_values("time").reset_index(drop=True)
    df_logs = pd.DataFrame(log_messages)
    
    time_start = int(df_metrics["time"].min())
    time_end = int(df_metrics["time"].max())
    
    df_log_ts, temp_info = log_parser.parse_logs(df_logs, time_start, time_end)
    df_metrics = df_metrics.ffill().fillna(0)
    
    # 2. Run Anomaly Detection
    baseline_len = max(10, int(len(df_metrics) * 0.8))
    detection_results = run_metric_anomaly_detection(df_metrics, baseline_len)
    
    mif_anoms = detection_results["multivariate"]["anomalies"]
    mif_scores = detection_results["multivariate"]["scores"]
    
    anomaly_detected = False
    anomaly_idx = -1
    
    # Scan the active detection window (after baseline) from start to end.
    # The moment an anomaly is detected (Multivariate or EWMA), we return it immediately.
    start_check = baseline_len
    for i in range(start_check, len(df_metrics)):
        is_anom = mif_anoms[i]
        if not is_anom:
            for col, results in detection_results["ewma"].items():
                if results["anomalies"][i]:
                    is_anom = True
                    break
        if is_anom:
            anomaly_detected = True
            anomaly_idx = i
            break
                
    if not anomaly_detected:
        return DetectResponse(
            anomaly_detected=False,
            severity=0.0,
            confidence=0.90,
            reasoning="No anomalies detected in the current telemetry window.",
            correlation_id=request.correlation_id or str(uuid.uuid4())
        )
        
    # Anomaly found! Localize root cause
    if anomaly_idx == -1:
        anomaly_idx = len(df_metrics) - 1
        
    target_service, suspected_fault_type, reasoning, confidence = correlation_analyzer.analyze(
        df_metrics=df_metrics,
        df_logs=df_log_ts,
        template_info=temp_info,
        anomaly_idx=anomaly_idx,
        window_size=ANALYSIS_WINDOW_SIZE
    )
    
    raw_severity = mif_scores[anomaly_idx]
    severity = float(np.clip(abs(raw_severity) * 2.0, 0.4, 0.95))
    
    trigger_metric = None
    trigger_val = None
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
                    
    # 3. Apply Alert Correlation & Deduplication
    corr_id, is_correlated = alert_correlator.correlate(target_service, suspected_fault_type, time_end)
    
    if is_correlated:
        reasoning = f"[CORRELATED ALERT] {reasoning}"
        if len(reasoning) > 300:
            reasoning = reasoning[:297] + "..."
            
    top_5_services = correlation_analyzer.last_top_k[:5]
    if not top_5_services:
        top_5_services = [target_service]
        
    context = AnomalyContext(
        target_service=top_5_services,
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

@app.post("/v1/decide", response_model=DecideResponse, response_model_exclude_none=True)
async def decide_action_plan(request: DecideRequest):
    """
    Endpoint: POST /v1/decide
    Suppresses actions for downstream symptoms or redundant healing requests.
    """
    ctx = request.anomaly_context
    target_service = ctx.target_service
    top_service = target_service[0] if isinstance(target_service, list) and target_service else target_service
    suspected_fault_type = ctx.suspected_fault_type
    
    # 1. Check if this is a correlated symptom or duplicate using the server-side AlertCorrelationEngine state
    is_suppressed = False
    suppression_reason = ""
    
    corr_id = request.correlation_id
    if corr_id in alert_correlator.active_incidents:
        incident = alert_correlator.active_incidents[corr_id]
        if top_service == incident["root_cause_service"]:
            if incident.get("decided", False):
                is_suppressed = True
                suppression_reason = "duplicate alert for root-cause (already decided)"
            else:
                incident["decided"] = True
        else:
            is_suppressed = True
            suppression_reason = f"correlated downstream symptom of upstream {incident['root_cause_service']}"
            
    if is_suppressed:
        print(f"  [DEDUPLICATION] Suppressing healing action plan for {top_service} ({suspected_fault_type}): {suppression_reason}.")
        return DecideResponse(
            matched_runbook="CorrelatedSymptomSuppression",
            pattern_type="urgent",
            action_plan=[],  # Empty action plan = do nothing!
            blast_radius_config=BlastRadiusConfig(
                max_pod_impact_pct=0,
                circuit_breaker_error_rate=0.0,
                allowed_namespaces=["production"]
            ),
            verify_policy=VerifyPolicy(window_seconds=10, success_conditions=[]),
            correlation_id=request.correlation_id,
            idempotency_key=request.idempotency_key,
            dry_run_mode=request.dry_run_mode,
            cost_cap_exceeded=False
        )
        
    # 2. Execute healing plan for primary root cause
    decision = healer.decide(top_service, suspected_fault_type)
    
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

@app.post("/v1/verify", response_model=VerifyResponse, response_model_exclude_none=True)
async def verify_healing(request: VerifyRequest):
    """
    Endpoint: POST /v1/verify
    Verifies and closes the correlated incident upon successful recovery.
    """
    action = request.action_executed
    corr_id = request.correlation_id
    
    if action.status == "FAILED":
        return VerifyResponse(
            success=False,
            regression_detected=False,
            next_action="RETRY",
            escalation_bundle=EscalationBundle(
                reason=f"Healing action '{action.action}' on '{action.target}' failed to execute on CDO executor."
            )
        )
        
    success = True
    regression_detected = False
    reasons = []
    
    target_service = action.target.split("/")[-1]
    service_points = [p for p in request.post_telemetry_window if p.service == target_service]
    
    for p in service_points:
        if "error" in p.signal_name and float(p.value) > VERIFY_ERROR_THRESHOLD:
            success = False
            reasons.append(f"High error rate detected: {p.signal_name} = {p.value}")
        if "latency" in p.signal_name and float(p.value) > VERIFY_LATENCY_THRESHOLD:
            success = False
            reasons.append(f"High latency detected: {p.signal_name} = {p.value}")
            
    other_points = [p for p in request.post_telemetry_window if p.service != target_service]
    for p in other_points:
        if "error" in p.signal_name and float(p.value) > VERIFY_REGRESSION_ERROR_THRESHOLD:
            regression_detected = True
            reasons.append(f"Regression detected in other service '{p.service}': {p.signal_name} = {p.value}")
            
    if success and not regression_detected:
        # Close the incident in the correlation engine
        alert_correlator.close_incident(corr_id)
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
            next_action="ESCALATE",
            escalation_bundle=EscalationBundle(
                reason=f"Healing executed successfully but indicators failed to recover: {'; '.join(reasons)}"
            )
        )
