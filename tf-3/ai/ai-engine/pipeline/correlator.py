import json
import numpy as np
from datetime import datetime
from config.settings import Settings
from utils.logger import get_logger

logger = get_logger("AlertCorrelator")

class Alert:
    def __init__(self, service: str, metric: str, fault_type: str, score: float, timestamp: float, trigger_value: float = 0.0, log_template: str = ""):
        self.service = service
        self.metric = metric
        self.fault_type = fault_type
        self.score = score
        self.timestamp = timestamp
        self.trigger_value = trigger_value
        self.log_template = log_template
        
        # Layer 1: Fingerprint
        self.fingerprint = f"{service}:{metric}:{fault_type}"

    def __repr__(self):
        return f"Alert({self.service}, {self.metric}, {self.fault_type}, score={self.score:.2f}, ts={self.timestamp})"

class AlertCorrelator:
    def __init__(self, settings: Settings = None):
        self.settings = settings or Settings()
        self.time_window = self.settings.CORRELATION_TIME_WINDOW_SEC
        self.semantic_weight = self.settings.CORRELATION_SEMANTIC_WEIGHT
        self.dependencies = self._load_topology()

    def _normalize_name(self, name: str) -> str:
        """Maps Online Boutique services to GeekShop services in the topology graph."""
        name = name.lower().strip()
        mapping = {
            "frontend": "edge-lb",
            "frontendservice": "edge-lb",
            "checkoutservice": "checkout-svc",
            "paymentservice": "payment-svc",
            "cartservice": "cart-svc",
            "productcatalogservice": "catalog-svc",
            "recommendationservice": "recommender-svc",
            "shippingservice": "inventory-svc",
            "emailservice": "notification-svc",
            "adservice": "search-svc",
            "redis": "cart-redis",
            "kafka": "kafka-events",
            # Ensure it is idempotent
            "edge-lb": "edge-lb",
            "auth-svc": "auth-svc",
            "checkout-svc": "checkout-svc",
            "payment-svc": "payment-svc",
            "cart-svc": "cart-svc",
            "catalog-svc": "catalog-svc",
            "recommender-svc": "recommender-svc",
            "inventory-svc": "inventory-svc",
            "notification-svc": "notification-svc",
            "search-svc": "search-svc",
            "payments-db": "payments-db",
            "catalog-db": "catalog-db",
            "cart-redis": "cart-redis",
            "kafka-events": "kafka-events"
        }
        return mapping.get(name, name)

    def _denormalize_name(self, name: str) -> str:
        """Maps GeekShop services in the topology graph back to Online Boutique service names."""
        mapping = {
            "edge-lb": "frontendservice",
            "checkout-svc": "checkoutservice",
            "payment-svc": "paymentservice",
            "cart-svc": "cartservice",
            "catalog-svc": "productcatalogservice",
            "recommender-svc": "recommendationservice",
            "inventory-svc": "shippingservice",
            "notification-svc": "emailservice",
            "search-svc": "adservice",
            "cart-redis": "redis"
        }
        return mapping.get(name, name)

    def _load_topology(self) -> dict:
        """Loads the topology graph from the configured JSON file path."""
        dependencies = {}
        path = self.settings.TOPOLOGY_GRAPH_PATH
        if not path or not path.exists():
            logger.warning(f"Topology graph JSON not found at {path}. Using default fallback graph.")
            return {
                "edge-lb": ["auth-svc", "catalog-svc", "search-svc", "checkout-svc"],
                "checkout-svc": ["cart-svc", "payment-svc", "inventory-svc", "notification-svc"]
            }
            
        try:
            with open(path, "r") as f:
                topo = json.load(f)
                
            for edge in topo.get("edges", []):
                src = self._normalize_name(edge["from"])
                dst = self._normalize_name(edge["to"])
                if src not in dependencies:
                    dependencies[src] = []
                dependencies[src].append(dst)
                
            logger.info(f"Successfully loaded topology graph from {path} with {len(dependencies)} source service(s).")
        except Exception as e:
            logger.error(f"Failed to load topology graph JSON: {e}. Using fallback.")
            dependencies = {
                "edge-lb": ["auth-svc", "catalog-svc", "search-svc", "checkout-svc"],
                "checkout-svc": ["cart-svc", "payment-svc", "inventory-svc", "notification-svc"]
            }
        return dependencies

    def correlate(self, raw_anomalies: list) -> dict:
        """
        Correlates raw anomalies through the 4 layers:
        1. Fingerprint
        2. Time-window
        3. Semantic
        4. Clustering & Root Cause Identification
        """
        if not raw_anomalies:
            return {}

        # Convert raw dicts to Alert objects
        alerts = []
        for anom in raw_anomalies:
            normalized_service = self._normalize_name(anom["service"])
            alerts.append(Alert(
                service=normalized_service,
                metric=anom.get("trigger_metric", "unknown"),
                fault_type=anom["fault_type"],
                score=anom["score"],
                timestamp=anom.get("timestamp", datetime.now().timestamp()),
                trigger_value=anom.get("trigger_value", 0.0),
                log_template=anom.get("log_template", "")
            ))

        # Layer 1: Fingerprint (Deduplication)
        deduped_alerts = self._fingerprint_layer(alerts)
        logger.info(f"Layer 1 (Fingerprint): Reduced {len(alerts)} alerts to {len(deduped_alerts)} unique alerts.")

        if not deduped_alerts:
            return {}

        # Layer 2: Time-window (Temporal Grouping)
        temporal_groups = self._time_window_layer(deduped_alerts)
        logger.info(f"Layer 2 (Time-window): Formed {len(temporal_groups)} temporal group(s).")

        # Layer 3 & 4: Semantic Analysis & Clustering (incident merging + RCA)
        incidents = []
        for group in temporal_groups:
            incident = self._cluster_and_rca(group)
            if incident:
                incidents.append(incident)

        if not incidents:
            return {}

        # Sort incidents by severity/score and return the primary incident
        incidents.sort(key=lambda x: x["score"], reverse=True)
        return incidents[0]

    def _fingerprint_layer(self, alerts: list) -> list:
        """Deduplicates alerts with identical fingerprints by keeping the highest score."""
        fingerprints = {}
        for alert in alerts:
            fp = alert.fingerprint
            if fp not in fingerprints or alert.score > fingerprints[fp].score:
                fingerprints[fp] = alert
        return list(fingerprints.values())

    def _time_window_layer(self, alerts: list) -> list:
        """Groups alerts that occur close in time using a sliding window."""
        sorted_alerts = sorted(alerts, key=lambda x: x.timestamp)
        groups = []
        
        for alert in sorted_alerts:
            placed = False
            for group in groups:
                if any(abs(alert.timestamp - member.timestamp) <= self.time_window for member in group):
                    group.append(alert)
                    placed = True
                    break
            if not placed:
                groups.append([alert])
                
        return groups

    def _is_dependency(self, service_a: str, service_b: str) -> bool:
        """Returns True if service_a calls service_b directly or indirectly (1-hop)."""
        if service_a == service_b:
            return False
            
        # Direct dependency
        if service_a in self.dependencies and service_b in self.dependencies[service_a]:
            return True
            
        # Indirect dependency (1-hop transitive check)
        if service_a in self.dependencies:
            for intermediate in self.dependencies[service_a]:
                if intermediate in self.dependencies and service_b in self.dependencies[intermediate]:
                    return True
                    
        return False

    def _cluster_and_rca(self, group: list) -> dict:
        """
        Layer 3 & 4: Identifies semantic relationships, clusters the group,
        and runs heuristics to locate the definitive Root Cause.
        """
        if not group:
            return {}

        # Heuristic 1: Earliest Alert
        earliest_alert = min(group, key=lambda x: x.timestamp)
        
        # Heuristic 2: Topology Dependency Depth
        rca_candidates = {}
        for alert in group:
            service = alert.service
            if service not in rca_candidates or alert.score > rca_candidates[service].score:
                rca_candidates[service] = alert

        # Rank candidates based on downstream dependency
        candidate_services = list(rca_candidates.keys())
        scores = {svc: 1.0 for svc in candidate_services}

        for svc_a in candidate_services:
            for svc_b in candidate_services:
                if self._is_dependency(svc_a, svc_b):
                    # svc_b is downstream of svc_a. So svc_b is more likely the root cause!
                    scores[svc_b] += self.semantic_weight
                    scores[svc_a] -= 0.5

        # Incorporate timestamp: earlier alerts get a slight boost
        for svc, alert in rca_candidates.items():
            if alert == earliest_alert:
                scores[svc] += 0.5
            scores[svc] += float(alert.score / 100.0)

        # Select the service with the highest score as the root cause
        root_cause_service = max(scores, key=scores.get)
        root_alert = rca_candidates[root_cause_service]

        # Calculate combined incident score as max score in the group
        incident_score = max(alert.score for alert in group)

        # Denormalize root service name back to Online Boutique format for the benchmark and decider
        original_service = self._denormalize_name(root_alert.service)

        # Build Incident context
        return {
            "target_service": original_service,
            "suspected_fault_type": root_alert.fault_type,
            "score": incident_score,
            "trigger_metric": root_alert.metric,
            "trigger_value": root_alert.trigger_value,
            "correlated_services": [self._denormalize_name(alert.service) for alert in group],
            "reasoning": f"Correlated {len(group)} alerts. Located root cause in downstream service '{original_service}' "
                         f"({root_alert.fault_type} anomaly, combined score: {incident_score:.2f}) based on dynamic topology graph."
        }
