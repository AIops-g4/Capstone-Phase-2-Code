import json
import os
from typing import List, Dict, Any

class RunbookRetriever:
    """
    Component 2: RAG Retriever for Operational Runbooks.
    Matches fault types to runbooks using signal_triggers field.
    """
    def __init__(self, file_path: str = "runbooks/runbooks.json"):
        self.file_path = file_path
        self._runbooks = []
        self._load()

    def _load(self):
        # Look for the file relative to the project root
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        full_path = os.path.join(base_dir, self.file_path)
        
        if os.path.exists(full_path):
            with open(full_path, 'r', encoding='utf-8') as f:
                self._runbooks = json.load(f)
        else:
            # Fallback for testing — matches runbooks.json structure
            self._runbooks = [
                {
                    "id": "DatabaseConnectionRecoveryRunbook",
                    "description": "Recovers database connection issues by patching memory limits and restarting the deployment.",
                    "pattern_type": "urgent",
                    "signal_triggers": ["db_connection_pool_saturation", "service_error_rate"],
                    "actions": ["PATCH_MEMORY_LIMIT", "RESTART_DEPLOYMENT"]
                },
                {
                    "id": "PodOOMKilledRunbook",
                    "description": "Handles pod OOMKilled events by increasing memory limits and restarting.",
                    "pattern_type": "urgent",
                    "signal_triggers": ["pod_oom_event", "container_resource_usage"],
                    "actions": ["PATCH_MEMORY_LIMIT", "RESTART_DEPLOYMENT"]
                },
                {
                    "id": "ServiceUnhealthyRunbook",
                    "description": "Restarts unhealthy deployments that fail probes, with rollback if needed.",
                    "pattern_type": "urgent",
                    "signal_triggers": ["service_unhealthy", "container_restart_count"],
                    "actions": ["RESTART_DEPLOYMENT", "ROLLOUT_UNDO"]
                },
                {
                    "id": "QueueBacklogRunbook",
                    "description": "Scales up workers when queue backlog exceeds threshold.",
                    "pattern_type": "deferred",
                    "signal_triggers": ["queue_backlog"],
                    "actions": ["SCALE_REPLICAS"]
                },
                {
                    "id": "CertExpiryRotationRunbook",
                    "description": "Rotates expiring secrets/certificates before disruption.",
                    "pattern_type": "deferred",
                    "signal_triggers": ["secret_expiry_warning"],
                    "actions": ["ROTATE_SECRET"]
                }
            ]

    def retrieve_relevant(self, fault_type: str, limit: int = 3) -> List[Dict[str, Any]]:
        """
        Returns relevant runbooks for the LLM to choose from.
        Prioritizes runbooks whose signal_triggers match the fault_type keywords.
        """
        # Simple keyword matching to prioritize relevant runbooks
        scored = []
        fault_lower = fault_type.lower()
        
        for rb in self._runbooks:
            score = 0
            # Check if fault_type keywords appear in signal_triggers or description
            triggers = rb.get("signal_triggers", [])
            for trigger in triggers:
                if any(word in fault_lower for word in trigger.split("_")):
                    score += 2
            # Also check description
            desc = rb.get("description", "").lower()
            for word in fault_lower.split("_"):
                if word in desc:
                    score += 1
            scored.append((score, rb))
        
        # Sort by relevance (highest score first), then take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        return [rb for _, rb in scored[:limit]]
