"""
Component 4 — Verification Evaluator: Pattern-specific success condition evaluation.
Per design: Each pattern defines specific success_conditions evaluated against post_telemetry_window.
"""
from typing import List, Dict, Tuple
from app.schemas.common import TelemetryPoint
import logging

logger = logging.getLogger(__name__)

# Pattern-specific success conditions and their evaluation logic
# Maps suspected_fault_type → list of (condition_name, signal_name, evaluator_fn)
PATTERN_SUCCESS_CONDITIONS = {
    "pod_oom_event": {
        "conditions": ["pod_ready == true", "restart_count_no_increase == true", "container_memory_usage_pct < 80"],
        "checks": {
            "pod_oom_event": lambda v: False,  # Presence of OOM = regression
            "service_unhealthy": lambda v: False,  # Presence = not ready
            "container_restart_count": lambda v: float(v) <= 3,
            "container_resource_usage": lambda v: float(v) < 0.80 if isinstance(v, (int, float)) or float(v) <= 1.0 else True,
        },
    },
    "pod_oom_killed": {
        "conditions": ["pod_ready == true", "restart_count_no_increase == true", "container_memory_usage_pct < 80"],
        "checks": {
            "pod_oom_event": lambda v: False,
            "service_unhealthy": lambda v: False,
            "container_restart_count": lambda v: float(v) <= 3,
            "container_resource_usage": lambda v: float(v) < 0.80 if float(v) <= 1.0 else True,
        },
    },
    "service_unhealthy": {
        "conditions": ["pod_ready == true", "restart_count_no_increase == true", "service_error_rate < 0.05"],
        "checks": {
            "service_unhealthy": lambda v: False,
            "container_restart_count": lambda v: float(v) <= 3,
            "service_error_rate": lambda v: float(v) < 0.05,
        },
    },
    "service_health_check_failure": {
        "conditions": ["pod_ready == true", "restart_count_no_increase == true", "service_error_rate < 0.05"],
        "checks": {
            "service_unhealthy": lambda v: False,
            "container_restart_count": lambda v: float(v) <= 3,
            "service_error_rate": lambda v: float(v) < 0.05,
        },
    },
    "queue_backlog": {
        "conditions": ["queue_depth_decreasing == true", "worker_pods_ready == true"],
        "checks": {
            "queue_backlog": lambda v: float(v) < 5000,
            "service_unhealthy": lambda v: False,
        },
    },
    "queue_congestion": {
        "conditions": ["queue_depth_decreasing == true", "worker_pods_ready == true"],
        "checks": {
            "queue_backlog": lambda v: float(v) < 5000,
            "service_unhealthy": lambda v: False,
        },
    },
    "secret_expiry_warning": {
        "conditions": ["secret_rotated == true", "service_healthy == true"],
        "checks": {
            "secret_expiry_warning": lambda v: float(v) > 30,  # Days until expiry increased
            "service_unhealthy": lambda v: False,
            "service_error_rate": lambda v: float(v) < 0.05,
        },
    },
    "certificate_expiring": {
        "conditions": ["secret_rotated == true", "service_healthy == true"],
        "checks": {
            "secret_expiry_warning": lambda v: float(v) > 30,
            "service_unhealthy": lambda v: False,
            "service_error_rate": lambda v: float(v) < 0.05,
        },
    },
    "db_connection_pool_saturation": {
        "conditions": ["pool_saturation < 0.80", "service_error_rate < 0.05"],
        "checks": {
            "db_connection_pool_saturation": lambda v: float(v) < 0.80,
            "service_error_rate": lambda v: float(v) < 0.05,
        },
    },
    "database_connection_failure": {
        "conditions": ["pool_saturation < 0.80", "service_error_rate < 0.05"],
        "checks": {
            "db_connection_pool_saturation": lambda v: float(v) < 0.80,
            "service_error_rate": lambda v: float(v) < 0.05,
        },
    },
    "crash_loop": {
        "conditions": ["pod_ready == true", "restart_count_no_increase == true"],
        "checks": {
            "service_unhealthy": lambda v: False,
            "container_restart_count": lambda v: float(v) <= 1,
        },
    },
    "crash_loop_backoff": {
        "conditions": ["pod_ready == true", "restart_count_no_increase == true"],
        "checks": {
            "service_unhealthy": lambda v: False,
            "container_restart_count": lambda v: float(v) <= 1,
        },
    },

}

# Generic fallback checks for unknown fault types
GENERIC_CHECKS = {
    "service_error_rate": lambda v: float(v) < 0.05,
    "service_unhealthy": lambda v: False,
    "pod_oom_event": lambda v: False,
    "container_restart_count": lambda v: float(v) <= 3,
    "service_latency_p95": lambda v: float(v) < 500.0,
}


class VerificationEvaluator:
    """
    Evaluates post-action telemetry against pattern-specific success conditions.
    Returns (success, regression_detected, error_logs, metrics_summary).
    """

    def evaluate(
        self,
        fault_type: str,
        post_telemetry: List[TelemetryPoint],
    ) -> Tuple[bool, bool, List[str], Dict]:
        """
        Evaluates post-telemetry against success conditions for the given fault type.
        
        Returns:
            (success, regression_detected, error_logs, metrics_summary)
        """
        pattern_config = PATTERN_SUCCESS_CONDITIONS.get(fault_type)
        checks = pattern_config["checks"] if pattern_config else GENERIC_CHECKS

        has_regression = False
        still_unhealthy = False
        error_logs = []
        metrics_summary = {}

        for point in post_telemetry:
            signal = point.signal_name
            check_fn = checks.get(signal)

            if check_fn is None:
                continue

            try:
                is_healthy = check_fn(point.value)
            except (ValueError, TypeError):
                continue

            # Store metric value
            try:
                metrics_summary[f"post_{signal}"] = float(point.value)
            except (ValueError, TypeError):
                metrics_summary[f"post_{signal}"] = str(point.value)

            if not is_healthy:
                # Determine if this is a regression or just not recovered
                if signal in ("pod_oom_event", "container_restart_count", "service_latency_p95"):
                    has_regression = True
                    error_logs.append(
                        f"Regression: {signal}={point.value} on {point.service}"
                    )
                else:
                    still_unhealthy = True
                    error_logs.append(
                        f"Not recovered: {signal}={point.value} on {point.service}"
                    )

        success = not has_regression and not still_unhealthy
        return success, has_regression, error_logs, metrics_summary
