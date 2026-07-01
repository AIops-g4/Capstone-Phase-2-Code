import os
import json
import uuid
import threading
from typing import Dict, Any, Tuple, Optional
from .config import DEPENDENCY_GRAPH, DEPENDENCY_GRAPH_PATH, ALERT_HEALING_WINDOW_SECONDS

class IncidentManager:
    """
    Tracks incident lifecycles, handles alert deduplication, and dependency-based symptom suppression.
    Loads dependency graph dynamically.
    
    Thread safety:
    - All mutations to self.active_incidents are guarded by self._lock (TC-K1 fix).
    """
    def __init__(self, dependency_graph_path: str = DEPENDENCY_GRAPH_PATH):
        self.dependency_graph_path = dependency_graph_path
        self.dependency_graph = {}
        self.active_incidents = {}  # corr_id -> incident_dict
        self._lock = threading.Lock()
        self.load_dependency_graph()

    def load_dependency_graph(self) -> None:
        """
        Loads microservices dependency graph from JSON. Fallback to hardcoded defaults if missing.
        """
        if os.path.exists(self.dependency_graph_path):
            try:
                with open(self.dependency_graph_path, "r") as f:
                    self.dependency_graph = json.load(f)
                print(f"Loaded dependency graph from {self.dependency_graph_path}")
            except Exception as e:
                print(f"Warning: Failed to load dependency graph from {self.dependency_graph_path}: {e}. Using defaults.")
                self._load_defaults()
        else:
            print(f"Warning: Dependency graph file not found at {self.dependency_graph_path}. Using defaults.")
            self._load_defaults()

    def _load_defaults(self) -> None:
        self.dependency_graph = DEPENDENCY_GRAPH.copy()

    def correlate_alert(self, target_service: str, fault_type: str, timestamp: int, correlation_id: Optional[str] = None) -> Tuple[str, bool]:
        """
        Correlates a newly detected service alert against active incidents.
        Thread-safe via self._lock.
        
        If `correlation_id` is provided, it will be used as the incident key
        when creating a new primary incident (instead of generating a random UUID).
        This ensures the caller can control the correlation_id and subsequent
        /v1/decide or /v1/verify calls will find the expected workflow state.
        
        Returns:
        - correlation_id: The primary incident's UUID.
        - is_correlated: True if this alert is a symptom or duplicate and should be suppressed,
                          False if it is the primary root cause.
        """
        with self._lock:
            # 1. Check if there is an active incident on the exact same service
            for corr_id, inc in self.active_incidents.items():
                if inc["status"] == "active" and inc["root_cause_service"] == target_service:
                    print(f"  [ALERT CORRELATION] Alert on {target_service} correlated as DUPLICATE of active incident {corr_id}.")
                    return corr_id, True
                    
            # 2. Check if the newly flagged service is a downstream symptom of an active upstream incident
            for corr_id, inc in self.active_incidents.items():
                if inc["status"] == "active":
                    upstream_service = inc["root_cause_service"]
                    
                    # If target_service is downstream of the upstream_service, it is a symptom
                    if target_service in self.dependency_graph and upstream_service in self.dependency_graph[target_service]:
                        inc["symptoms"].append(target_service)
                        print(f"  [ALERT CORRELATION] Alert on downstream {target_service} correlated as SYMPTOM of upstream incident {corr_id} ({upstream_service}).")
                        return corr_id, True
                        
            # 3. Create a new primary incident
            # Use the provided correlation_id if given, otherwise generate a new UUID
            new_corr_id = correlation_id if correlation_id else str(uuid.uuid4())
            self.active_incidents[new_corr_id] = {
                "root_cause_service": target_service,
                "fault_type": fault_type,
                "status": "active",
                "start_time": timestamp,
                "symptoms": [],
                "decided": False
            }
            print(f"  [ALERT CORRELATION] Created new primary incident {new_corr_id} for root cause service {target_service} ({fault_type}).")
            return new_corr_id, False

    def close_incident(self, correlation_id: str) -> None:
        """
        Closes an active incident. Thread-safe via self._lock.
        """
        with self._lock:
            if correlation_id in self.active_incidents:
                self.active_incidents[correlation_id]["status"] = "resolved"
                print(f"  [ALERT CORRELATION] Closed incident: {correlation_id}")
