import uuid
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple

from .telemetry import TelemetryProcessor
from .anomaly_detector import AnomalyDetectionPipeline
from .correlation_analyzer import RootCauseAnalyzer
from .incident import IncidentManager
from .config import ANALYSIS_WINDOW_SIZE

class AIOpsEngine:
    """
    Facade class that coordinates the overall AIOps workflow across the modular engines.
    Exposes clean methods for FastAPI endpoints to delegate to.
    """
    def __init__(self):
        self.telemetry_processor = TelemetryProcessor()
        self.detection_pipeline = AnomalyDetectionPipeline()
        self.rca_analyzer = RootCauseAnalyzer()
        self.incident_manager = IncidentManager()

    def detect_anomalies(
        self, 
        telemetry_window: List[Any], 
        input_correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ingests telemetry, checks for anomalies, runs RCA, and correlates active alerts.
        """
        # 1. Ingest and preprocess telemetry
        df_metrics, df_log_ts, temp_info = self.telemetry_processor.process_telemetry_window(telemetry_window)
        
        if df_metrics.empty:
            return {
                "anomaly_detected": False,
                "severity": 0.0,
                "confidence": 1.0,
                "reasoning": "No metrics data found in telemetry window.",
                "correlation_id": input_correlation_id or str(uuid.uuid4())
            }
            
        # 2. Run Anomaly Detection Pipeline
        baseline_len = max(10, int(len(df_metrics) * 0.8))
        detection_results = self.detection_pipeline.run_pipeline(df_metrics, baseline_len)
        
        mif_anoms = detection_results["multivariate"]["anomalies"]
        mif_scores = detection_results["multivariate"]["scores"]
        
        anomaly_detected = False
        anomaly_idx = -1
        
        # Scan the active window for the first BOCPD change point
        for i in range(baseline_len, len(df_metrics)):
            is_anom = mif_anoms[i]
            if is_anom:
                anomaly_detected = True
                anomaly_idx = i
                break
                
        if not anomaly_detected:
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
            
        return {
            "anomaly_detected": True,
            "severity": severity,
            "anomaly_context": {
                "target_service": target_service,
                "suspected_fault_type": suspected_fault_type,
                "system": "E-COMMERCE",
                "namespace": "production",
                "deployment": target_service,
                "trigger_metric": trigger_metric,
                "trigger_value": trigger_val
            },
            "confidence": confidence,
            "reasoning": reasoning,
            "correlation_id": corr_id
        }
