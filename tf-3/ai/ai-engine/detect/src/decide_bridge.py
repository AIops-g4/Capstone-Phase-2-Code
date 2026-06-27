"""
Bridge detect /v1/decide to the full decide-engine rule-based logic (FAULT_RUNBOOK_MAPPING).
Replaces the lite SelfHealer mapping (cpu-only + default).
"""
import copy
import importlib.util
import json
import os
from typing import Any, Dict

_DETECT_SRC = os.path.dirname(os.path.abspath(__file__))
_DETECT_DIR = os.path.dirname(_DETECT_SRC)
_AI_ENGINE_DIR = os.path.dirname(_DETECT_DIR)
_DECIDE_SRC = os.path.join(_AI_ENGINE_DIR, "decide", "src")


def _load_decide_module(module_name: str):
    path = os.path.join(_DECIDE_SRC, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(f"decide_{module_name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_decide_config = _load_decide_module("config")
FAULT_RUNBOOK_MAPPING = _decide_config.FAULT_RUNBOOK_MAPPING


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


class IntegratedDecideEngine:
    """Full rule-based decide (same logic as decide/src/rule_decider.py)."""

    def __init__(self, runbooks_path: str):
        self.runbooks_path = runbooks_path
        self.runbooks: Dict[str, Any] = {}
        self._load_runbooks()

    def _load_runbooks(self) -> None:
        if not os.path.exists(self.runbooks_path):
            catalog = _load_decide_module("runbook_catalog")
            catalog.write_runbooks(self.runbooks_path)
        if os.path.exists(self.runbooks_path):
            with open(self.runbooks_path, "r", encoding="utf-8") as f:
                self.runbooks = json.load(f)
            print(f"Loaded {len(self.runbooks)} runbooks from {self.runbooks_path}")

    def decide(self, target_service: str, suspected_fault_type: str) -> Dict[str, Any]:
        ctx = {
            "target_service": target_service,
            "suspected_fault_type": suspected_fault_type,
            "system": "E-COMMERCE",
            "namespace": "production",
            "deployment": f"deployment/{target_service}",
        }
        return self.decide_from_context(ctx)

    def decide_from_context(self, anomaly_context: dict) -> Dict[str, Any]:
        fault = anomaly_context.get("suspected_fault_type", "unknown")
        target_service = anomaly_context["target_service"]
        if isinstance(target_service, list):
            target_service = target_service[0] if target_service else "unknown"
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
        }


# Alias used by engine.py (replaces lite HealingEngine / SelfHealer)
HealingEngine = IntegratedDecideEngine
