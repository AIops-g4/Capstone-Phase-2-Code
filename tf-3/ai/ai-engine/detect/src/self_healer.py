import os
import json
from typing import Dict, Any, List

from .llm import LLMFactory

class SelfHealer:
    """
    Matches diagnosed anomalies to self-healing runbooks and generates compliant action plans.
    Supports both rule-based deterministic matching and LLM-based intelligent decision generation.
    """
    def __init__(self, runbooks_path: str):
        self.runbooks_path = runbooks_path
        self.runbooks = {}
        self.load_runbooks()

    def load_runbooks(self) -> None:
        """
        Loads runbooks from the configured path.
        """
        if os.path.exists(self.runbooks_path):
            try:
                with open(self.runbooks_path, "r") as f:
                    self.runbooks = json.load(f)
                print(f"Loaded {len(self.runbooks)} runbooks from {self.runbooks_path}")
            except Exception as e:
                print(f"Warning: Failed to load runbooks from {self.runbooks_path}: {e}")
                self._load_fallback_runbooks()
        else:
            print(f"Warning: Runbooks file not found at {self.runbooks_path}. Using fallback defaults.")
            self._load_fallback_runbooks()

    def _load_fallback_runbooks(self) -> None:
        self.runbooks = {
            "DefaultRecoveryRunbook": {
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
            }
        }

    def decide(self, target_service: str, suspected_fault_type: str) -> Dict[str, Any]:
        """
        Selects the correct runbook and renders the templated action plan.
        Can run in LLM mode or fallback rule-based mode.
        """
        use_llm = os.getenv("USE_LLM_DECISION", "False").lower() == "true"
        
        # 1. LLM Decision Mode
        if use_llm:
            try:
                # Get client from factory
                client = LLMFactory.get_client()
                
                # Format prompt
                prompt = self._format_prompt(target_service, suspected_fault_type)
                
                # Generate decision
                response_text = client.generate_decision(prompt)
                
                # Clean up response to ensure clean JSON parsing
                clean_json = response_text.strip()
                if clean_json.startswith("```json"):
                    clean_json = clean_json.split("```json", 1)[1].split("```", 1)[0].strip()
                elif clean_json.startswith("```"):
                    clean_json = clean_json.split("```", 1)[1].split("```", 1)[0].strip()
                    
                decision = json.loads(clean_json)
                
                # Validate that all required keys are present
                required_keys = ["matched_runbook", "pattern_type", "action_plan", "blast_radius_config", "verify_policy"]
                if all(k in decision for k in required_keys):
                    print(f"  [LLM DECISION] Successfully generated action plan using LLM provider: {os.getenv('LLM_PROVIDER')}")
                    return decision
                else:
                    print("  [LLM DECISION Warning] Generated JSON missing required keys. Falling back to rule-based.")
            except Exception as e:
                print(f"  [LLM DECISION Warning] LLM decide failed: {e}. Falling back to rule-based.")
                
        # 2. Fallback / Deterministic Rule-Based Mode
        runbook_key = "DefaultRecoveryRunbook"
        if suspected_fault_type == "cpu":
            runbook_key = "CPUSaturationRecoveryRunbook"
            
        runbook = self.runbooks.get(runbook_key)
        
        if not runbook:
            self._load_fallback_runbooks()
            runbook = self.runbooks.get(runbook_key, self.runbooks["DefaultRecoveryRunbook"])
            
        # Render the templated values by replacing {{target_service}}
        rendered_action_plan = []
        for action in runbook["action_plan"]:
            rendered_action = json.loads(
                json.dumps(action).replace("{{target_service}}", target_service)
            )
            rendered_action_plan.append(rendered_action)
            
        decision = {
            "matched_runbook": runbook["name"],
            "pattern_type": runbook.get("pattern_type", "urgent"),
            "action_plan": rendered_action_plan,
            "blast_radius_config": runbook["blast_radius_config"],
            "verify_policy": runbook["verify_policy"]
        }
        
        return decision

    def _format_prompt(self, target_service: str, suspected_fault_type: str) -> str:
        """
        Formats prompt requesting LLM to output a compliant DecideResponse JSON object.
        """
        return f"""You are a senior Site Reliability Engineer (SRE) managing a microservices cluster.
An anomaly has been detected on:
- Target Service: {target_service}
- Suspected Fault: {suspected_fault_type}

Available runbooks templates:
{json.dumps(self.runbooks, indent=2)}

Please select or generate a recovery plan. You can use one of the templates above or design a customized plan.
Substitute any instances of '{{target_service}}' with the actual value: '{target_service}'.

You MUST respond with a single, valid JSON object containing exactly the following keys and matching types:
{{
  "matched_runbook": "Name of the runbook chosen (string)",
  "pattern_type": "urgent" or "deferred" (string),
  "action_plan": [
    {{
      "step": 1,
      "action": "RESTART_DEPLOYMENT" or "PATCH_MEMORY_LIMIT" or "SCALE_REPLICAS" or "ROLLOUT_UNDO" or "ROTATE_SECRET" (string),
      "target": "deployment/actual_service_name" (string),
      "params": {{
        "namespace": "production",
        "grace_period_seconds": 30
      }}
    }}
  ],
  "blast_radius_config": {{
    "max_pod_impact_pct": 25,
    "circuit_breaker_error_rate": 0.20,
    "allowed_namespaces": ["production", "default"]
  }},
  "verify_policy": {{
    "window_seconds": 120,
    "success_conditions": ["pod_ready == true"]
  }}
}}

Ensure there is no conversational text, no comments, and no markdown formatting in your response. Just return the raw JSON object.
"""

# Create alias
HealingEngine = SelfHealer
