import uuid
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple

from .telemetry import TelemetryProcessor
from .anomaly_detector import AnomalyDetectionPipeline
from .correlation_analyzer import RootCauseAnalyzer
from .incident import IncidentManager
from .self_healer import SelfHealer
from .verifier import VerificationEngine
from .config import (
    RUNBOOKS_PATH,
    BASELINE_LENGTH,
    ANALYSIS_WINDOW_SIZE,
    ALLOWED_NAMESPACES,
    DEFAULT_DEPLOYMENT_TEMPLATE,
    DEFAULT_NAMESPACE,
    SYSTEM_NAME,
)


def _render_deployment(target_service: str) -> str:
    return DEFAULT_DEPLOYMENT_TEMPLATE.replace("{{target_service}}", target_service)


def _build_llm_fault_rank_evidence(
    df_metrics: pd.DataFrame,
    df_log_ts: pd.DataFrame,
    anomaly_idx: int,
    target_service: str,
    baseline_len: int,
    max_metrics: int = 12,
    max_logs: int = 8,
) -> Dict[str, Any]:
    """Summarize the BOCPD input window for LLM fault-type ranking without sending raw telemetry."""
    metric_rows = []
    baseline = df_metrics.iloc[:baseline_len]
    current = df_metrics.iloc[anomaly_idx]
    for col in df_metrics.columns:
        if col == "time":
            continue
        if target_service and not str(col).startswith(target_service):
            continue
        mean = baseline[col].mean()
        std = baseline[col].std()
        val = current[col]
        z = float(abs(val - mean) / std) if std and std > 0 else 0.0
        metric_rows.append({
            "metric": str(col),
            "current_value": float(val),
            "baseline_mean": float(mean) if not pd.isna(mean) else None,
            "baseline_std": float(std) if not pd.isna(std) else None,
            "abs_zscore": round(z, 4),
        })
    metric_rows.sort(key=lambda item: item["abs_zscore"], reverse=True)

    log_rows = []
    if isinstance(df_log_ts, pd.DataFrame) and not df_log_ts.empty:
        time_col = "time" if "time" in df_log_ts.columns else None
        svc_col = "service" if "service" in df_log_ts.columns else None
        msg_col = "message" if "message" in df_log_ts.columns else None
        if svc_col or msg_col:
            logs = df_log_ts
            if svc_col:
                logs = logs[logs[svc_col].astype(str).str.contains(target_service, case=False, na=False)]
            for _, row in logs.head(max_logs).iterrows():
                log_rows.append({
                    "time": row.get(time_col) if time_col else None,
                    "service": row.get(svc_col) if svc_col else target_service,
                    "message": str(row.get(msg_col, ""))[:300] if msg_col else str(row.to_dict())[:300],
                })

    return {
        "bocpd_input_summary": {
            "anomaly_index": int(anomaly_idx),
            "anomaly_time": float(df_metrics.iloc[anomaly_idx]["time"]) if "time" in df_metrics.columns else None,
            "baseline_len": int(baseline_len),
            "metrics_rows": int(len(df_metrics)),
            "top_metric_deviations_for_fixed_service": metric_rows[:max_metrics],
            "log_samples_for_fixed_service": log_rows,
        }
    }

