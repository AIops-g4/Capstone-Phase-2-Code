"""Detect-stage anomaly detection.

This stage intentionally keeps only BOCPD for anomaly/change-point detection.
Alternative detectors belong to older mixed implementations and should not be
used in the detect-only benchmark.
"""

from functools import partial

import numpy as np
import pandas as pd

from .config import BASELINE_LENGTH, BOCPD_HAZARD


class BOCPDDetector:
    """Bayesian Online Change Point Detection wrapper for multivariate metrics."""

    def fit(self, df_baseline: pd.DataFrame) -> None:
        # BOCPD is online/non-parametric for this usage; baseline is accepted for
        # a uniform detector interface and benchmark metadata consistency.
        self.baseline_columns = list(df_baseline.columns)

    def detect(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        from baro._bocpd import MultivariateT, constant_hazard, online_changepoint_detection
        from baro.anomaly_detection import find_cps

        df_clean = (
            df.fillna(0)
            .replace([np.inf, -np.inf], np.nan)
            .ffill()
            .bfill()
            .fillna(0)
        )

        selected_cols = []
        for col in df_clean.columns:
            col_lower = col.lower()
            if "queue-master" in col_lower or "rabbitmq_" in col_lower:
                continue
            if "latency" in col_lower or "error" in col_lower:
                continue
            selected_cols.append(col)

        if selected_cols:
            df_clean = df_clean[selected_cols]

        df_clean = df_clean.loc[:, df_clean.nunique() > 1]
        if df_clean.empty:
            return np.zeros(len(df), dtype=bool), np.zeros(len(df), dtype=float)

        for col in df_clean.columns:
            col_min = df_clean[col].min()
            col_max = df_clean[col].max()
            if col_max - col_min > 1e-6:
                df_clean[col] = (df_clean[col] - col_min) / (col_max - col_min)
            else:
                df_clean[col] = 0.0

        data = df_clean.to_numpy()

        try:
            _, maxes = online_changepoint_detection(
                data,
                partial(constant_hazard, BOCPD_HAZARD),
                MultivariateT(dims=data.shape[1]),
            )
            cps = find_cps(maxes)
            anomaly_indices = [point[0] for point in cps]
        except Exception as exc:
            print(f"  [BOCPD Warning] Detection failed: {exc}. Returning no anomalies.")
            anomaly_indices = []

        anomalies = np.zeros(len(df), dtype=bool)
        for idx in anomaly_indices:
            if 0 <= idx < len(df):
                anomalies[idx] = True

        scores = anomalies.astype(float)
        return anomalies, scores


class AnomalyDetectionPipeline:
    """Detect-only pipeline: BOCPD multivariate anomaly detection."""

    def run_pipeline(self, df_metrics: pd.DataFrame, baseline_len: int = BASELINE_LENGTH) -> dict:
        df_features = df_metrics.drop(columns=["time"], errors="ignore")
        df_baseline = df_features.iloc[:baseline_len]

        detector = BOCPDDetector()
        detector.fit(df_baseline)
        anomalies, scores = detector.detect(df_features)

        return {
            "multivariate": {"anomalies": anomalies, "scores": scores},
            "univariate": {},
            "ewma": {},
        }


def run_metric_anomaly_detection(
    df_metrics: pd.DataFrame, baseline_len: int = BASELINE_LENGTH
) -> dict:
    """Backward-compatible wrapper for the detect-only BOCPD pipeline."""
    return AnomalyDetectionPipeline().run_pipeline(df_metrics, baseline_len)