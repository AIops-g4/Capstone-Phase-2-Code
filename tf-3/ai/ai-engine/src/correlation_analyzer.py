import pandas as pd
import numpy as np

from .config import CORRELATION_THRESHOLD, BASELINE_LENGTH, ANALYSIS_WINDOW_SIZE

class CorrelationAnalyzer:
    """
    Analyzes the correlation and deviation between metric timeseries and log template frequencies
    to localize the root-cause service and fault type.
    """
    def __init__(self, correlation_threshold=CORRELATION_THRESHOLD, baseline_len=BASELINE_LENGTH):
        self.correlation_threshold = correlation_threshold
        self.baseline_len = baseline_len

    def analyze(self, 
                df_metrics: pd.DataFrame, 
                df_logs: pd.DataFrame, 
                template_info: dict, 
                anomaly_idx: int, 
                window_size: int = ANALYSIS_WINDOW_SIZE):
        """
        Calculates correlation and metric deviations around the anomaly index.
        
        Parameters:
        - df_metrics: DataFrame of simple metrics.
        - df_logs: DataFrame of log template frequencies.
        - template_info: Dict with template patterns and containers.
        - anomaly_idx: Index of the detected anomaly.
        - window_size: Size of the analysis window (in seconds) preceding and including the anomaly.
        
        Returns:
        - target_service: Str, the diagnosed faulty service.
        - suspected_fault_type: Str, the diagnosed fault type.
        - reasoning: Str, explanation of the findings.
        - confidence: Float, confidence score of the diagnosis (0.0 to 1.0).
        """
        # 1. Define window around the anomaly
        start_idx = max(0, anomaly_idx - window_size)
        end_idx = min(len(df_metrics) - 1, anomaly_idx + 10)
        
        window_metrics = df_metrics.iloc[start_idx:end_idx+1].copy()
        window_logs = df_logs.iloc[start_idx:end_idx+1].copy()
        
        metric_features = window_metrics.drop(columns=["time"], errors="ignore")
        log_features = window_logs.drop(columns=["time"], errors="ignore")
        
        # Apply smoothing (rolling average of 15 seconds) to metrics and logs to remove high-frequency noise
        metric_smooth = metric_features.rolling(window=15, min_periods=1).mean()
        log_smooth = log_features.rolling(window=15, min_periods=1).mean()
        
        # 2. Compute Pearson Correlation Matrix on smoothed features
        combined_df = pd.concat([metric_smooth, log_smooth], axis=1)
        corr_matrix = combined_df.corr(method="pearson").fillna(0)
        
        metric_cols = list(metric_features.columns)
        log_cols = list(log_features.columns)
        sub_corr = corr_matrix.loc[metric_cols, log_cols]
        
        # 3. Calculate Z-score deviations for each metric over a 30-second window after anomaly is flagged
        # Baseline is the first 600 rows
        baseline_df = df_metrics.iloc[:self.baseline_len].drop(columns=["time"], errors="ignore")
        baseline_means = baseline_df.mean()
        baseline_stds = baseline_df.std()
        
        # Regularize standard deviations to prevent division by zero or fake astronomical Z-scores on constant metrics.
        # Clip standard deviation to at least 5% of the mean plus a small absolute constant (0.05)
        regularized_stds = np.maximum(baseline_stds, 0.05 * baseline_means.abs() + 0.05)
        
        end_dev_idx = min(len(df_metrics) - 1, anomaly_idx + 30)
        window_metrics_dev = df_metrics.iloc[anomaly_idx:end_dev_idx+1].drop(columns=["time"], errors="ignore")
        z_scores = ((window_metrics_dev - baseline_means).abs() / regularized_stds).max()
        
        # 4. Aggregate diagnostic scores per service and fault type
        services = ["checkoutservice", "currencyservice", "emailservice", "productcatalogservice", "recommendationservice", 
                    "adservice", "cartservice", "frontend", "paymentservice", "redis", "shippingservice"]
        
        metric_types = ["cpu", "mem", "latency", "error", "socket", "diskio"]
        
        service_scores = {s: 0.0 for s in services}
        service_fault_scores = {s: {t: 0.0 for t in metric_types} for s in services}
        high_corr_evidence = []
        
        for m_col in metric_cols:
            col_service = None
            col_type = None
            
            for s in services:
                if m_col.startswith(s):
                    col_service = s
                    break
            
            for t in metric_types:
                if t in m_col:
                    col_type = t
                    break
                    
            if not col_service or not col_type:
                continue
                
            # A. Add Z-score deviation to the score (highly anomalous metrics indicate the root cause)
            z_val = z_scores.get(m_col, 0.0)
            if z_val > 3.0:  # Statistically significant anomaly (> 3 std devs)
                z_score_contrib = min(500.0, z_val)
                service_scores[col_service] += z_score_contrib
                service_fault_scores[col_service][col_type] += z_score_contrib
            
            # B. Add Log Correlation to the score (ONLY for error-related logs)
            for l_col in log_cols:
                corr_val = abs(sub_corr.loc[m_col, l_col])
                
                if corr_val >= self.correlation_threshold:
                    t_info = template_info.get(l_col, {})
                    is_err = t_info.get("is_error", False)
                    
                    # CRITICAL: Ignore non-error logs to prevent normal workload templates from causing false positives
                    if not is_err:
                        continue
                        
                    l_container = t_info.get("container", "")
                    pattern = t_info.get("pattern", "")
                    
                    # Log-metric correlation weight
                    weight = 1.0
                    if l_container == col_service:
                        weight = 3.0  # High weight for co-located container logs and metrics
                        
                    score = corr_val * weight * 15.0  # High weight for genuine error correlations
                    service_scores[col_service] += score
                    service_fault_scores[col_service][col_type] += score
                    
                    high_corr_evidence.append({
                        "metric": m_col,
                        "log": l_col,
                        "container": l_container,
                        "correlation": corr_val,
                        "pattern": pattern,
                        "score": score
                    })
                    
        # 5. Determine the best service
        best_service = None
        max_service_score = -1.0
        
        for s, score in service_scores.items():
            if score > max_service_score:
                max_service_score = score
                best_service = s
                
        if not best_service or max_service_score <= 0.0:
            # Absolute fallback to checkoutservice cpu
            best_service = "checkoutservice"
            suspected_fault_type = "cpu"
            confidence = 0.50
            reasoning = "No strong metric deviation or log correlation found. Defaulting to checkoutservice cpu."
            return best_service, suspected_fault_type, reasoning, confidence
            
        # 6. Determine the suspected fault type
        # Look for semantic keywords in the most correlated error logs for this service
        service_evidence = [e for e in high_corr_evidence if e["metric"].startswith(best_service)]
        service_evidence.sort(key=lambda x: x["correlation"], reverse=True)
        
        semantic_fault = None
        
        fault_keywords = {
            "disk": ["disk", "io", "write", "read", "storage", "file", "flush", "buffer", "space"],
            "socket": ["socket", "port", "bind", "connection", "exhaust", "address", "descriptor"],
            "loss": ["loss", "drop", "packet", "reset", "disconnect", "unreachable"],
            "delay": ["delay", "latency", "slow", "timeout", "wait", "lag"],
            "cpu": ["cpu", "thread", "busy", "load", "saturation", "active"],
            "mem": ["mem", "memory", "oom", "alloc", "heap", "garbage", "gc", "out of memory"]
        }
        
        for ev in service_evidence:
            pattern = ev["pattern"].lower()
            for f_type, keywords in fault_keywords.items():
                if any(kw in pattern for kw in keywords):
                    semantic_fault = f_type
                    break
            if semantic_fault:
                break
                
        # If semantic fault is found in correlated logs, use it! Otherwise fallback to metric Z-score
        if semantic_fault:
            suspected_fault_type = semantic_fault
            print(f"  [SEMANTIC DIAGNOSIS] Diagnosed fault type '{suspected_fault_type}' based on log template patterns.")
        else:
            # Determine the best fault type based on metric scores
            best_fault = None
            max_fault_score = -1.0
            for t, score in service_fault_scores[best_service].items():
                if score > max_fault_score:
                    max_fault_score = score
                    best_fault = t
            
            fault_mapping = {
                "cpu": "cpu",
                "mem": "mem",
                "latency": "delay",
                "error": "loss",
                "diskio": "disk",
                "socket": "socket"
            }
            suspected_fault_type = fault_mapping.get(best_fault, "cpu")
            
        # 7. Build the reasoning and confidence score
        confidence = min(0.95, 0.70 + (max_service_score / 200.0))
        
        # Check maximum Z-score of this service's metrics for reasoning
        service_metrics = [m for m in metric_cols if m.startswith(best_service)]
        max_z_col = None
        max_z_val = 0.0
        for m in service_metrics:
            z_val = z_scores.get(m, 0.0)
            if z_val > max_z_val:
                max_z_val = z_val
                max_z_col = m
                
        if service_evidence and max_z_col:
            top_ev = service_evidence[0]
            reasoning = (f"Anomaly in {best_service} ({suspected_fault_type}). "
                         f"Metric '{max_z_col}' deviated by {max_z_val:.1f} std devs. "
                         f"Metric '{top_ev['metric']}' correlates (r={top_ev['correlation']:.2f}) "
                         f"with error log: '{top_ev['pattern'][:80]}'.")
        elif max_z_col:
            reasoning = (f"Anomaly in {best_service} ({suspected_fault_type}). "
                         f"Metric '{max_z_col}' deviated significantly by {max_z_val:.1f} standard deviations "
                         f"from the baseline.")
        else:
            reasoning = f"Anomaly detected in {best_service} ({suspected_fault_type}) due to combined metric deviations and log correlation."
            
        # Limit reasoning to 300 characters to comply with the contract SLA
        if len(reasoning) > 300:
            reasoning = reasoning[:297] + "..."
            
        return best_service, suspected_fault_type, reasoning, confidence
