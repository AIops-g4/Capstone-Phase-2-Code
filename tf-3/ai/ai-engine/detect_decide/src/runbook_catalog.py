"""Predefined runbook catalog aligned with ai-api-contract.md."""

import json
import os

from .config import FAULT_RUNBOOK_MAPPING, RUNBOOKS_PATH


def get_runbooks() -> dict:
    return {
        "CPUSaturationRecoveryRunbook": {
            "name": "CPUSaturationRecoveryRunbook",
            "description": "Handle CPU saturation by scaling replicas.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "SCALE_REPLICAS",
                    "target": "deployment/{{target_service}}",
                    "params": {"namespace": "production", "replicas": 3},
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"],
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": ["pod_ready == true"],
            },
        },
        "MemoryLeakRecoveryRunbook": {
            "name": "MemoryLeakRecoveryRunbook",
            "description": "Handle memory pressure by patching memory limits.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "PATCH_MEMORY_LIMIT",
                    "target": "deployment/{{target_service}}",
                    "params": {
                        "namespace": "production",
                        "container": "main",
                        "memory_request_mb": 512,
                        "memory_limit_mb": 1024,
                    },
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"],
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": ["pod_ready == true", "restart_count_no_increase == true"],
            },
        },
        "NetworkLatencyRecoveryRunbook": {
            "name": "NetworkLatencyRecoveryRunbook",
            "description": "Handle network latency via rolling restart.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": "deployment/{{target_service}}",
                    "params": {"namespace": "production", "grace_period_seconds": 30},
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"],
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": ["pod_ready == true", "service_latency_p95 < 200.0"],
            },
        },
        "PacketLossRecoveryRunbook": {
            "name": "PacketLossRecoveryRunbook",
            "description": "Handle packet loss via rolling restart.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": "deployment/{{target_service}}",
                    "params": {"namespace": "production", "grace_period_seconds": 30},
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"],
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": ["pod_ready == true", "service_error_rate < 0.01"],
            },
        },
        "DiskIORecoveryRunbook": {
            "name": "DiskIORecoveryRunbook",
            "description": "Handle disk I/O bottlenecks via rolling restart.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": "deployment/{{target_service}}",
                    "params": {"namespace": "production", "grace_period_seconds": 30},
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"],
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": ["pod_ready == true"],
            },
        },
        "SocketExhaustionRecoveryRunbook": {
            "name": "SocketExhaustionRecoveryRunbook",
            "description": "Handle socket exhaustion by scaling replicas.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "SCALE_REPLICAS",
                    "target": "deployment/{{target_service}}",
                    "params": {"namespace": "production", "replicas": 3},
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"],
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": ["pod_ready == true"],
            },
        },
        "DefaultRecoveryRunbook": {
            "name": "DefaultRecoveryRunbook",
            "description": "Fallback restart for code-level / unknown faults (RE3 f1-f5).",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": "deployment/{{target_service}}",
                    "params": {"namespace": "production", "grace_period_seconds": 30},
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"],
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": ["pod_ready == true"],
            },
        },
    }


def resolve_runbook_for_fault(fault_type: str) -> str:
    return FAULT_RUNBOOK_MAPPING.get(fault_type, "DefaultRecoveryRunbook")


def write_runbooks(path: str = RUNBOOKS_PATH) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(get_runbooks(), f, indent=2)
    return path
