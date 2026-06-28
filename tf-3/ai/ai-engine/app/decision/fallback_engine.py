"""
Component 2 — Fallback Decision Engine (Rule-Based, No LLM).
Per AI API Contract §4: Static decision tree for when LLM is unavailable.

Triggered by 4 conditions:
1. Cost cap exceeded ($50/day per tenant)
2. Bedrock rate-limited (429)
3. Bedrock timeout/downtime
4. LLM response parse failure
"""
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

# Static decision tree: suspected_fault_type → action, pattern_type, params
FALLBACK_DECISION_TREE = {
    "pod_oom_event": {
        "runbook": "FallbackRule::PodOOMKilledRunbook",
        "pattern_type": "urgent",
        "action": "PATCH_MEMORY_LIMIT",
        "default_params": {
            "memory_request_mb": 512,
            "memory_limit_mb": 1024,
        },
    },
    "pod_oom_killed": {
        "runbook": "FallbackRule::PodOOMKilledRunbook",
        "pattern_type": "urgent",
        "action": "PATCH_MEMORY_LIMIT",
        "default_params": {
            "memory_request_mb": 512,
            "memory_limit_mb": 1024,
        },
    },
    "memory_pressure": {
        "runbook": "FallbackRule::PodOOMKilledRunbook",
        "pattern_type": "urgent",
        "action": "PATCH_MEMORY_LIMIT",
        "default_params": {
            "memory_request_mb": 512,
            "memory_limit_mb": 1024,
        },
    },
    "service_unhealthy": {
        "runbook": "FallbackRule::ServiceUnhealthyRunbook",
        "pattern_type": "urgent",
        "action": "RESTART_DEPLOYMENT",
        "default_params": {
            "grace_period_seconds": 30,
        },
    },
    "service_health_check_failure": {
        "runbook": "FallbackRule::ServiceUnhealthyRunbook",
        "pattern_type": "urgent",
        "action": "RESTART_DEPLOYMENT",
        "default_params": {
            "grace_period_seconds": 30,
        },
    },
    "service_error_spike": {
        "runbook": "FallbackRule::ServiceUnhealthyRunbook",
        "pattern_type": "urgent",
        "action": "RESTART_DEPLOYMENT",
        "default_params": {
            "grace_period_seconds": 30,
        },
    },
    "queue_backlog": {
        "runbook": "FallbackRule::QueueBacklogRunbook",
        "pattern_type": "deferred",
        "action": "SCALE_REPLICAS",
        "default_params": {
            "replicas": 3,
        },
    },
    "queue_congestion": {
        "runbook": "FallbackRule::QueueBacklogRunbook",
        "pattern_type": "deferred",
        "action": "SCALE_REPLICAS",
        "default_params": {
            "replicas": 3,
        },
    },
    "secret_expiry_warning": {
        "runbook": "FallbackRule::CertExpiryRotationRunbook",
        "pattern_type": "deferred",
        "action": "ROTATE_SECRET",
        "default_params": {},
    },
    "certificate_expiring": {
        "runbook": "FallbackRule::CertExpiryRotationRunbook",
        "pattern_type": "deferred",
        "action": "ROTATE_SECRET",
        "default_params": {},
    },
    "db_connection_pool_saturation": {
        "runbook": "FallbackRule::DatabaseConnectionRecoveryRunbook",
        "pattern_type": "urgent",
        "action": "RESTART_DEPLOYMENT",
        "default_params": {
            "grace_period_seconds": 30,
        },
    },
    "database_connection_failure": {
        "runbook": "FallbackRule::DatabaseConnectionRecoveryRunbook",
        "pattern_type": "urgent",
        "action": "RESTART_DEPLOYMENT",
        "default_params": {
            "grace_period_seconds": 30,
        },
    },
    "crash_loop": {
        "runbook": "FallbackRule::ServiceUnhealthyRunbook",
        "pattern_type": "urgent",
        "action": "ROLLOUT_UNDO",
        "default_params": {},
    },
    "crash_loop_backoff": {
        "runbook": "FallbackRule::ServiceUnhealthyRunbook",
        "pattern_type": "urgent",
        "action": "ROLLOUT_UNDO",
        "default_params": {},
    },
}


class FallbackDecisionEngine:
    """
    Deterministic rule-based decision engine. 
    Maps suspected_fault_type directly to a runbook + action plan.
    Guaranteed p99 < 500ms (no external calls).
    """

    def decide(self, anomaly_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Returns a decision JSON matching the DecideResponse schema.
        If fault_type is unknown → ESCALATE.
        """
        fault_type = anomaly_context.get("suspected_fault_type", "unknown")
        target_service = anomaly_context.get(
            "deployment", anomaly_context.get("target_service", "unknown")
        )
        namespace = anomaly_context.get("namespace", "production")

        match = FALLBACK_DECISION_TREE.get(fault_type)

        if match is None:
            logger.warning(f"No fallback rule for fault_type='{fault_type}'. Escalating.")
            return {
                "matched_runbook": "FallbackRule::ESCALATE",
                "pattern_type": "urgent",
                "action_plan": [],
                "next_action": "ESCALATE",
            }

        # Build params with namespace always included
        params = {"namespace": namespace}
        params.update(match["default_params"])

        # For ROTATE_SECRET, add secret_name from anomaly context
        if match["action"] == "ROTATE_SECRET":
            params["secret_name"] = f"tf-3/{target_service}/cert"

        return {
            "matched_runbook": match["runbook"],
            "pattern_type": match["pattern_type"],
            "action_plan": [
                {
                    "step": 1,
                    "action": match["action"],
                    "target": f"deployment/{target_service}",
                    "params": params,
                }
            ],
        }
