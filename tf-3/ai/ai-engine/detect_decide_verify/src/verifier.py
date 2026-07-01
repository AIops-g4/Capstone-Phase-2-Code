from typing import List, Tuple, Dict, Any, Optional
from .config import (
    VERIFY_ERROR_THRESHOLD,
    VERIFY_LATENCY_THRESHOLD,
    VERIFY_REGRESSION_ERROR_THRESHOLD
)

class VerificationEngine:
    """
    Evaluates executed healing action statuses and post-telemetry metrics to verify recovery.
    """
    def __init__(
        self,
        error_threshold: float = VERIFY_ERROR_THRESHOLD,
        latency_threshold: float = VERIFY_LATENCY_THRESHOLD,
        regression_threshold: float = VERIFY_REGRESSION_ERROR_THRESHOLD
    ):
        self.error_threshold = error_threshold
        self.latency_threshold = latency_threshold
        self.regression_threshold = regression_threshold

    def verify_action(
        self, 
        action: Any, 
        post_telemetry_window: List[Any]
    ) -> Tuple[bool, bool, str, Optional[str]]:
        """
        Verifies healing action execution.
        
        Returns:
        - success (bool): True if target service recovered without regressions, False otherwise.
        - regression_detected (bool): True if other services experienced regressions, False otherwise.
        - next_action (str): "DONE", "RETRY", "ROLLBACK", "ESCALATE".
        - reason (str): Verification reasoning or failure description.
        """
        # 1. If the execution status on CDO executor failed, trigger RETRY immediately
        if action.status == "FAILED":
            reason = f"Healing action '{action.action}' on '{action.target}' failed to execute on CDO executor."
            return False, False, "RETRY", reason
            
        success = True
        regression_detected = False
        reasons = []
        
        # Parse target service from execution target (e.g. deployment/checkoutservice -> checkoutservice)
        target_service = action.target.split("/")[-1]
        
        # 2. Check recovery of the target service
        service_points = [p for p in post_telemetry_window if p.service == target_service]
        for p in service_points:
            if "error" in p.signal_name and float(p.value) > self.error_threshold:
                success = False
                reasons.append(f"High error rate detected: {p.signal_name} = {p.value}")
            if "latency" in p.signal_name and float(p.value) > self.latency_threshold:
                success = False
                reasons.append(f"High latency detected: {p.signal_name} = {p.value}")
                
        # 3. Check for regressions in other services
        other_points = [p for p in post_telemetry_window if p.service != target_service]
        for p in other_points:
            if "error" in p.signal_name and float(p.value) > self.regression_threshold:
                regression_detected = True
                reasons.append(f"Regression detected in other service '{p.service}': {p.signal_name} = {p.value}")
                
        # 4. Formulate the next action decision
        if success and not regression_detected:
            return True, False, "DONE", "Target service recovered successfully without any regressions."
        elif regression_detected:
            reason = f"Healing action caused regression: {'; '.join(reasons)}"
            return False, True, "ROLLBACK", reason
        else:
            reason = f"Target service failed to recover: {'; '.join(reasons)}"
            return False, False, "ESCALATE", reason
