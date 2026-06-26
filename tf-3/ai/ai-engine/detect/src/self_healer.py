import os
import json

class SelfHealer:
    """
    Matches diagnosed anomalies to self-healing runbooks and generates compliant action plans.
    """
    def __init__(self, runbooks_path: str):
        self.runbooks_path = runbooks_path
        self.runbooks = {}
        self.load_runbooks()

    def load_runbooks(self):
        if os.path.exists(self.runbooks_path):
            with open(self.runbooks_path, "r") as f:
                self.runbooks = json.load(f)
            print(f"Loaded {len(self.runbooks)} runbooks from {self.runbooks_path}")
        else:
            print(f"Warning: Runbooks file not found at {self.runbooks_path}")

    def decide(self, target_service: str, suspected_fault_type: str):
        """
        Selects the correct runbook and renders the templated action plan.
        """
        # Always use the DefaultRecoveryRunbook directly (no fault-based selection)
        runbook_key = "DefaultRecoveryRunbook"
        runbook = self.runbooks.get(runbook_key)
        
        # Fallback if specific runbook not found
        if not runbook:
            runbook = self.runbooks.get("DefaultRecoveryRunbook", {
                "name": "DefaultRecoveryRunbook",
                "description": "Default fallback runbook that restarts the anomalous deployment.",
                "pattern_type": "urgent",
                "action_plan": [
                    {
                        "step": 1,
                        "action": "RESTART_DEPLOYMENT",
                        "target": "deployment/{{target_service}}",
                        "params": {
                            "namespace": "production",
                            "grace_period_seconds": 30
                        }
                    }
                ],
                "blast_radius_config": {
                    "max_pod_impact_pct": 25,
                    "circuit_breaker_error_rate": 0.20,
                    "allowed_namespaces": ["production", "default"]
                },
                "verify_policy": {
                    "window_seconds": 120,
                    "success_conditions": ["pod_ready == true"]
                }
            })
            
        # 2. Render the templated values by replacing {{target_service}}
        rendered_action_plan = []
        for action in runbook["action_plan"]:
            rendered_action = json.loads(
                json.dumps(action).replace("{{target_service}}", target_service)
            )
            rendered_action_plan.append(rendered_action)
            
        # 3. Compile output according to the DecideResponse contract
        decision = {
            "matched_runbook": runbook["name"],
            "pattern_type": runbook.get("pattern_type", "urgent"),
            "action_plan": rendered_action_plan,
            "blast_radius_config": runbook["blast_radius_config"],
            "verify_policy": runbook["verify_policy"]
        }
        
        return decision
