import numpy as np
from scipy import stats
from typing import List, Tuple, Dict, Any
from app.schemas.common import TelemetryPoint

class TelemetryAnalyzer:
    """
    Component 1: Anomaly Detector using Statistical Methods (Z-Score)
    Instead of LLM, we use deterministic math to find anomalies in time-series data.
    """
    def __init__(self, z_threshold: float = 2.5):
        self.z_threshold = z_threshold

    def analyze(self, telemetry: List[TelemetryPoint]) -> Tuple[bool, float, float, str]:
        """
        Returns: (is_anomaly, severity, confidence, reasoning)
        """
        if not telemetry or len(telemetry) < 3:
            return False, 0.0, 0.0, "Not enough data points for statistical analysis."

        # Extract numerical values
        values = []
        for p in telemetry:
            try:
                values.append(float(p.value))
            except ValueError:
                pass # ignore non-numerical telemetry for now
        
        if not values:
            return False, 0.0, 0.0, "No numerical data found."

        data = np.array(values)
        
        # If variance is 0, zscore returns NaN. Handle it.
        if np.std(data) == 0:
            return False, 0.0, 0.95, "Flatline metric, variance is 0."

        z_scores = stats.zscore(data)
        
        # Check the latest point (which should be the anomaly trigger)
        latest_z = abs(z_scores[-1])
        
        is_anomaly = latest_z > self.z_threshold
        
        # Map Z-Score to severity (0.0 to 1.0)
        # Z=2.5 -> ~0.5, Z=5.0 -> 1.0
        severity = min(latest_z / 5.0, 1.0)
        
        # Confidence based on number of points (more points = higher confidence)
        confidence = min(len(values) / 20.0, 0.95)
        if is_anomaly:
            confidence = min(confidence + 0.1, 0.99)
        
        if is_anomaly:
            reasoning = f"Statistical Anomaly Detected: The latest value deviates by {latest_z:.2f} standard deviations from the historical mean (Threshold: {self.z_threshold})."
        else:
            reasoning = f"Normal behavior: The latest value is within {latest_z:.2f} standard deviations."

        return bool(is_anomaly), float(severity), float(confidence), reasoning
