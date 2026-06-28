"""
Component 3 — Blast Radius Config Generator.
Per AI API Contract §3.2: Generates blast_radius_config based on action type and pattern.
CDO Controller is responsible for enforcing these limits against real-time cluster state.
"""
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)

# Pattern-specific blast radius defaults
PATTERN_BLAST_RADIUS = {
    "RESTART_DEPLOYMENT": {
        "max_pod_impact_pct": 25,
        "circuit_breaker_error_rate": 0.20,
    },
    "PATCH_MEMORY_LIMIT": {
        "max_pod_impact_pct": 25,
        "circuit_breaker_error_rate": 0.20,
    },
    "SCALE_REPLICAS": {
        "max_pod_impact_pct": 50,  # Scaling affects more pods
        "circuit_breaker_error_rate": 0.15,
    },
    "ROLLOUT_UNDO": {
        "max_pod_impact_pct": 100,  # Rollback affects all pods
        "circuit_breaker_error_rate": 0.30,  # More tolerant during rollback
    },
    "ROTATE_SECRET": {
        "max_pod_impact_pct": 25,
        "circuit_breaker_error_rate": 0.10,  # Secret rotation should be safe
    },
}


class BlastRadiusGenerator:
    """
    Generates blast_radius_config for the /v1/decide response.
    Configuration is action-specific and returned to CDO for enforcement.
    """

    def generate(
        self,
        action_plan: List[Dict[str, Any]],
        allowed_namespaces: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Generates blast_radius_config based on the planned actions.
        Uses the most restrictive limits across all action steps.
        """
        if allowed_namespaces is None:
            allowed_namespaces = ["production"]

        max_impact = 25  # Default
        max_error_rate = 0.20  # Default

        for step in action_plan:
            action = step.get("action", "RESTART_DEPLOYMENT")
            config = PATTERN_BLAST_RADIUS.get(action, {})
            max_impact = max(max_impact, config.get("max_pod_impact_pct", 25))
            max_error_rate = max(max_error_rate, config.get("circuit_breaker_error_rate", 0.20))

        return {
            "max_pod_impact_pct": max_impact,
            "circuit_breaker_error_rate": max_error_rate,
            "allowed_namespaces": allowed_namespaces,
        }
