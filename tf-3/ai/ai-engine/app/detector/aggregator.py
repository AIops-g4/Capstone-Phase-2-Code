"""
Component 1 — Aggregator: Routes signals to Rule Engine or RRCF Engine,
then aggregates scores to produce a final detection result.
Per design: Binary events → Rule Engine, Continuous metrics → RRCF Engine.
"""
from typing import List, Tuple, Optional
from app.schemas.common import TelemetryPoint, AnomalyContext
from app.detector.rule_engine import RuleEngine
from app.detector.rrcf_engine import RRCFEngine
import logging

logger = logging.getLogger(__name__)


class DetectionAggregator:
    """
    Orchestrates the two-layer detection pipeline:
      Layer 1 (Rule Engine): Binary event signals → immediate anomaly, high confidence
      Layer 2 (RRCF Engine): Continuous metric signals → anomaly score via CoDisp
    
    Aggregation logic:
      - Rule layer hits → immediate anomaly (confidence 0.95+)
      - RRCF high CoDisp → anomaly (confidence proportional to score)
      - Both layers agree → highest confidence
      - RRCF alone → moderate confidence
    """

    def __init__(self):
        self.rule_engine = RuleEngine()
        self.rrcf_engine = RRCFEngine()

    def analyze(
        self, telemetry: List[TelemetryPoint]
    ) -> Tuple[bool, float, float, str, Optional[AnomalyContext]]:
        """
        Runs both detection layers and aggregates results.
        
        Returns:
            (is_anomaly, severity, confidence, reasoning, anomaly_context)
        """
        if not telemetry:
            return False, 0.0, 0.0, "No telemetry data provided.", None

        # Run both layers
        rule_detections = self.rule_engine.analyze(telemetry)
        rrcf_detections = self.rrcf_engine.analyze(telemetry)

        all_detections = rule_detections + rrcf_detections

        if not all_detections:
            return (
                False,
                0.0,
                0.85,
                "All signals within normal parameters. No anomaly detected.",
                None,
            )

        # Pick the highest-severity detection as the primary
        primary = max(all_detections, key=lambda d: (d["severity"], d["confidence"]))

        # If both layers detected the same service, boost confidence
        rule_services = {d["trigger_point"].service for d in rule_detections}
        rrcf_services = {d["trigger_point"].service for d in rrcf_detections}
        overlapping = rule_services & rrcf_services

        confidence = primary["confidence"]
        if overlapping and primary["trigger_point"].service in overlapping:
            confidence = min(confidence + 0.05, 0.99)

        # Build AnomalyContext from the primary detection
        trigger_point = primary["trigger_point"]

        # Extract labels
        system = "UNKNOWN"
        namespace = None
        deployment = None
        if trigger_point.labels:
            system = trigger_point.labels.system or "UNKNOWN"
            namespace = trigger_point.labels.namespace
            deployment = trigger_point.labels.deployment

        # Get trigger value (numeric only)
        trigger_value = None
        try:
            trigger_value = float(trigger_point.value) if isinstance(
                trigger_point.value, (int, float)
            ) else float(trigger_point.value)
        except (ValueError, TypeError):
            pass

        anomaly_context = AnomalyContext(
            target_service=trigger_point.service,
            suspected_fault_type=primary["suspected_fault_type"],
            system=system,
            namespace=namespace,
            deployment=deployment,
            trigger_metric=trigger_point.signal_name,
            trigger_value=trigger_value,
        )

        # Combine reasoning from multiple detections if present
        reasoning = primary["reasoning"]
        other_count = len(all_detections) - 1
        if other_count > 0:
            reasoning = f"{reasoning} (+{other_count} correlated signals)"
        reasoning = reasoning[:300]  # Truncate to 300 chars per contract

        return (
            True,
            float(primary["severity"]),
            float(confidence),
            reasoning,
            anomaly_context,
        )
