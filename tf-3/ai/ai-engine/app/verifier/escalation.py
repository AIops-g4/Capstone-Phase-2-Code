"""
Component 4 — Escalation Bundle Generator.
Per TF3 requirement: "message phải kèm full context bundle — logs, metrics, deploy history, attempts đã thử"
Generates AI-powered (LLM) or template-based escalation messages.
"""
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)


class EscalationBundleGenerator:
    """
    Assembles full context escalation bundles for the on-call engineer.
    Uses template-based generation (LLM escalation summary is optional).
    """

    def generate(
        self,
        fault_type: str,
        target_service: str,
        action_executed: Dict[str, Any],
        error_logs: List[str],
        metrics_summary: Dict[str, Any],
        attempted_actions: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Generates a complete escalation bundle.
        
        Args:
            fault_type: The suspected_fault_type from detection
            target_service: The target service name
            action_executed: What CDO executed (action, target, status)
            error_logs: Error log lines from verification
            metrics_summary: Post-action metrics snapshot
            attempted_actions: List of previous remediation attempts
        """
        # Template-based reason generation (LLM-free fallback)
        reason = self._generate_reason(
            fault_type=fault_type,
            target_service=target_service,
            action=action_executed.get("action", "UNKNOWN"),
            status=action_executed.get("status", "UNKNOWN"),
            metrics=metrics_summary,
        )

        bundle = {
            "reason": reason,
            "logs": error_logs if error_logs else [f"No detailed logs captured for {target_service}."],
            "metrics": metrics_summary if metrics_summary else {},
        }

        if attempted_actions:
            bundle["attempted_actions"] = attempted_actions

        return bundle

    def _generate_reason(
        self,
        fault_type: str,
        target_service: str,
        action: str,
        status: str,
        metrics: Dict[str, Any],
    ) -> str:
        """
        Generates a human-readable reason string.
        Template-based fallback when LLM is unavailable.
        """
        # Format key metrics into a readable string
        key_metrics = ", ".join(
            f"{k}={v}" for k, v in list(metrics.items())[:3]
        ) if metrics else "no metrics available"

        if status == "FAILED":
            return (
                f"Auto-remediation failed for {fault_type} on {target_service}. "
                f"Action {action} could not be executed by CDO. "
                f"Current state: {key_metrics}."
            )[:300]
        else:
            return (
                f"Auto-remediation did not resolve {fault_type} on {target_service}. "
                f"Action {action} was executed but the issue persists. "
                f"Current state: {key_metrics}."
            )[:300]
