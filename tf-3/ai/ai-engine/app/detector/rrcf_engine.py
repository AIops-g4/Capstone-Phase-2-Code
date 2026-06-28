"""
Component 1 — Layer 2: RRCF (Robust Random Cut Forest) Engine for continuous metric signals.
Scores anomalies using Collusive Displacement (CoDisp) on streaming data.
Per design: Handles service_error_rate, service_latency_p95, container_resource_usage,
            queue_backlog, db_connection_pool_saturation, service_throughput_rps.
"""
import rrcf
import numpy as np
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from app.schemas.common import TelemetryPoint
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# Continuous metric signals handled by RRCF
CONTINUOUS_SIGNALS = {
    "service_error_rate",
    "service_latency_p95",
    "container_resource_usage",
    "queue_backlog",
    "db_connection_pool_saturation",
    "service_throughput_rps",
}

# Signal-to-fault-type mapping for continuous metrics
SIGNAL_FAULT_MAP = {
    "service_error_rate": "service_error_spike",
    "service_latency_p95": "latency_degradation",
    "container_resource_usage": "memory_pressure",
    "queue_backlog": "queue_backlog",
    "db_connection_pool_saturation": "db_connection_pool_saturation",
    "service_throughput_rps": "throughput_anomaly",
}

# Default thresholds for CoDisp scores per signal type
DEFAULT_CODISP_THRESHOLDS = {
    "service_error_rate": 5.0,
    "service_latency_p95": 5.0,
    "container_resource_usage": 5.0,
    "queue_backlog": 5.0,
    "db_connection_pool_saturation": 5.0,
    "service_throughput_rps": 5.0,
}


class RRCFEngine:
    """
    RRCF-based anomaly detection for continuous metric signals.
    Maintains a forest of random cut trees per signal key (service + signal_name).
    Uses Collusive Displacement (CoDisp) to score anomalies.
    
    Streaming-friendly: insert new points, forget old points (sliding window).
    No batch retraining needed — avoids "Auto-retrain ML" out-of-scope constraint.
    """

    def __init__(
        self,
        num_trees: int = None,
        tree_size: int = None,
        shingle_size: int = None,
    ):
        self.num_trees = num_trees or settings.RRCF_NUM_TREES
        self.tree_size = tree_size or settings.RRCF_TREE_SIZE
        self.shingle_size = shingle_size or settings.RRCF_SHINGLE_SIZE

        # Per-signal-key forests: key = (service, signal_name)
        self._forests: Dict[str, List[rrcf.RCTree]] = defaultdict(self._create_forest)
        # Per-signal-key point index counter
        self._indices: Dict[str, int] = defaultdict(int)

    def _create_forest(self) -> List[rrcf.RCTree]:
        """Creates a new RRCF forest with empty trees."""
        return [rrcf.RCTree() for _ in range(self.num_trees)]

    def _get_forest_key(self, service: str, signal_name: str) -> str:
        """Returns a unique key for a (service, signal) pair."""
        return f"{service}::{signal_name}"

    def analyze(self, telemetry: List[TelemetryPoint]) -> List[dict]:
        """
        Evaluates continuous metric telemetry points using RRCF anomaly scoring.
        Returns a list of detected anomalies.
        
        Each anomaly dict contains:
        - is_anomaly: bool
        - severity: float (0.0 - 1.0)
        - confidence: float (0.0 - 1.0)
        - reasoning: str (max 300 chars)
        - suspected_fault_type: str
        - trigger_point: TelemetryPoint
        - codisp_score: float (raw CoDisp score for observability)
        """
        detections = []

        # Filter to continuous signals only
        continuous_points = [p for p in telemetry if p.signal_name in CONTINUOUS_SIGNALS]
        if not continuous_points:
            return detections

        for point in continuous_points:
            try:
                value = float(point.value)
            except (ValueError, TypeError):
                continue  # Skip non-numeric values

            forest_key = self._get_forest_key(point.service, point.signal_name)
            forest = self._forests[forest_key]
            idx = self._indices[forest_key]

            # Insert point into each tree and compute CoDisp
            codisp_scores = []
            for tree in forest:
                # If tree is at capacity, forget oldest point
                if len(tree.leaves) >= self.tree_size:
                    oldest = min(tree.leaves.keys())
                    tree.forget_point(oldest)

                # Insert new point (1D for single-signal)
                tree.insert_point(np.array([value]), index=idx)

                # Compute Collusive Displacement
                try:
                    codisp = tree.codisp(idx)
                    codisp_scores.append(codisp)
                except Exception:
                    codisp_scores.append(0.0)

            self._indices[forest_key] = idx + 1

            # Average CoDisp across all trees
            avg_codisp = float(np.mean(codisp_scores)) if codisp_scores else 0.0

            # Check against threshold
            threshold = DEFAULT_CODISP_THRESHOLDS.get(point.signal_name, 5.0)
            is_anomaly = avg_codisp > threshold

            if is_anomaly:
                # Map CoDisp to severity (0.0 - 1.0)
                # CoDisp at threshold → 0.5, CoDisp at 2x threshold → 1.0
                severity = min(avg_codisp / (threshold * 2), 1.0)

                # Confidence scales with number of data points in forest
                total_points = len(forest[0].leaves) if forest else 0
                confidence = min(total_points / self.tree_size * 0.9, 0.90)
                if is_anomaly:
                    confidence = min(confidence + 0.05, 0.95)

                fault_type = SIGNAL_FAULT_MAP.get(point.signal_name, "statistical_anomaly")
                reasoning = (
                    f"RRCF anomaly on {point.service}/{point.signal_name}: "
                    f"CoDisp={avg_codisp:.2f} exceeds threshold {threshold:.1f}. "
                    f"Value={value}."
                )[:300]

                detections.append({
                    "is_anomaly": True,
                    "severity": float(severity),
                    "confidence": float(confidence),
                    "reasoning": reasoning,
                    "suspected_fault_type": fault_type,
                    "trigger_point": point,
                    "codisp_score": avg_codisp,
                })

        return detections
