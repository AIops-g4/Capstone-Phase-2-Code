"""
Component 3 — Circuit Breaker: Per-tenant action rate limiter.
Per design: Trips when >3 automated actions per 5 minutes per tenant.
When tripped, all automated actions halt → escalate to human engineer.
"""
from collections import defaultdict
from typing import Dict, List, Tuple
from datetime import datetime, timezone
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    Per-tenant circuit breaker that limits the rate of automated remediation actions.
    Uses an in-memory sliding window to track action timestamps per tenant.
    
    NOTE: In production, this should use a distributed store (Redis/DynamoDB) 
    for multi-pod consistency. In-memory is sufficient for single-pod capstone.
    """

    def __init__(
        self,
        max_actions: int = None,
        window_seconds: int = None,
    ):
        self.max_actions = max_actions or settings.CIRCUIT_BREAKER_MAX_ACTIONS
        self.window_seconds = window_seconds or settings.CIRCUIT_BREAKER_WINDOW_SECONDS
        # tenant_id → list of action timestamps
        self._action_log: Dict[str, List[datetime]] = defaultdict(list)

    def check_and_record(self, tenant_id: str) -> Tuple[bool, int]:
        """
        Checks if the circuit breaker is tripped for this tenant.
        If not tripped, records the action timestamp.
        
        Returns:
            (is_tripped: bool, current_action_count: int)
        """
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - self.window_seconds

        # Clean expired entries
        self._action_log[tenant_id] = [
            ts for ts in self._action_log[tenant_id]
            if ts.timestamp() > cutoff
        ]

        current_count = len(self._action_log[tenant_id])

        if current_count >= self.max_actions:
            logger.warning(
                f"Circuit breaker TRIPPED for tenant {tenant_id}: "
                f"{current_count} actions in last {self.window_seconds}s "
                f"(max: {self.max_actions})"
            )
            return True, current_count

        # Record this action
        self._action_log[tenant_id].append(now)
        logger.info(
            f"Circuit breaker OK for tenant {tenant_id}: "
            f"{current_count + 1}/{self.max_actions} actions in window."
        )
        return False, current_count + 1

    def is_tripped(self, tenant_id: str) -> bool:
        """Check-only: does not record an action."""
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - self.window_seconds

        recent = [
            ts for ts in self._action_log.get(tenant_id, [])
            if ts.timestamp() > cutoff
        ]
        return len(recent) >= self.max_actions
