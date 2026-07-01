import pandas as pd
import numpy as np
from typing import Tuple, List, Dict, Any

from .config import (
    CORRELATION_THRESHOLD, 
    BASELINE_LENGTH, 
    ANALYSIS_WINDOW_SIZE, 
    USE_BARO_RCA, 
    BARO_TOP_K,
    RCA_ZSCORE_THRESHOLD,
    RCA_ZSCORE_MAX_CONTRIBUTION,
    RCA_LOG_METRIC_DEFAULT_WEIGHT,
    RCA_LOG_METRIC_COLOCATED_WEIGHT,
    RCA_LOG_METRIC_MULTIPLIER,
    RCA_CONFIDENCE_MAX,
    RCA_CONFIDENCE_BASE,
    RCA_CONFIDENCE_DIVISOR,
    RCA_SMOOTHING_WINDOW,
    RCA_DEVIATION_WINDOW,
    RCA_ANALYSIS_WINDOW_AFTER,
    BARO_RCA_CONFIDENCE,
    RCA_STD_REG_MULTIPLIER,
    RCA_STD_REG_ADDITIVE,
    SERVICES_LIST,
    METRIC_TYPES_LIST,
    FAULT_SIGNAL_PATTERNS,
    FAULT_SIGNAL_WEIGHTS,
    FAULT_LOG_EVIDENCE_WEIGHT,
    FAULT_BARO_RANK_WEIGHT,
    FAULT_SCORE_MIN,
)


