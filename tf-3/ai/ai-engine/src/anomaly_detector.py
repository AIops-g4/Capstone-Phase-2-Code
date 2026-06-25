import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from .config import (
    IFOREST_MULTIVARIATE_THRESHOLD_MULTIPLIER,
    IFOREST_UNIVARIATE_THRESHOLD_MULTIPLIER,
    EWMA_ALPHA,
    EWMA_THRESHOLD,
    BASELINE_LENGTH
)

class EWMAAnomalyDetector:
    """
    Exponentially Weighted Moving Average (EWMA) Anomaly Detector for univariate time series.
    Suitable for service level indicators like latency and error rates.
    """
    def __init__(self, alpha=EWMA_ALPHA, threshold=EWMA_THRESHOLD):
        self.alpha = alpha
        self.threshold = threshold

    def detect(self, series: pd.Series, baseline_len: int = BASELINE_LENGTH):
        """
        Detect anomalies in a series.
        baseline_len defines the initial period used to compute standard deviation baseline.
        """
        # Calculate EWMA
        ewma = series.ewm(alpha=self.alpha, adjust=False).mean()
        
        # Calculate residuals
        residuals = series - ewma
        
        # Compute standard deviation on baseline period
        baseline_residuals = residuals.iloc[:baseline_len]
        std = baseline_residuals.std()
        if pd.isna(std) or std == 0:
            std = 1e-6  # Prevent division by zero
            
        # Anomaly if residual exceeds threshold * std
        anomalies = np.abs(residuals) > self.threshold * std
        scores = np.abs(residuals) / std
        
        return anomalies, scores

class IsolationForestDetector:
    """
    Isolation Forest Anomaly Detector. Supports both univariate and multivariate inputs.
    Uses dynamic score thresholding based on baseline mean and standard deviation to prevent false positives.
    """
    def __init__(self, threshold_multiplier=4.0, random_state=42):
        self.threshold_multiplier = threshold_multiplier
        self.random_state = random_state
        self.model = None
        self.score_threshold = 0.0

    def fit(self, df_baseline: pd.DataFrame):
        """
        Fit the Isolation Forest model on normal baseline data and calibrate the threshold.
        """
        df_clean = df_baseline.fillna(0)
        
        # Fit with a small nominal contamination
        self.model = IsolationForest(
            contamination=0.01,
            random_state=self.random_state,
            n_estimators=100
        )
        self.model.fit(df_clean)
        
        # Calibrate threshold on the baseline scores
        # decision_function returns negative values for outliers, positive for inliers.
        # We invert it: higher score = more anomalous
        baseline_scores = -self.model.decision_function(df_clean)
        mean_score = np.mean(baseline_scores)
        std_score = np.std(baseline_scores)
        self.score_threshold = mean_score + self.threshold_multiplier * std_score
        print(f"  [IForest Calibration] Baseline score mean: {mean_score:.4f}, std: {std_score:.4f}. Threshold set to: {self.score_threshold:.4f}")

    def detect(self, df: pd.DataFrame):
        """
        Predict anomalies on the dataset. Returns boolean anomaly flags and anomaly scores.
        """
        if self.model is None:
            raise ValueError("Model must be fitted before detection.")
            
        df_clean = df.fillna(0)
        scores = -self.model.decision_function(df_clean)
        anomalies = scores > self.score_threshold
        
        return anomalies, scores

def run_metric_anomaly_detection(df_metrics: pd.DataFrame, baseline_len: int = BASELINE_LENGTH):
    """
    Runs the comprehensive metric anomaly detection pipeline.
    1. Multivariate Isolation Forest on all metrics.
    2. Univariate Isolation Forest on individual resource metrics (CPU, Memory, Sockets, DiskIO)
       to pinpoint which metric of which service is anomalous.
    3. EWMA on service-level metrics (Latency, Errors) to detect sudden spikes.
    
    Returns a dictionary of results.
    """
    # 1. Prepare data (exclude time column)
    df_features = df_metrics.drop(columns=["time"], errors="ignore")
    df_baseline = df_features.iloc[:baseline_len]
    
    # 2. Multivariate Isolation Forest
    mif = IsolationForestDetector(threshold_multiplier=IFOREST_MULTIVARIATE_THRESHOLD_MULTIPLIER)
    mif.fit(df_baseline)
    mif_anomalies, mif_scores = mif.detect(df_features)
    
    # 3. Univariate Isolation Forest and EWMA for each column
    univariate_results = {}
    ewma_results = {}
    
    # Identify service columns
    for col in df_features.columns:
        series = df_features[col]
        
        # Check if the column is a service level indicator (latency, error) -> use EWMA
        if "latency" in col or "error" in col:
            detector = EWMAAnomalyDetector(alpha=EWMA_ALPHA, threshold=EWMA_THRESHOLD)
            anoms, scores = detector.detect(series, baseline_len)
            ewma_results[col] = {
                "anomalies": anoms,
                "scores": scores
            }
        else:
            # Resource metrics (cpu, mem, socket, diskio, workload) -> use Univariate Isolation Forest
            detector = IsolationForestDetector(threshold_multiplier=IFOREST_UNIVARIATE_THRESHOLD_MULTIPLIER)
            # Take baseline for this single column
            col_baseline = pd.DataFrame({col: df_baseline[col]})
            detector.fit(col_baseline)
            col_features = pd.DataFrame({col: df_features[col]})
            anoms, scores = detector.detect(col_features)
            univariate_results[col] = {
                "anomalies": anoms,
                "scores": scores
            }
            
    return {
        "multivariate": {
            "anomalies": mif_anomalies,
            "scores": mif_scores
        },
        "univariate": univariate_results,
        "ewma": ewma_results
    }
