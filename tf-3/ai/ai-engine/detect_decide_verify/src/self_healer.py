import copy
import json
import os
import re
from typing import Any, Dict, List, Union

from .config import (
    ALLOWED_NAMESPACES,
    DEPENDENCY_GRAPH,
    DEFAULT_DEPLOYMENT_TEMPLATE,
    DEFAULT_NAMESPACE,
    DEFAULT_SERVICE,
    FAULT_RUNBOOK_MAPPING,
    PLATFORM_PROFILE,
    SERVICES_LIST,
    SYSTEM_NAME,
)
from .llm import LLMFactory


class LLMDecisionOutputParser:
    """
    Small LangChain-style structured output parser for LLM decisions.

    It extracts one JSON object, validates the required schema, restricts enum
    values, and rejects hallucinated runbooks/actions/targets before the result
    can reach the API response. Invalid output raises ValueError so the caller
    can safely fall back to deterministic rule-based decisions.
    """

    REQUIRED_TOP_LEVEL_KEYS = {
        "detect_assessment",
        "corrected_anomaly_context",
        "matched_runbook",
        "pattern_type",
        "action_plan",
        "blast_radius_config",
        "verify_policy",
    }
    ALLOWED_PATTERN_TYPES = {"urgent", "deferred"}
    ALLOWED_ACTIONS = {
        "RESTART_DEPLOYMENT",
        "PATCH_MEMORY_LIMIT",
        "SCALE_REPLICAS",
        "ROLLOUT_UNDO",
        "ROTATE_SECRET",
    }

    def __init__(self, runbooks: Dict[str, Any]):
        self.runbooks = runbooks

    def format_instructions(self) -> str:
        allowed_runbooks = sorted(self.runbooks.keys())
        allowed_actions = sorted(self.ALLOWED_ACTIONS)
        return (
            "Return exactly one raw JSON object with no markdown, no comments, and no extra text. "
            "The object must contain only these top-level keys: "
            f"{sorted(self.REQUIRED_TOP_LEVEL_KEYS)}. "
            "detect_assessment must include is_detect_output_plausible(boolean) and assessment_reason(string). "
            "corrected_anomaly_context must include target_service and suspected_fault_type after reviewing the detect output. "
            f"corrected_anomaly_context.target_service must be one of: {SERVICES_LIST}. "
            f"corrected_anomaly_context.suspected_fault_type must be one of: {sorted(FAULT_RUNBOOK_MAPPING.keys())}. "
            f"matched_runbook must be one of: {allowed_runbooks}. "
            "matched_runbook must align with fault_runbook_mapping for the corrected fault type. "
            "pattern_type must be either 'urgent' or 'deferred'. "
            f"Every action_plan[].action must be one of: {allowed_actions}. "
            f"Every action_plan[].target must follow this template: {DEFAULT_DEPLOYMENT_TEMPLATE}. "
            "Do not invent services, namespaces, actions, or runbook names."
        )

    def parse(
        self,
        response_text: str,
        fallback_target_service: str,
        fallback_namespace: str,
        fallback_deployment: str,
    ) -> Dict[str, Any]:
        raw_json = self._extract_json_object(response_text)
        decision = json.loads(raw_json)
        self._validate_top_level(decision)
        corrected = self._validate_corrected_context(
            decision,
            fallback_target_service,
            fallback_namespace,
            fallback_deployment,
        )
        self._validate_detect_assessment(decision)
        self._validate_runbook(decision, corrected["suspected_fault_type"])
        self._validate_action_plan(
            decision,
            corrected["target_service"],
            corrected["namespace"],
            corrected["deployment"],
        )
        self._validate_blast_radius(decision, corrected["namespace"])
        self._validate_verify_policy(decision)
        return decision

    def _extract_json_object(self, response_text: str) -> str:
        clean = response_text.strip()
        if clean.startswith("```json"):
            clean = clean.split("```json", 1)[1].split("```", 1)[0].strip()
        elif clean.startswith("```"):
            clean = clean.split("```", 1)[1].split("```", 1)[0].strip()

        if clean.startswith("{") and clean.endswith("}"):
            return clean

        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            raise ValueError("LLM response does not contain a JSON object")
        return match.group(0)

    def _validate_top_level(self, decision: Dict[str, Any]) -> None:
        if not isinstance(decision, dict):
            raise ValueError("LLM decision must be a JSON object")
        keys = set(decision.keys())
        missing = self.REQUIRED_TOP_LEVEL_KEYS - keys
        extra = keys - self.REQUIRED_TOP_LEVEL_KEYS
        if missing:
            raise ValueError(f"LLM decision missing required keys: {sorted(missing)}")
        if extra:
            raise ValueError(f"LLM decision contains unsupported keys: {sorted(extra)}")
        if decision["pattern_type"] not in self.ALLOWED_PATTERN_TYPES:
            raise ValueError("LLM decision pattern_type is invalid")

    def _validate_detect_assessment(self, decision: Dict[str, Any]) -> None:
        assessment = decision.get("detect_assessment")
        if not isinstance(assessment, dict):
            raise ValueError("detect_assessment must be an object")
        if not isinstance(assessment.get("is_detect_output_plausible"), bool):
            raise ValueError("detect_assessment.is_detect_output_plausible must be boolean")
        if not isinstance(assessment.get("assessment_reason"), str) or not assessment["assessment_reason"].strip():
            raise ValueError("detect_assessment.assessment_reason must be a non-empty string")

    def _validate_corrected_context(
        self,
        decision: Dict[str, Any],
        fallback_target_service: str,
        fallback_namespace: str,
        fallback_deployment: str,
    ) -> Dict[str, Any]:
        corrected = decision.get("corrected_anomaly_context")
        if not isinstance(corrected, dict):
            raise ValueError("corrected_anomaly_context must be an object")

        target_service = corrected.get("target_service") or fallback_target_service
        fault_type = corrected.get("suspected_fault_type")
        namespace = corrected.get("namespace") or fallback_namespace
        deployment = corrected.get("deployment") or DEFAULT_DEPLOYMENT_TEMPLATE.replace(
            "{{target_service}}", target_service
        )

        if target_service not in SERVICES_LIST:
            raise ValueError(f"LLM corrected target_service is not in platform profile: {target_service}")
        if fault_type not in FAULT_RUNBOOK_MAPPING:
            raise ValueError(f"LLM corrected unsupported fault type: {fault_type}")
        if namespace not in ALLOWED_NAMESPACES:
            raise ValueError(f"LLM corrected namespace is not allowed: {namespace}")

        allowed_deployments = {
            fallback_deployment,
            DEFAULT_DEPLOYMENT_TEMPLATE.replace("{{target_service}}", target_service),
            f"deployment/{target_service}",
        }
        if deployment not in allowed_deployments:
            raise ValueError(f"LLM corrected unsupported deployment: {deployment}")

        corrected.setdefault("system", SYSTEM_NAME)
        corrected["target_service"] = target_service
        corrected["suspected_fault_type"] = fault_type
        corrected["namespace"] = namespace
        corrected["deployment"] = deployment
        return corrected

    def _validate_runbook(self, decision: Dict[str, Any], corrected_fault_type: str) -> None:
        matched_runbook = decision.get("matched_runbook")
        valid_names = {key for key in self.runbooks}
        valid_names.update(
            runbook.get("name")
            for runbook in self.runbooks.values()
            if isinstance(runbook, dict) and runbook.get("name")
        )
        if matched_runbook not in valid_names:
            raise ValueError(f"LLM hallucinated unsupported runbook: {matched_runbook}")

        expected_key = FAULT_RUNBOOK_MAPPING.get(corrected_fault_type, "DefaultRecoveryRunbook")
        expected = self.runbooks.get(expected_key, {})
        expected_names = {expected_key}
        if isinstance(expected, dict) and expected.get("name"):
            expected_names.add(expected["name"])
        if matched_runbook not in expected_names:
            raise ValueError(
                f"LLM runbook {matched_runbook} does not match corrected fault {corrected_fault_type}"
            )

    def _validate_action_plan(
        self,
        decision: Dict[str, Any],
        target_service: str,
        namespace: str,
        deployment: str,
    ) -> None:
        action_plan = decision.get("action_plan")
        if not isinstance(action_plan, list) or not action_plan:
            raise ValueError("LLM decision action_plan must be a non-empty list")

        allowed_targets = {deployment, f"deployment/{target_service}"}
        for idx, step in enumerate(action_plan, start=1):
            if not isinstance(step, dict):
                raise ValueError("Each action_plan step must be an object")
            required = {"step", "action", "target", "params"}
            if not required.issubset(step.keys()):
                raise ValueError(f"Action step {idx} missing required fields")
            if step["action"] not in self.ALLOWED_ACTIONS:
                raise ValueError(f"Unsupported LLM action: {step['action']}")
            if step["target"] not in allowed_targets:
                raise ValueError(f"Unsupported LLM target: {step['target']}")
            if not isinstance(step["params"], dict):
                raise ValueError("Action params must be an object")
            step["params"].setdefault("namespace", namespace)
            if step["params"].get("namespace") != namespace:
                raise ValueError("LLM attempted to use an unexpected namespace")

    def _validate_blast_radius(self, decision: Dict[str, Any], namespace: str) -> None:
        blast = decision.get("blast_radius_config")
        if not isinstance(blast, dict):
            raise ValueError("blast_radius_config must be an object")
        required = {"max_pod_impact_pct", "circuit_breaker_error_rate", "allowed_namespaces"}
        if not required.issubset(blast.keys()):
            raise ValueError("blast_radius_config missing required fields")
        if not isinstance(blast["allowed_namespaces"], list):
            raise ValueError("allowed_namespaces must be a list")
        if namespace not in blast["allowed_namespaces"]:
            raise ValueError("LLM omitted the requested namespace from allowed_namespaces")

    def _validate_verify_policy(self, decision: Dict[str, Any]) -> None:
        verify_policy = decision.get("verify_policy")
        if not isinstance(verify_policy, dict):
            raise ValueError("verify_policy must be an object")
        if "window_seconds" not in verify_policy:
            raise ValueError("verify_policy.window_seconds is required")
        if not isinstance(verify_policy["window_seconds"], int):
            raise ValueError("verify_policy.window_seconds must be an integer")


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
        profile_runbooks = PLATFORM_PROFILE.get("runbooks")
        if isinstance(profile_runbooks, dict) and profile_runbooks:
            self.runbooks = profile_runbooks
            print(f"Loaded {len(self.runbooks)} runbooks from platform profile")
            return
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
                        "target": DEFAULT_DEPLOYMENT_TEMPLATE,
                        "params": {
                            "namespace": DEFAULT_NAMESPACE,
                            "grace_period_seconds": 30,
                        },
                    }
                ],
                "blast_radius_config": {
                    "max_pod_impact_pct": 25,
                    "circuit_breaker_error_rate": 0.20,
                    "allowed_namespaces": ALLOWED_NAMESPACES,
                },
                "verify_policy": {
                    "window_seconds": 120,
                    "success_conditions": ["pod_ready == true"],
                },
            }
        }

    def decide(
        self,
        anomaly_context: Union[Dict[str, Any], str],
        suspected_fault_type: str = None,
        detect_evidence: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Select runbook and render action plan.
        Accepts full anomaly_context dict (preferred) or legacy (target_service, fault_type) args.
        """
        if isinstance(anomaly_context, dict):
            ctx = anomaly_context
            target_service = ctx.get("target_service")
            if isinstance(target_service, list):
                target_service = target_service[0] if target_service else DEFAULT_SERVICE
            fault_type = ctx.get("suspected_fault_type", "unknown")
            namespace = ctx.get("namespace", DEFAULT_NAMESPACE)
            deployment = ctx.get("deployment") or self._render_deployment(target_service)
        else:
            target_service = str(anomaly_context)
            fault_type = suspected_fault_type or "unknown"
            namespace = DEFAULT_NAMESPACE
            deployment = self._render_deployment(target_service)
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
                parser = LLMDecisionOutputParser(self.runbooks)
                prompt = self._format_prompt(ctx, detect_evidence or {}, parser)
                response_text = client.generate_decision(prompt)
                decision = parser.parse(response_text, target_service, namespace, deployment)
                print(
                    f"  [LLM DECISION] Successfully generated validated action plan using LLM provider: {os.getenv('LLM_PROVIDER')}"
                )
                return decision
            except Exception as e:
                print(f"  [LLM DECISION Warning] LLM decide validation failed: {e}. Falling back to rule-based.")

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
                rendered["target"] = deployment

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
                    "allowed_namespaces": ALLOWED_NAMESPACES,
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

    def _render_deployment(self, target_service: str) -> str:
        return DEFAULT_DEPLOYMENT_TEMPLATE.replace("{{target_service}}", target_service)

    def _format_prompt(
        self,
        anomaly_context: Dict[str, Any],
        detect_evidence: Dict[str, Any],
        parser: LLMDecisionOutputParser,
    ) -> str:
        target_service = anomaly_context.get("target_service", DEFAULT_SERVICE)
        suspected_fault_type = anomaly_context.get("suspected_fault_type", "unknown")
        return f"""You are a senior Site Reliability Engineer (SRE) managing a microservices cluster.
You are operating inside the /v1/decide stage. Your job is NOT to blindly trust /v1/detect.
First review whether the detect/RCA output is plausible, then decide the final runbook.

Detected anomaly_context from /v1/detect:
{json.dumps(anomaly_context, indent=2)}

Additional detect evidence for review:
{json.dumps(detect_evidence, indent=2)}

Platform profile summary:
{json.dumps({
    "system": SYSTEM_NAME,
    "services": SERVICES_LIST,
    "fault_runbook_mapping": FAULT_RUNBOOK_MAPPING,
    "dependency_graph": DEPENDENCY_GRAPH,
    "default_namespace": DEFAULT_NAMESPACE,
    "allowed_namespaces": ALLOWED_NAMESPACES,
    "deployment_template": DEFAULT_DEPLOYMENT_TEMPLATE,
}, indent=2)}

Available runbooks templates:
{json.dumps(self.runbooks, indent=2)}

Instructions:
1. Assess if /v1/detect likely identified the correct target_service and suspected_fault_type.
2. If detect evidence suggests a better service/fault, correct it in corrected_anomaly_context.
3. Choose matched_runbook from fault_runbook_mapping using the corrected fault type.
4. Render action_plan targets using the corrected target service and deployment template.
5. Do not invent services, fault types, runbooks, namespaces, or actions outside the platform profile.

Structured output instructions:
{parser.format_instructions()}

You MUST respond with a single, valid JSON object containing exactly the following keys and matching types:
{{
  "detect_assessment": {{
    "is_detect_output_plausible": true,
    "assessment_reason": "Short reason based on detect evidence"
  }},
  "corrected_anomaly_context": {{
    "target_service": "service from platform profile",
    "suspected_fault_type": "fault type from fault_runbook_mapping",
    "system": "{SYSTEM_NAME}",
    "namespace": "{DEFAULT_NAMESPACE}",
    "deployment": "deployment/service"
  }},
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
    "allowed_namespaces": {json.dumps(ALLOWED_NAMESPACES)}
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