class RootCauseAnalyzer:
    """
    Analyzes the correlation and deviation between metric timeseries and log template frequencies
    to localize the root-cause service and fault type.
    """
    def __init__(self, correlation_threshold=CORRELATION_THRESHOLD, baseline_len=BASELINE_LENGTH):
        self.correlation_threshold = correlation_threshold
        self.baseline_len = baseline_len
        self.use_baro = USE_BARO_RCA
        self.baro_top_k = BARO_TOP_K
        self.last_top_k = []

    def analyze(self, 
                 df_metrics: pd.DataFrame, 
                 df_logs: pd.DataFrame, 
                 template_info: dict, 
                 anomaly_idx: int, 
                 window_size: int = ANALYSIS_WINDOW_SIZE) -> Tuple[str, str, str, float]:
        """
        Calculates correlation and metric deviations around the anomaly index.
        
        Returns:
        - target_service: Str, the diagnosed faulty service.
        - suspected_fault_type: Str, the diagnosed fault type.
        - reasoning: Str, explanation of the findings.
        - confidence: Float, confidence score of the diagnosis (0.0 to 1.0).
        """
        self.last_top_k = []
        
        # 1. BARO RCA Engine Path
        if self.use_baro:
            try:
                from baro.root_cause_analysis import robust_scorer
                
                # Clean metrics data (exclude time column, handle NaNs/Infs)
                df_clean = df_metrics.drop(columns=["time"], errors="ignore").replace([np.inf, -np.inf], np.nan).ffill().bfill().fillna(0).copy()
                
                # Scale-normalization to prevent fake astronomical Z-scores on constant metrics with large raw units (like redis_diskio)
                for col in df_clean.columns:
                    std = df_clean[col].std()
                    mean = df_clean[col].mean()
                    scale = max(std, RCA_STD_REG_MULTIPLIER * abs(mean) + RCA_STD_REG_ADDITIVE)
                    df_clean[col] = df_clean[col] / scale
                    
                # Perform root cause analysis using robust_scorer on scale-normalized metrics
                baro_res = robust_scorer(df_clean, anomalies=[anomaly_idx])
                ranks = baro_res.get("ranks", [])
                
                # Map metric ranks to service ranks
                for r in ranks:
                    service, _ = self._map_metric_to_service_fault(r)
                    if service not in self.last_top_k:
                        self.last_top_k.append(service)
                        
                if self.last_top_k:
                    best_service = self.last_top_k[0]
                    # Infer fault type by aggregating generic signal evidence for
                    # the selected service instead of trusting one strongest symptom.
                    z_fault, z_metric, z_val, fault_scores = self._infer_fault_for_service(
                        df_metrics,
                        anomaly_idx,
                        best_service,
                        ranked_metrics=ranks,
                        template_info=template_info,
                    )
                    if z_metric and max(fault_scores.values(), default=0.0) >= FAULT_SCORE_MIN:
                        suspected_fault_type = z_fault
                        top_metric = z_metric
                    else:
                        # Fallback: BARO rank metric for this service
                        suspected_fault_type = "cpu"
                        top_metric = ranks[0] if ranks else ""
                        for ranked_metric in ranks:
                            svc, fault = self._map_metric_to_service_fault(ranked_metric)
                            if svc == best_service:
                                suspected_fault_type = fault
                                top_metric = ranked_metric
                                break
                        else:
                            if ranks:
                                _, suspected_fault_type = self._map_metric_to_service_fault(ranks[0])

                    confidence = BARO_RCA_CONFIDENCE
                    reasoning = (
                        f"[BARO RCA] Diagnosed {best_service} ({suspected_fault_type}) as root cause "
                        f"from aggregated fault evidence led by '{top_metric}' (z={z_val:.1f}, "
                        f"scores={self._format_fault_scores(fault_scores)}). "
                        f"Top candidates: {', '.join(ranks[:self.baro_top_k])}."
                    )
                    if len(reasoning) > 300:
                        reasoning = reasoning[:297] + "..."
                    return best_service, suspected_fault_type, reasoning, confidence
            except Exception as e:
                print(f"  [BARO ERROR] Failed to run simplified BARO robust_scorer: {e}. Falling back to default RCA.")

        # 2. Default RCA Engine Path (Pearson + Z-score Deviation)
        start_idx = max(0, anomaly_idx - window_size)
        end_idx = min(len(df_metrics) - 1, anomaly_idx + RCA_ANALYSIS_WINDOW_AFTER)
        
        window_metrics = df_metrics.iloc[start_idx:end_idx+1].copy()
        window_logs = df_logs.iloc[start_idx:end_idx+1].copy()
        
        metric_features = window_metrics.drop(columns=["time"], errors="ignore")
        log_features = window_logs.drop(columns=["time"], errors="ignore")
        
        # Apply smoothing to metrics and logs
        metric_smooth = metric_features.rolling(window=RCA_SMOOTHING_WINDOW, min_periods=1).mean()
        log_smooth = log_features.rolling(window=RCA_SMOOTHING_WINDOW, min_periods=1).mean()
        
        # Compute Pearson Correlation Matrix
        combined_df = pd.concat([metric_smooth, log_smooth], axis=1)
        corr_matrix = combined_df.corr(method="pearson").fillna(0)
        
        metric_cols = list(metric_features.columns)
        log_cols = list(log_features.columns)
        sub_corr = corr_matrix.loc[metric_cols, log_cols]
        
        # Calculate Z-score deviations regularizing standard deviations
        baseline_df = df_metrics.iloc[:self.baseline_len].drop(columns=["time"], errors="ignore")
        baseline_means = baseline_df.mean()
        baseline_stds = baseline_df.std()
        regularized_stds = np.maximum(baseline_stds, RCA_STD_REG_MULTIPLIER * baseline_means.abs() + RCA_STD_REG_ADDITIVE)
        
        end_dev_idx = min(len(df_metrics) - 1, anomaly_idx + RCA_DEVIATION_WINDOW)
        window_metrics_dev = df_metrics.iloc[anomaly_idx:end_dev_idx+1].drop(columns=["time"], errors="ignore")
        z_scores = ((window_metrics_dev - baseline_means).abs() / regularized_stds).max()
        
        # Aggregate diagnostic scores per service and fault type
        service_scores = {s: 0.0 for s in SERVICES_LIST}
        service_fault_scores = {s: {t: 0.0 for t in FAULT_SIGNAL_PATTERNS} for s in SERVICES_LIST}
        high_corr_evidence = []
        
        for m_col in metric_cols:
            col_service = None
            col_type = None
            
            for s in SERVICES_LIST:
                if m_col.startswith(s):
                    col_service = s
                    break
            
            _, mapped_fault = self._map_metric_to_service_fault(m_col)
            col_type = mapped_fault if mapped_fault in service_fault_scores.get(col_service, {}) else None
                    
            if not col_service or not col_type:
                continue
                
            # A. Add Z-score deviation to score
            z_val = z_scores.get(m_col, 0.0)
            if z_val > RCA_ZSCORE_THRESHOLD:
                z_score_contrib = min(RCA_ZSCORE_MAX_CONTRIBUTION, z_val)
                service_scores[col_service] += z_score_contrib
                service_fault_scores[col_service][col_type] += z_score_contrib
            
            # B. Add Log Correlation to score (only for error templates)
            for l_col in log_cols:
                corr_val = abs(sub_corr.loc[m_col, l_col])
                
                if corr_val >= self.correlation_threshold:
                    t_info = template_info.get(l_col, {})
                    is_err = t_info.get("is_error", False)
                    
                    if not is_err:
                        continue
                        
                    l_container = t_info.get("container", "")
                    pattern = t_info.get("pattern", "")
                    
                    weight = RCA_LOG_METRIC_DEFAULT_WEIGHT
                    if l_container == col_service:
                        weight = RCA_LOG_METRIC_COLOCATED_WEIGHT
                        
                    score = corr_val * weight * RCA_LOG_METRIC_MULTIPLIER
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
                    
        # Sort and map results
        sorted_services = sorted(service_scores.items(), key=lambda x: x[1], reverse=True)
        self.last_top_k = [s[0] for s in sorted_services]
        
        best_service = None
        max_service_score = -1.0
        if sorted_services:
            best_service = sorted_services[0][0]
            max_service_score = sorted_services[0][1]
                
        if not best_service or max_service_score <= 0.0:
            best_service = "checkoutservice"
            suspected_fault_type = "cpu"
            confidence = 0.50
            reasoning = "No strong metric deviation or log correlation found. Defaulting to checkoutservice cpu."
            return best_service, suspected_fault_type, reasoning, confidence
            
        suspected_fault_type = "cpu"
        service_evidence = [e for e in high_corr_evidence if e["metric"].startswith(best_service)]
        service_evidence.sort(key=lambda x: x["correlation"], reverse=True)
            
        confidence = min(RCA_CONFIDENCE_MAX, RCA_CONFIDENCE_BASE + (max_service_score / RCA_CONFIDENCE_DIVISOR))
        
        # Check maximum Z-score of this service's metrics
        service_metrics = [m for m in metric_cols if m.startswith(best_service)]
        max_z_col = None
        max_z_val = 0.0
        for m in service_metrics:
            z_val = z_scores.get(m, 0.0)
            if z_val > max_z_val:
                max_z_val = z_val
                max_z_col = m

        fault_type, evidence_metric, evidence_z, fault_scores = self._infer_fault_for_service(
            df_metrics,
            anomaly_idx,
            best_service,
            template_info=template_info,
        )
        if max(fault_scores.values(), default=0.0) >= FAULT_SCORE_MIN:
            suspected_fault_type = fault_type
            if evidence_metric:
                max_z_col = evidence_metric
                max_z_val = evidence_z
        else:
            fault_scores = service_fault_scores.get(best_service, {})
            if fault_scores:
                suspected_fault_type = max(fault_scores.items(), key=lambda x: x[1])[0]

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
            
        if len(reasoning) > 300:
            reasoning = reasoning[:297] + "..."
            
        return best_service, suspected_fault_type, reasoning, confidence

    def _z_scores_at_anomaly(
        self, df_metrics: pd.DataFrame, anomaly_idx: int
    ) -> pd.Series:
        """Max absolute Z-score per metric column around the anomaly index."""
        baseline_df = (
            df_metrics.iloc[: self.baseline_len]
            .drop(columns=["time"], errors="ignore")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0)
        )
        baseline_means = baseline_df.mean().fillna(0)
        baseline_stds = baseline_df.std().fillna(0)
        regularized_stds = np.maximum(
            baseline_stds,
            RCA_STD_REG_MULTIPLIER * baseline_means.abs() + RCA_STD_REG_ADDITIVE,
        )
        end_dev_idx = min(len(df_metrics) - 1, anomaly_idx + RCA_DEVIATION_WINDOW)
        window_metrics_dev = (
            df_metrics.iloc[anomaly_idx : end_dev_idx + 1]
            .drop(columns=["time"], errors="ignore")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0)
        )
        return ((window_metrics_dev - baseline_means).abs() / regularized_stds).max().fillna(0)

    def _infer_fault_for_service(
        self,
        df_metrics: pd.DataFrame,
        anomaly_idx: int,
        best_service: str,
        ranked_metrics: List[str] | None = None,
        template_info: Dict[str, Any] | None = None,
    ) -> Tuple[str, str, float, Dict[str, float]]:
        """
        Infer fault type by aggregating configurable signal-family evidence.

        This is intentionally generic: it uses metric/log signal names, BARO rank
        position, and configurable weights. It does not inspect service names,
        tenant/team IDs, dataset folder names, or ground-truth labels.

        Returns (fault_type, lead_metric_or_signal, z_score, fault_scores).
        """
        z_scores = self._z_scores_at_anomaly(df_metrics, anomaly_idx)
        fault_scores = {fault: 0.0 for fault in FAULT_SIGNAL_PATTERNS}
        lead_metric_by_fault = {fault: "" for fault in FAULT_SIGNAL_PATTERNS}
        lead_z_by_fault = {fault: 0.0 for fault in FAULT_SIGNAL_PATTERNS}

        for col in z_scores.index:
            if col == "time" or not str(col).startswith(best_service):
                continue
            z_val = float(z_scores.get(col, 0.0))
            if not np.isfinite(z_val) or z_val <= 0:
                continue
            evidence_strength = min(RCA_ZSCORE_MAX_CONTRIBUTION, z_val)
            signal_groups = self._fault_groups_for_text(self._metric_suffix(col, best_service))
            for fault in signal_groups:
                weight = FAULT_SIGNAL_WEIGHTS.get(fault, 1.0)
                fault_scores[fault] = fault_scores.get(fault, 0.0) + evidence_strength * weight
                if z_val > lead_z_by_fault.get(fault, 0.0):
                    lead_z_by_fault[fault] = z_val
                    lead_metric_by_fault[fault] = str(col)

        for rank_idx, ranked_metric in enumerate(ranked_metrics or []):
            svc, _ = self._map_metric_to_service_fault(str(ranked_metric))
            if svc != best_service:
                continue
            rank_strength = FAULT_BARO_RANK_WEIGHT / max(1, rank_idx + 1)
            for fault in self._fault_groups_for_text(self._metric_suffix(str(ranked_metric), best_service)):
                fault_scores[fault] = fault_scores.get(fault, 0.0) + rank_strength
                if not lead_metric_by_fault.get(fault):
                    lead_metric_by_fault[fault] = str(ranked_metric)

        for info in (template_info or {}).values():
            if info.get("container") != best_service or not info.get("is_error", False):
                continue
            pattern = str(info.get("pattern", ""))
            for fault in self._fault_groups_for_text(pattern):
                weight = FAULT_SIGNAL_WEIGHTS.get(fault, 1.0)
                fault_scores[fault] = fault_scores.get(fault, 0.0) + (FAULT_LOG_EVIDENCE_WEIGHT * weight)
                if not lead_metric_by_fault.get(fault):
                    lead_metric_by_fault[fault] = f"log:{pattern[:80]}"

        if not fault_scores:
            return "cpu", "", 0.0, {}
        best_fault, best_score = max(fault_scores.items(), key=lambda item: item[1])
        if best_score <= 0:
            return "cpu", "", 0.0, fault_scores
        return (
            best_fault,
            lead_metric_by_fault.get(best_fault, ""),
            lead_z_by_fault.get(best_fault, 0.0),
            fault_scores,
        )

    def _fault_groups_for_text(self, text: str) -> List[str]:
        text_lower = str(text).lower()
        matches = []
        for fault, tokens in FAULT_SIGNAL_PATTERNS.items():
            if any(token in text_lower for token in tokens):
                matches.append(fault)
        return matches

    def _metric_suffix(self, metric_name: str, service: str) -> str:
        metric_lower = str(metric_name).lower()
        service_lower = str(service).lower()
        if metric_lower.startswith(service_lower + "_"):
            return metric_lower[len(service_lower) + 1 :]
        return metric_lower

    def _format_fault_scores(self, fault_scores: Dict[str, float]) -> str:
        top_scores = sorted(fault_scores.items(), key=lambda item: item[1], reverse=True)[:3]
        return ",".join(f"{fault}:{score:.1f}" for fault, score in top_scores)

    def _map_metric_to_service_fault(self, metric_name: str) -> Tuple[str, str]:
        """1A: Map RE2 metric column names to (service, fault_type)."""
        metric_lower = metric_name.lower()

        service = metric_name.split("_", 1)[0]
        for svc in sorted(SERVICES_LIST, key=len, reverse=True):
            if metric_lower.startswith(svc.lower() + "_") or metric_lower == svc.lower():
                service = svc
                break

        metric_suffix = (
            metric_lower[len(service) + 1 :]
            if metric_lower.startswith(service.lower())
            else metric_lower
        )

        groups = self._fault_groups_for_text(metric_suffix)
        fault_type = groups[0] if groups else "cpu"
        return service, fault_type


# Create alias for backward compatibility
CorrelationAnalyzer = RootCauseAnalyzer