class AIOpsEngine:
    """
    Facade class that coordinates the overall AIOps workflow across the modular engines.
    Exposes clean methods for FastAPI endpoints to delegate to.
    """
    def __init__(self):
        self.telemetry_processor = TelemetryProcessor()
        self.detection_pipeline = AnomalyDetectionPipeline()
        self.rca_analyzer = RootCauseAnalyzer()
        self.rca_analyzer.use_baro = True
        self.incident_manager = IncidentManager()
        self.healing_engine = SelfHealer(RUNBOOKS_PATH)
        self.verifier = VerificationEngine()

    def detect_anomalies(
        self, 
        telemetry_window: List[Any], 
        input_correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ingests telemetry, checks for anomalies, runs RCA, and correlates active alerts.
        """
        print("\n[API][DETECT] =========================================")
        print(f"[API][DETECT] Correlation ID: {input_correlation_id or 'new'}")
        print(f"[API][DETECT] Telemetry points received: {len(telemetry_window)}")
        print("[API][DETECT] Detector stack: BOCPD + EWMA signals | RCA: BARO")

        # 1. Ingest and preprocess telemetry
        df_metrics, df_log_ts, temp_info = self.telemetry_processor.process_telemetry_window(telemetry_window)
        
        if df_metrics.empty:
            print("[API][DETECT] Result: NO_METRICS")
            return {
                "anomaly_detected": False,
                "severity": 0.0,
                "confidence": 1.0,
                "reasoning": "No metrics data found in telemetry window.",
                "correlation_id": input_correlation_id or str(uuid.uuid4())
            }
            
        # 2. Run Anomaly Detection Pipeline
        baseline_len = max(10, int(len(df_metrics) * 0.8))
        print(f"[API][DETECT] Metrics rows={len(df_metrics)} baseline_len={baseline_len}")
        detection_results = self.detection_pipeline.run_pipeline(df_metrics, baseline_len)
        
        mif_anoms = detection_results["multivariate"]["anomalies"]
        mif_scores = detection_results["multivariate"]["scores"]
        
        anomaly_detected = False
        anomaly_idx = -1
        
        # Scan the active window for first flagged anomaly (Multivariate or EWMA)
        for i in range(baseline_len, len(df_metrics)):
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
            print("[API][DETECT] Result: NO_ANOMALY")
            return {
                "anomaly_detected": False,
                "severity": 0.0,
                "confidence": 1.0,
                "reasoning": "No anomalies detected in the active telemetry window.",
                "correlation_id": input_correlation_id or str(uuid.uuid4())
            }
            
        # 3. Diagnose root cause (RCA)
        target_service, suspected_fault_type, reasoning, confidence = self.rca_analyzer.analyze(
            df_metrics=df_metrics,
            df_logs=df_log_ts,
            template_info=temp_info,
            anomaly_idx=anomaly_idx,
            window_size=ANALYSIS_WINDOW_SIZE
        )
        
        # Extract severity at anomaly index
        raw_severity = mif_scores[anomaly_idx]
        severity = float(np.clip(abs(raw_severity) * 2.0, 0.4, 0.95))
        
        # Determine trigger metric with highest deviation
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
                        
        # 4. Correlate with active alerts
        time_end = int(df_metrics["time"].max())
        corr_id, is_correlated = self.incident_manager.correlate_alert(
            target_service=target_service, 
            fault_type=suspected_fault_type, 
            timestamp=time_end
        )
        
        if is_correlated:
            reasoning = f"[CORRELATED ALERT] {reasoning}"
            if len(reasoning) > 300:
                reasoning = reasoning[:297] + "..."
                
        # Return top 5 services list in target_service response
        top_5_services = self.rca_analyzer.last_top_k[:5]
        if not top_5_services:
            top_5_services = [target_service]

        print(f"[API][DETECT] Anomaly index: {anomaly_idx}")
        print(f"[API][DETECT] Predicted Service: {target_service}")
        print(f"[API][DETECT] Predicted Fault:   {suspected_fault_type}")
        print(f"[API][DETECT] Top-{len(top_5_services)} Candidates: {', '.join(top_5_services)}")
        print(f"[API][DETECT] Confidence:        {confidence:.3f}")
        print(f"[API][DETECT] Reasoning:         {reasoning}")
        llm_fault_rank_evidence = _build_llm_fault_rank_evidence(
            df_metrics=df_metrics,
            df_log_ts=df_log_ts,
            anomaly_idx=anomaly_idx,
            target_service=target_service,
            baseline_len=baseline_len,
        )
            
        return {
            "anomaly_detected": True,
            "severity": severity,
            "anomaly_context": {
                "target_service": target_service,
                "suspected_fault_type": suspected_fault_type,
                "system": SYSTEM_NAME,
                "namespace": DEFAULT_NAMESPACE,
                "deployment": _render_deployment(target_service),
                "trigger_metric": trigger_metric,
                "trigger_value": trigger_val
            },
            "service_top_k": top_5_services,
            "llm_fault_rank_evidence": llm_fault_rank_evidence,
            "confidence": confidence,
            "reasoning": reasoning,
            "correlation_id": corr_id
        }

    def rank_fault_types(
        self,
        anomaly_context: Dict[str, Any],
        detect_evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Rank fault types for a fixed service so an external CDO can decide retry order."""
        target = anomaly_context.get("target_service")
        current_fault = anomaly_context.get("suspected_fault_type")
        print("\n[API][FAULT-RANK] =====================================")
        print(f"[API][FAULT-RANK] Fixed Service:      {target}")
        print(f"[API][FAULT-RANK] Current Fault:      {current_fault}")
        result = self.healing_engine.rank_fault_types_with_llm(anomaly_context, detect_evidence or {})
        ranking = result.get("fault_type_ranking", [])
        if ranking:
            order = ", ".join(f"{x.get('suspected_fault_type')}={float(x.get('confidence', 0.0)):.2f}" for x in ranking)
            print(f"[API][FAULT-RANK] Ranked Faults:      {order}")
        else:
            print(f"[API][FAULT-RANK] Ranked Faults:      unavailable ({result.get('error', 'no ranking')})")
        return result

    def decide_healing_action(
        self, 
        correlation_id: str, 
        idempotency_key: str, 
        dry_run_mode: bool, 
        anomaly_context: Dict[str, Any],
        detect_evidence: Optional[Dict[str, Any]] = None,
        tenant_id: str = "default-tenant",
    ) -> Dict[str, Any]:
        """
        Determines and templates healing action plans, suppressing duplicate/symptom alerts.
        """
        target_service = anomaly_context["target_service"]
        suspected_fault_type = anomaly_context["suspected_fault_type"]
        print("\n[API][DECIDE] =========================================")
        print(f"[API][DECIDE] Correlation ID:     {correlation_id}")
        print(f"[API][DECIDE] Input Service/Fault:{target_service} ({suspected_fault_type})")
        
        # Extract top 1 service if it is a list of strings
        top_service = target_service[0] if isinstance(target_service, list) and target_service else target_service
        
        # 1. Incident suppression check (symptom or duplicate)
        is_suppressed = False
        suppression_reason = ""
        
        if correlation_id in self.incident_manager.active_incidents:
            incident = self.incident_manager.active_incidents[correlation_id]
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
            print(f"[API][DECIDE] Suppressed:         {suppression_reason}")
            return {
                "matched_runbook": "CorrelatedSymptomSuppression",
                "pattern_type": "urgent",
                "action_plan": [],  # Empty action plan = do nothing!
                "blast_radius_config": {
                    "max_pod_impact_pct": 0,
                    "circuit_breaker_error_rate": 0.0,
                        "allowed_namespaces": [DEFAULT_NAMESPACE]
                },
                "verify_policy": {"window_seconds": 10, "success_conditions": []},
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
                "dry_run_mode": dry_run_mode,
                "cost_cap_exceeded": False
            }
            
        # 2. Decide healing action for primary root cause (full decide/ rule-based mapping)
        decide_ctx = dict(anomaly_context)
        decide_ctx["target_service"] = top_service
        if not decide_ctx.get("deployment"):
            decide_ctx["deployment"] = _render_deployment(top_service)
        decide_ctx.setdefault("namespace", DEFAULT_NAMESPACE)
        evidence = detect_evidence or {}
        decision = self.healing_engine.decide(decide_ctx, detect_evidence=evidence, tenant_id=tenant_id)
        should_rank_faults = bool(evidence.get("rank_fault_catalog_for_topk_service"))
        fault_type_ranking = {"fault_type_ranking": [], "used": False, "reason": "not_requested"}
        if should_rank_faults:
            fault_type_ranking = self.healing_engine.rank_fault_types_with_llm(
                decide_ctx,
                evidence,
            )
        corrected = decision.get("corrected_anomaly_context") or {}
        exec_service = corrected.get("target_service", top_service)
        exec_fault = corrected.get("suspected_fault_type", suspected_fault_type)
        print(f"[API][DECIDE] Selected Target:    {exec_service} ({exec_fault})")
        print(f"[API][DECIDE] Matched Runbook:    {decision['matched_runbook']}")
        print(f"[API][DECIDE] Action Count:       {len(decision['action_plan'])}")
        ranking_items = fault_type_ranking.get("fault_type_ranking", [])
        if ranking_items:
            ranking_log = ", ".join(
                f"{item.get('suspected_fault_type')}={float(item.get('confidence', 0.0)):.2f}"
                for item in ranking_items
            )
            print(f"[API][DECIDE] Fault Ranking:      {ranking_log}")
        elif should_rank_faults:
            print("[API][DECIDE] Fault Ranking:      requested but unavailable")
        else:
            print("[API][DECIDE] Fault Ranking:      skipped (not a Top-K ranking request)")
        
        return {
            "matched_runbook": decision["matched_runbook"],
            "pattern_type": decision["pattern_type"],
            "action_plan": decision["action_plan"],
            "blast_radius_config": decision["blast_radius_config"],
            "verify_policy": decision["verify_policy"],
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "dry_run_mode": dry_run_mode,
            "cost_cap_exceeded": decision.get("cost_cap_exceeded", False),
            "detect_assessment": decision.get("detect_assessment"),
            "corrected_anomaly_context": decision.get("corrected_anomaly_context"),
            "fault_type_ranking": fault_type_ranking.get("fault_type_ranking", []),
            "fault_type_ranking_used": fault_type_ranking.get("used", False),
        }

    def verify_healing(
        self, 
        correlation_id: str, 
        action_executed: Any, 
        post_telemetry_window: List[Any]
    ) -> Dict[str, Any]:
        """
        Verifies executed healing action and closes incident if successfully resolved.
        """
        print("\n[API][VERIFY] =========================================")
        print(f"[API][VERIFY] Correlation ID:     {correlation_id}")
        print(f"[API][VERIFY] Action Executed:    {getattr(action_executed, 'action', None)} -> {getattr(action_executed, 'target', None)} status={getattr(action_executed, 'status', None)}")
        print(f"[API][VERIFY] Post telemetry pts: {len(post_telemetry_window)}")
        success, regression_detected, next_action, reason = self.verifier.verify_action(
            action_executed, 
            post_telemetry_window
        )
        print(f"[API][VERIFY] Result:             {'OK' if success and next_action == 'DONE' else next_action}")
        print(f"[API][VERIFY] Regression:         {regression_detected}")
        if reason:
            print(f"[API][VERIFY] Reason:             {reason}")
        
        if success and next_action == "DONE":
            # Close the incident in our state engine
            self.incident_manager.close_incident(correlation_id)
            
        response = {
            "success": success,
            "regression_detected": regression_detected,
            "next_action": next_action
        }
        
        if not success:
            response["escalation_bundle"] = {
                "reason": reason
            }
            
        return response
