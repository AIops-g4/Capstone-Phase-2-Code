import copy
import json
import os
from typing import Any, Dict

from src.config import FAULT_RUNBOOK_MAPPING, RUNBOOKS_PATH


class RuleBasedDecider:
    """Map anomaly_context to predefined runbooks (contract §3.2, rule-based path)."""

    def __init__(self, runbooks_path: str = RUNBOOKS_PATH):
        self.runbooks_path = runbooks_path
        self.runbooks: Dict[str, Any] = {}
        self._load_runbooks()

    def _load_runbooks(self) -> None:
        if not os.path.exists(self.runbooks_path):
            from src.runbook_catalog import write_runbooks

            write_runbooks(self.runbooks_path)
        if os.path.exists(self.runbooks_path):
            with open(self.runbooks_path, "r", encoding="utf-8") as f:
                self.runbooks = json.load(f)

    def decide(self, anomaly_context: dict) -> dict:
        fault = anomaly_context.get("suspected_fault_type", "unknown")
        target_service = anomaly_context["target_service"]
        namespace = anomaly_context.get("namespace", "production")
        deployment = anomaly_context.get("deployment") or f"deployment/{target_service}"

        runbook_key = FAULT_RUNBOOK_MAPPING.get(fault, "DefaultRecoveryRunbook")
        runbook = self.runbooks.get(runbook_key) or self.runbooks.get("DefaultRecoveryRunbook")
        if not runbook:
            runbook = _default_runbook_template()

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
                params["secret_name"] = params["secret_name"].replace(
                    "{service}", target_service
                )
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
            "cost_cap_exceeded": False,
        }


def _default_runbook_template() -> dict:
    return {
        "name": "DefaultRecoveryRunbook",
        "pattern_type": "urgent",
        "action_plan": [
            {
                "step": 1,
                "action": "RESTART_DEPLOYMENT",
                "target": "deployment/{{target_service}}",
                "params": {"namespace": "production", "grace_period_seconds": 30},
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
