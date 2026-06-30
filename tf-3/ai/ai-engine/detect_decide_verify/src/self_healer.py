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
    FAULT_TYPE_CATALOG,
    METRIC_TYPES_LIST,
    PLATFORM_PROFILE,
    SERVICES_LIST,
    SYSTEM_NAME,
)
from .llm import LLMFactory


FAULT_TYPE_CANDIDATES = list(FAULT_TYPE_CATALOG)


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
            f"corrected_anomaly_context.suspected_fault_type must be one of metric_types: {sorted(FAULT_TYPE_CANDIDATES)}. "
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
        if fault_type not in FAULT_TYPE_CANDIDATES:
            raise ValueError(f"LLM corrected unsupported metric/fault type: {fault_type}")
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


class LLMFaultTypeOutputParser:
    """
    Structured parser for a dedicated LLM fault-type classifier.

    This parser intentionally does not accept target_service in the output. The
    selected service is fixed by the caller; LLM is only allowed to refine
    suspected_fault_type and explain the evidence.
    """

    REQUIRED_KEYS = {"suspected_fault_type", "confidence", "reason"}

    def format_instructions(self) -> str:
        return (
            "Return exactly one raw JSON object with no markdown, no comments, and no extra text. "
            f"The object must contain only these keys: {sorted(self.REQUIRED_KEYS)}. "
            f"suspected_fault_type must be one of metric_types: {sorted(FAULT_TYPE_CANDIDATES)}. "
            "confidence must be a number between 0.0 and 1.0. "
            "reason must be a non-empty string explaining metric/log evidence. "
            "Do not include target_service and do not try to change the selected service."
        )

    def parse(self, response_text: str) -> Dict[str, Any]:
        raw_json = self._extract_json_object(response_text)
        result = json.loads(raw_json)
        if not isinstance(result, dict):
            raise ValueError("LLM fault-type response must be a JSON object")

        keys = set(result.keys())
        missing = self.REQUIRED_KEYS - keys
        extra = keys - self.REQUIRED_KEYS
        if missing:
            raise ValueError(f"LLM fault-type response missing keys: {sorted(missing)}")
        if extra:
            raise ValueError(f"LLM fault-type response contains unsupported keys: {sorted(extra)}")

        fault_type = result.get("suspected_fault_type")
        if fault_type not in FAULT_TYPE_CANDIDATES:
            raise ValueError(f"LLM fault-type response has unsupported metric/fault type: {fault_type}")

        try:
            confidence = float(result.get("confidence"))
        except (TypeError, ValueError):
            raise ValueError("LLM fault-type confidence must be numeric")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("LLM fault-type confidence must be between 0.0 and 1.0")

        reason = result.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("LLM fault-type reason must be a non-empty string")

        return {
            "suspected_fault_type": fault_type,
            "confidence": confidence,
            "reason": reason.strip(),
        }

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


