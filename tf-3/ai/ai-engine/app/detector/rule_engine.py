"""
Component 1 — Layer 1: Rule-Based Engine for binary event signals.
Handles discrete events that are inherently anomalous when present.
Per design: pod_oom_event, service_unhealthy, secret_expiry_warning, container_restart_count.
"""
from typing import List, Tuple, Optional
from app.schemas.common import TelemetryPoint
import logging

logger = logging.getLogger(__name__)

# Binary event signals and their detection rules
BINARY_EVENT_RULES = {
    "pod_oom_event": {
        "fault_type": "pod_oom_event",
        "severity": 0.90,
        "confidence": 0.95,
        "reasoning_template": "OOMKilled event detected on {service}. Container exceeded memory limit.",
    },
    "service_unhealthy": {
        "fault_type": "service_unhealthy",
        "severity": 0.85,
        "confidence": 0.95,
        "reasoning_template": "Service {service} failed liveness/readiness probe.",
    },
    "secret_expiry_warning": {
        "fault_type": "secret_expiry_warning",
        "severity": 0.70,
        "confidence": 0.95,
        "reasoning_template": "Certificate/secret for {service} expiring in {value} days.",
        "value_threshold": 7,  # Anomaly only if value <= 7 days
    },
    "container_restart_count": {
        "fault_type": "crash_loop",
        "severity": 0.80,
        "confidence": 0.90,
        "reasoning_template": "Container restart count for {service} reached {value} (threshold: 5).",
        "value_threshold": 5,  # Anomaly only if value >= 5
        "threshold_direction": "gte",  # greater than or equal
    },
}


class RuleEngine:
    """
    Rule-based anomaly detection for binary/event signals.
    Returns immediate anomaly decisions with high confidence (0.95+).
    """

    def analyze(self, telemetry: List[TelemetryPoint]) -> List[dict]:
        """
        Evaluates each telemetry point against binary event rules.
        Returns a list of detected anomalies (may be empty).
        
        Each anomaly dict contains:
        - is_anomaly: bool
        - severity: float (0.0 - 1.0)
        - confidence: float (0.0 - 1.0)
        - reasoning: str (max 300 chars)
        - suspected_fault_type: str
        - trigger_point: TelemetryPoint
        """
        detections = []

        for point in telemetry:
            rule = BINARY_EVENT_RULES.get(point.signal_name)
            if rule is None:
                continue  # Not a binary event signal — skip to RRCF layer

            is_anomaly = True

            # Some binary signals have a value threshold
            if "value_threshold" in rule:
                try:
                    val = float(point.value)
                    direction = rule.get("threshold_direction", "lte")
                    if direction == "lte":
                        is_anomaly = val <= rule["value_threshold"]
                    elif direction == "gte":
                        is_anomaly = val >= rule["value_threshold"]
                except (ValueError, TypeError):
                    # Non-numeric value for threshold signal — treat as anomaly if event present
                    is_anomaly = True

            if is_anomaly:
                reasoning = rule["reasoning_template"].format(
                    service=point.service,
                    value=point.value,
                )
                # Truncate reasoning to 300 chars per contract
                reasoning = reasoning[:300]

                detections.append({
                    "is_anomaly": True,
                    "severity": rule["severity"],
                    "confidence": rule["confidence"],
                    "reasoning": reasoning,
                    "suspected_fault_type": rule["fault_type"],
                    "trigger_point": point,
                })

        return detections
