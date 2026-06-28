"""
Component 2 — Decision Router: Routes between LLM (Bedrock) and Fallback Engine.
Per AI API Contract §4: Checks 4 fallback conditions before calling LLM.

Fallback conditions:
1. Cost cap exceeded ($50/day per tenant)
2. Bedrock rate-limited (429 from Bedrock)
3. Bedrock timeout/downtime (>2500ms or 5xx)
4. LLM response parse failure (invalid JSON / schema validation failure)
"""
from typing import Dict, Any, List, Tuple
from app.decision.bedrock_client import BedrockDecisionEngine
from app.decision.fallback_engine import FallbackDecisionEngine
import logging
import json

logger = logging.getLogger(__name__)


from app.safety.cost_tracker import CostTracker

class DecisionRouter:
    """
    Routes decision requests between the LLM engine and the fallback engine.
    Returns (decision_json, cost_cap_exceeded: bool, used_fallback: bool).
    """

    def __init__(self):
        self.llm_engine = BedrockDecisionEngine()
        self.fallback_engine = FallbackDecisionEngine()
        self.cost_tracker = CostTracker()

    def decide(
        self,
        anomaly_context: Dict[str, Any],
        runbooks: List[Dict[str, Any]],
        tenant_id: str,
        cost_cap_exceeded: bool = False,
    ) -> Tuple[Dict[str, Any], bool, bool]:
        """
        Routes the decision to LLM or fallback based on conditions.
        
        Args:
            anomaly_context: Anomaly context from /v1/detect
            runbooks: Retrieved runbooks from RAG
            tenant_id: Tenant identifier for cost tracking
            cost_cap_exceeded: Whether tenant's cost cap is exceeded
            
        Returns:
            Tuple of (decision_json, cost_cap_exceeded, used_fallback)
        """
        # Condition 1: Cost cap exceeded → fallback
        if cost_cap_exceeded:
            logger.info(f"Tenant {tenant_id}: Cost cap exceeded. Using fallback engine.")
            decision = self.fallback_engine.decide(anomaly_context)
            return decision, True, True

        # Condition 2/3/4: Try LLM, fall back on failure
        try:
            decision, cost = self.llm_engine.decide(anomaly_context, runbooks, tenant_id)

            # Increment cost
            self.cost_tracker.increment_cost(tenant_id, cost)

            # Condition 4: Validate LLM output is proper JSON with required fields
            if not self._validate_llm_output(decision):
                logger.warning(
                    f"Tenant {tenant_id}: LLM output failed schema validation. Using fallback."
                )
                decision = self.fallback_engine.decide(anomaly_context)
                return decision, False, True

            return decision, False, False

        except Exception as e:
            # Conditions 2 & 3: Bedrock 429, timeout, 5xx, connection error
            logger.error(f"Tenant {tenant_id}: LLM invocation failed: {e}. Using fallback.")
            decision = self.fallback_engine.decide(anomaly_context)
            return decision, False, True


    def _validate_llm_output(self, decision: Dict[str, Any]) -> bool:
        """Validates that LLM output contains required fields per DecideResponse schema."""
        required_fields = ["matched_runbook", "pattern_type", "action_plan"]
        for field in required_fields:
            if field not in decision:
                return False

        # Validate pattern_type is a valid enum
        if decision.get("pattern_type") not in ("urgent", "deferred"):
            return False

        # Validate action_plan is a non-empty list
        action_plan = decision.get("action_plan", [])
        if not isinstance(action_plan, list) or len(action_plan) == 0:
            return False

        # Validate each step has required fields
        for step in action_plan:
            if not all(k in step for k in ("step", "action", "target", "params")):
                return False

        return True