class LLMFaultTypeRankingOutputParser:
    """Parser for LLM-ranked fault-type candidates for one fixed service."""

    REQUIRED_KEYS = {"fault_type_ranking"}

    def format_instructions(self) -> str:
        return (
            "Return exactly one raw JSON object with no markdown, no comments, and no extra text. "
            f"The object must contain only this key: {sorted(self.REQUIRED_KEYS)}. "
            "fault_type_ranking must be a non-empty list sorted by confidence descending. "
            "Each item must include suspected_fault_type, confidence, and reason. "
            f"suspected_fault_type must be one of metric_types: {sorted(FAULT_TYPE_CANDIDATES)}. "
            "confidence must be a number between 0.0 and 1.0. "
            "Do not include target_service and do not try to change the selected service."
        )

    def parse(self, response_text: str) -> Dict[str, Any]:
        raw_json = self._extract_json_object(response_text)
        result = json.loads(raw_json)
        if not isinstance(result, dict):
            raise ValueError("LLM fault-ranking response must be a JSON object")
        keys = set(result.keys())
        missing = self.REQUIRED_KEYS - keys
        extra = keys - self.REQUIRED_KEYS
        if missing:
            raise ValueError(f"LLM fault-ranking response missing keys: {sorted(missing)}")
        if extra:
            raise ValueError(f"LLM fault-ranking response contains unsupported keys: {sorted(extra)}")

        ranking = result.get("fault_type_ranking")
        if not isinstance(ranking, list) or not ranking:
            raise ValueError("fault_type_ranking must be a non-empty list")

        normalized = []
        seen = set()
        for idx, item in enumerate(ranking, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"fault_type_ranking item {idx} must be an object")
            fault_type = item.get("suspected_fault_type")
            if fault_type not in FAULT_TYPE_CANDIDATES:
                raise ValueError(f"LLM ranked unsupported metric/fault type: {fault_type}")
            if fault_type in seen:
                continue
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                raise ValueError("LLM ranked confidence must be numeric")
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("LLM ranked confidence must be between 0.0 and 1.0")
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("LLM ranked reason must be a non-empty string")
            normalized.append(
                {
                    "suspected_fault_type": fault_type,
                    "confidence": confidence,
                    "reason": reason.strip(),
                }
            )
            seen.add(fault_type)

        normalized.sort(key=lambda item: item["confidence"], reverse=True)
        return {"fault_type_ranking": normalized}

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

    def detect_fault_type_with_llm(
        self,
        anomaly_context: Dict[str, Any],
        detect_evidence: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Use LLM only as a fixed-service fault-type classifier.

        The LLM is not allowed to change target_service. On validation/API errors,
        this returns the original suspected_fault_type with used=False so callers
        can safely continue with deterministic rule-based healing.
        """
        selected_service = anomaly_context.get("target_service", DEFAULT_SERVICE)
        current_fault = anomaly_context.get("suspected_fault_type", "unknown")
        parser = LLMFaultTypeOutputParser()

        try:
            client = LLMFactory.get_client()
            prompt = self._format_fault_type_prompt(anomaly_context, detect_evidence or {}, parser)
            response_text = client.generate_decision(prompt)
            result = parser.parse(response_text)
            result.update(
                {
                    "selected_service": selected_service,
                    "previous_fault_type": current_fault,
                    "used": True,
                }
            )
            print(
                "  [LLM FAULT] Successfully classified fault type for fixed service "
                f"{selected_service}: {result['suspected_fault_type']} "
                f"(confidence={result['confidence']:.2f})"
            )
            return result
        except Exception as e:
            print(
                "  [LLM FAULT Warning] Fault-type classification failed: "
                f"{e}. Keeping existing fault type: {current_fault}."
            )
            return {
                "selected_service": selected_service,
                "suspected_fault_type": current_fault,
                "previous_fault_type": current_fault,
                "confidence": 0.0,
                "reason": str(e),
                "used": False,
                "error": str(e),
            }

    def rank_fault_types_with_llm(
        self,
        anomaly_context: Dict[str, Any],
        detect_evidence: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """
        Rank fault types by confidence for one fixed service.

        The current suspected_fault_type is not removed by this method; callers
        can still try it first, then use this ranking for fallback ordering.
        """
        selected_service = anomaly_context.get("target_service", DEFAULT_SERVICE)
        parser = LLMFaultTypeRankingOutputParser()
        try:
            client = LLMFactory.get_client()
            prompt = self._format_fault_type_ranking_prompt(anomaly_context, detect_evidence or {}, parser)
            response_text = client.generate_decision(prompt)
            result = parser.parse(response_text)
            result.update(
                {
                    "selected_service": selected_service,
                    "used": True,
                }
            )
            print(
                "  [LLM FAULT RANK] Ranked fault types for fixed service "
                f"{selected_service}: "
                + ", ".join(
                    f"{item['suspected_fault_type']}={item['confidence']:.2f}"
                    for item in result["fault_type_ranking"]
                )
            )
            return result
        except Exception as e:
            print(
                "  [LLM FAULT RANK Warning] Fault-type ranking failed: "
                f"{e}. Falling back to default fault order."
            )
            return {
                "selected_service": selected_service,
                "fault_type_ranking": [],
                "used": False,
                "error": str(e),
            }

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
    "metric_types_as_fault_candidates": FAULT_TYPE_CANDIDATES,
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
6. If Additional detect evidence contains failed_self_heal_attempts, treat them as negative feedback from /v1/verify.
7. If the target service appears stable but its selected fault runbook failed, keep the service and reassess the fault type using metric/log evidence.
8. If the same generated fault type has already been tried across candidate services and failed, reassess target_service among service_top_k instead of repeating the same service.

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

    def _format_fault_type_prompt(
        self,
        anomaly_context: Dict[str, Any],
        detect_evidence: Dict[str, Any],
        parser: LLMFaultTypeOutputParser,
    ) -> str:
        selected_service = anomaly_context.get("target_service", DEFAULT_SERVICE)
        suspected_fault_type = anomaly_context.get("suspected_fault_type", "unknown")
        return f"""You are a senior Site Reliability Engineer (SRE) classifying the fault type for a microservice incident.

The target service has already been selected and is FIXED:
{selected_service}

Your only task is to refine suspected_fault_type for this fixed service. Do NOT change target_service.

Current anomaly_context:
{json.dumps(anomaly_context, indent=2)}

Current BARO/rule-based suspected_fault_type:
{suspected_fault_type}

Additional evidence focused on the fixed service:
{json.dumps(detect_evidence, indent=2)}

Allowed metric_types to use as fault-type candidates:
{json.dumps(FAULT_TYPE_CANDIDATES, indent=2)}

Fault-to-runbook mapping for those metric_types:
{json.dumps(FAULT_RUNBOOK_MAPPING, indent=2)}

Useful interpretation hints:
- cpu: CPU/core/processor saturation or throttling evidence.
- mem: memory/OOM/heap/RSS/leak evidence.
- delay: latency/p95/p99/timeout/duration/slow-response evidence.
- loss: packet loss, high error rate, reset/refused/deadline/unavailable evidence.
- disk: disk I/O, filesystem, IOPS, disk latency evidence.
- socket: connection/socket/fd/TCP exhaustion evidence.
- If failed_self_heal_attempts exists, treat failed attempts as negative verification feedback.
- If the fixed service is stable under one fault hypothesis but verification failed, choose a different fault type supported by metric/log evidence.

Structured output instructions:
{parser.format_instructions()}

You MUST respond with a single valid JSON object exactly like:
{{
  "suspected_fault_type": "fault type from metric_types",
  "confidence": 0.82,
  "reason": "Short evidence-based explanation for the fixed service only"
}}

Ensure there is no conversational text, no comments, and no markdown formatting in your response. Just return the raw JSON object.
"""

    def _format_fault_type_ranking_prompt(
        self,
        anomaly_context: Dict[str, Any],
        detect_evidence: Dict[str, Any],
        parser: LLMFaultTypeRankingOutputParser,
    ) -> str:
        selected_service = anomaly_context.get("target_service", DEFAULT_SERVICE)
        suspected_fault_type = anomaly_context.get("suspected_fault_type", "unknown")
        return f"""You are a senior Site Reliability Engineer (SRE) ranking fault-type hypotheses for a fixed service.

The target service is FIXED and must not be changed:
{selected_service}

Current suspected_fault_type from RCA/previous attempt:
{suspected_fault_type}

Current anomaly_context:
{json.dumps(anomaly_context, indent=2)}

Evidence for ranking fault types on this fixed service:
{json.dumps(detect_evidence, indent=2)}

Allowed metric_types to use as fault-type candidates:
{json.dumps(FAULT_TYPE_CANDIDATES, indent=2)}

Fault-to-runbook mapping for those metric_types:
{json.dumps(FAULT_RUNBOOK_MAPPING, indent=2)}

Task:
- Rank fault types by confidence from highest to lowest for this fixed service.
- Use metric/log evidence and failed_self_heal_attempts as negative feedback.
- Do not change or output target_service.
- Include every plausible allowed fault type you can rank; confidence should reflect evidence strength.

Structured output instructions:
{parser.format_instructions()}

You MUST respond with a single valid JSON object exactly like:
{{
  "fault_type_ranking": [
    {{"suspected_fault_type": "delay", "confidence": 0.86, "reason": "Latency p95/timeout evidence dominates for the fixed service."}},
    {{"suspected_fault_type": "loss", "confidence": 0.42, "reason": "Some error/reset symptoms, but weaker than latency."}}
  ]
}}

Ensure there is no conversational text, no comments, and no markdown formatting in your response. Just return the raw JSON object.
"""


# Create alias
HealingEngine = SelfHealer
