import time
import uuid
from typing import Tuple


class AlertCorrelationEngine:
    """Deduplicate decide requests for correlated incidents (contract §3.2)."""

    def __init__(self, healing_window_seconds: int = 120):
        self.healing_window_seconds = healing_window_seconds
        self.active_incidents: dict = {}

        self.dependency_graph = {
            "frontend": [
                "checkoutservice",
                "recommendationservice",
                "productcatalogservice",
                "cartservice",
                "shippingservice",
                "currencyservice",
                "adservice",
                "paymentservice",
                "emailservice",
            ],
            "checkoutservice": [
                "shippingservice",
                "emailservice",
                "paymentservice",
                "cartservice",
                "currencyservice",
                "productcatalogservice",
            ],
        }

    def register_incident(self, correlation_id: str, root_service: str, fault_type: str) -> None:
        if correlation_id not in self.active_incidents:
            self.active_incidents[correlation_id] = {
                "root_cause_service": root_service,
                "fault_type": fault_type,
                "start_time": int(time.time()),
                "decided": False,
            }

    def should_suppress_decide(
        self, correlation_id: str, target_service: str
    ) -> Tuple[bool, str]:
        self._cleanup_expired()
        incident = self.active_incidents.get(correlation_id)
        if not incident:
            return False, ""

        root = incident["root_cause_service"]
        if target_service == root:
            if incident.get("decided"):
                return True, "duplicate alert for root-cause (already decided)"
            incident["decided"] = True
            return False, ""

        if target_service in self.dependency_graph:
            if root in self.dependency_graph[target_service]:
                return True, f"correlated downstream symptom of upstream {root}"

        return False, ""

    def close_incident(self, correlation_id: str) -> None:
        self.active_incidents.pop(correlation_id, None)

    def _cleanup_expired(self) -> None:
        now = int(time.time())
        expired = [
            cid
            for cid, inc in self.active_incidents.items()
            if now - inc["start_time"] > self.healing_window_seconds + 60
        ]
        for cid in expired:
            del self.active_incidents[cid]
