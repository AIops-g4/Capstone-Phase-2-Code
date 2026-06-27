from typing import Dict, Any

class SafetyGuardrails:
    """
    Component 3: Safety Validator
    Enforces deterministic blast radius constraints before the action plan is sent to CDO.
    Per AI API Contract §3.2 blast_radius_config and verify_policy.
    """
    def __init__(self):
        self.default_max_pod_impact_pct = 25
        self.default_circuit_breaker_error_rate = 0.20
        
    def generate_blast_radius(self, action_plan: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
        """
        Returns a BlastRadiusConfig based on the planned actions.
        """
        actions = [step.get("action") for step in action_plan.get("action_plan", [])]
        
        max_impact = self.default_max_pod_impact_pct
        if "ROLLOUT_UNDO" in actions:
            max_impact = 100  # Rollbacks usually affect all pods
            
        return {
            "max_pod_impact_pct": max_impact,
            "circuit_breaker_error_rate": self.default_circuit_breaker_error_rate,
            "allowed_namespaces": ["production"]
        }
        
    def generate_verify_policy(self) -> Dict[str, Any]:
        """
        Returns verify_policy matching demo output with 3 success conditions.
        """
        return {
            "window_seconds": 120,
            "success_conditions": [
                "pod_ready == true",
                "restart_count_no_increase == true",
                "container_memory_usage_pct < 80"
            ]
        }
