import copy
import json
import os
from typing import Any, Dict, Union

from .config import FAULT_RUNBOOK_MAPPING
from .llm import LLMFactory


class SelfHealer:
    """
    Matches diagnosed anomalies to self-healing runbooks and generates compliant action plans.
    Rule-based path uses FAULT_RUNBOOK_MAPPING in detect/src/config.py.
    """

    def __init__(self, runbooks_path: str):
        self.runbooks_path = runbooks_path
        self.runbooks: Dict[str, Any] = {}
        self.load_runbooks()

    def load_runbooks(self) -> None:
        if not os.path.exists(self.runbooks_path):
            self._try_seed_runbooks_from_catalog()
        if os.path.exists(self.runbooks_path):
            try:
                with open(self.runbooks_path, "r", encoding="utf-8") as f:
                    self.runbooks = json.load(f)
                print(f"Loaded {len(self.runbooks)} runbooks from {self.runbooks_path}")
                return
            except Exception as e:
                print(f"Warning: Failed to load runbooks from {self.runbooks_path}: {e}")
        print(f"Warning: Runbooks file not found at {self.runbooks_path}. Using fallback defaults.")
        self._load_fallback_runbooks()

    def _try_seed_runbooks_from_catalog(self) -> None:
        """Generate runbooks.json from detect/src/runbook_catalog.py when missing."""
        try:
            from .runbook_catalog import write_runbooks

            write_runbooks(self.runbooks_path)
            print(f"[OK] Seeded runbooks from runbook_catalog -> {self.runbooks_path}")
        except Exception as e:
            print(f"Warning: Could not seed runbooks from runbook_catalog: {e}")

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
                            "grace_period_seconds": 30,
                        },
                    }
                ],
                "blast_radius_config": {
                    "max_pod_impact_pct": 25,
                    "circuit_breaker_error_rate": 0.20,
                    "allowed_namespaces": ["production", "default"],
                },
                "verify_policy": {
                    "window_seconds": 120,
                    "success_conditions": ["pod_ready == true"],
                },
            }
        }

    def decide(self, anomaly_context: Union[Dict[str, Any], str], suspected_fault_type: str = None) -> Dict[str, Any]:
        """
        Select runbook and render action plan.
        Accepts full anomaly_context dict (preferred) or legacy (target_service, fault_type) args.
        """
        if isinstance(anomaly_context, dict):
            ctx = anomaly_context
            target_service = ctx.get("target_service")
            if isinstance(target_service, list):
                target_service = target_service[0] if target_service else "checkoutservice"
            fault_type = ctx.get("suspected_fault_type", "unknown")
            namespace = ctx.get("namespace", "production")
            deployment = ctx.get("deployment") or f"deployment/{target_service}"
        else:
            target_service = str(anomaly_context)
            fault_type = suspected_fault_type or "unknown"
            namespace = "production"
            deployment = f"deployment/{target_service}"
            ctx = {
                "target_service": target_service,
                "suspected_fault_type": fault_type,
                "namespace": namespace,
                "deployment": deployment,
            }

        use_llm = os.getenv("USE_LLM_DECISION", "False").lower() == "true"
        if use_llm:
            try:
                client = LLMFactory.get_client()
                prompt = self._format_prompt(target_service, fault_type)
                response_text = client.generate_decision(prompt)
                clean_json = response_text.strip()
                if clean_json.startswith("```json"):
                    clean_json = clean_json.split("```json", 1)[1].split("```", 1)[0].strip()
                elif clean_json.startswith("```"):
                    clean_json = clean_json.split("```", 1)[1].split("```", 1)[0].strip()
                decision = json.loads(clean_json)
                required_keys = [
                    "matched_runbook",
                    "pattern_type",
                    "action_plan",
                    "blast_radius_config",
                    "verify_policy",
                ]
                if all(k in decision for k in required_keys):
                    print(
                        f"  [LLM DECISION] Successfully generated action plan using LLM provider: {os.getenv('LLM_PROVIDER')}"
                    )
                    return decision
                print("  [LLM DECISION Warning] Generated JSON missing required keys. Falling back to rule-based.")
            except Exception as e:
                print(f"  [LLM DECISION Warning] LLM decide failed: {e}. Falling back to rule-based.")

        return self._decide_rule_based(ctx, target_service, fault_type, namespace, deployment)

    def _decide_rule_based(
        self,
        ctx: Dict[str, Any],
        target_service: str,
        fault_type: str,
        namespace: str,
        deployment: str,
    ) -> Dict[str, Any]:
        runbook_key = FAULT_RUNBOOK_MAPPING.get(fault_type, "DefaultRecoveryRunbook")
        runbook = self.runbooks.get(runbook_key) or self.runbooks.get("DefaultRecoveryRunbook")
        if not runbook:
            self._load_fallback_runbooks()
            runbook = self.runbooks.get(runbook_key, self.runbooks["DefaultRecoveryRunbook"])

        action_plan = []
        for step in runbook.get("action_plan", []):
            rendered = copy.deepcopy(step)
            rendered["target"] = (
                rendered.get("target", deployment)
                .replace("{{target_service}}", target_service)
                .replace("deployment/{{target_service}}", deployment)
            )
            if not rendered["target"].startswith("deployment/"):
                rendered["target"] = f"deployment/{target_service}"

            params = copy.deepcopy(rendered.get("params", {}))
            params.setdefault("namespace", namespace)
            if "secret_name" in params:
                params["secret_name"] = params["secret_name"].replace("{service}", target_service)
            rendered["params"] = params
            action_plan.append(rendered)

        blast = copy.deepcopy(
            runbook.get(
                "blast_radius_config",
                {
                    "max_pod_impact_pct": 25,
                    "circuit_breaker_error_rate": 0.20,
                    "allowed_namespaces": ["production", "default"],
                },
            )
        )
        if namespace not in blast.get("allowed_namespaces", []):
            blast.setdefault("allowed_namespaces", []).append(namespace)

        return {
            "matched_runbook": runbook.get("name", runbook_key),
            "pattern_type": runbook.get("pattern_type", "urgent"),
            "action_plan": action_plan,
            "blast_radius_config": blast,
            "verify_policy": copy.deepcopy(
                runbook.get("verify_policy", {"window_seconds": 120})
            ),
        }

    def _format_prompt(self, target_service: str, suspected_fault_type: str) -> str:
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
