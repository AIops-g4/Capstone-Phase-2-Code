import jsonschema
from jsonschema import validate

# 1. TelemetryDataPoint Schema
TELEMETRY_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "TelemetryDataPoint",
    "type": "object",
    "properties": {
        "ts": {"type": "string", "format": "date-time"},
        "tenant_id": {"type": "string", "format": "uuid"},
        "service": {"type": "string"},
        "signal_name": {
            "type": "string",
            "enum": [
                "service_error_rate",
                "service_latency_p95",
                "container_resource_usage",
                "application_log_event",
                "distributed_trace_error_event",
                "pod_oom_event",
                "service_unhealthy",
                "queue_backlog",
                "service_throughput_rps",
                "container_restart_count",
                "secret_expiry_warning",
                "db_connection_pool_saturation"
            ]
        },
        "value": {"type": ["number", "string"]},
        "labels": {
            "type": "object",
            "properties": {
                "system": {"type": "string"},
                "namespace": {"type": "string"},
                "deployment": {"type": "string"},
                "pod_name": {"type": "string"},
                "container": {"type": "string"},
                "endpoint": {"type": "string"},
                "trace_id": {"type": "string"},
                "span_id": {"type": "string"},
                "operation": {"type": "string"},
                "level": {"type": "string"}
            },
            "required": ["system"],
            "additionalProperties": True
        }
    },
    "required": ["ts", "tenant_id", "service", "signal_name", "value"],
    "additionalProperties": False
}

# 2. DetectRequest Schema
DETECT_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DetectRequest",
    "type": "object",
    "properties": {
        "correlation_id": {"type": "string", "format": "uuid"},
        "idempotency_key": {"type": "string", "format": "uuid"},
        "dry_run_mode": {"type": "boolean"},
        "telemetry_window": {
            "type": "array",
            "items": TELEMETRY_SCHEMA
        }
    },
    "required": ["idempotency_key", "dry_run_mode", "telemetry_window"],
    "additionalProperties": False
}

# 3. DetectResponse Schema
DETECT_RESPONSE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DetectResponse",
    "type": "object",
    "properties": {
        "anomaly_detected": {"type": "boolean"},
        "severity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "anomaly_context": {
            "type": "object",
            "properties": {
                "target_service": {"type": "string"},
                "suspected_fault_type": {"type": "string"},
                "system": {"type": "string"},
                "namespace": {"type": "string"},
                "deployment": {"type": "string"},
                "trigger_metric": {"type": "string"},
                "trigger_value": {"type": "number"}
            },
            "required": ["target_service", "suspected_fault_type", "system"]
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string", "maxLength": 300},
        "correlation_id": {"type": "string", "format": "uuid"}
    },
    "required": ["anomaly_detected", "severity", "confidence", "reasoning", "correlation_id"],
    "additionalProperties": False
}

# 4. DecideRequest Schema
DECIDE_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DecideRequest",
    "type": "object",
    "properties": {
        "correlation_id": {"type": "string", "format": "uuid"},
        "idempotency_key": {"type": "string", "format": "uuid"},
        "dry_run_mode": {"type": "boolean"},
        "anomaly_context": {
            "type": "object",
            "properties": {
                "target_service": {"type": "string"},
                "suspected_fault_type": {"type": "string"},
                "system": {"type": "string"},
                "namespace": {"type": "string"},
                "deployment": {"type": "string"},
                "trigger_metric": {"type": "string"},
                "trigger_value": {"type": "number"}
            },
            "required": ["target_service", "suspected_fault_type", "system"]
        }
    },
    "required": ["correlation_id", "idempotency_key", "dry_run_mode", "anomaly_context"],
    "additionalProperties": False
}

# 5. DecideResponse Schema
DECIDE_RESPONSE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DecideResponse",
    "type": "object",
    "properties": {
        "matched_runbook": {"type": "string"},
        "pattern_type": {"type": "string", "enum": ["urgent", "deferred"]},
        "action_plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "action": {
                        "type": "string",
                        "enum": ["RESTART_DEPLOYMENT", "PATCH_MEMORY_LIMIT", "SCALE_REPLICAS", "ROLLOUT_UNDO", "ROTATE_SECRET"]
                    },
                    "target": {"type": "string"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "namespace": {"type": "string"},
                            "container": {"type": "string"},
                            "memory_request_mb": {"type": "integer"},
                            "memory_limit_mb": {"type": "integer"},
                            "replicas": {"type": "integer"},
                            "secret_name": {"type": "string"},
                            "grace_period_seconds": {"type": "integer"}
                        },
                        "required": ["namespace"]
                    }
                },
                "required": ["step", "action", "target", "params"]
            }
        },
        "blast_radius_config": {
            "type": "object",
            "properties": {
                "max_pod_impact_pct": {"type": "integer"},
                "circuit_breaker_error_rate": {"type": "number"},
                "allowed_namespaces": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            },
            "required": ["max_pod_impact_pct", "circuit_breaker_error_rate", "allowed_namespaces"]
        },
        "verify_policy": {
            "type": "object",
            "properties": {
                "window_seconds": {"type": "integer"},
                "success_conditions": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            },
            "required": ["window_seconds"]
        },
        "correlation_id": {"type": "string", "format": "uuid"},
        "idempotency_key": {"type": "string", "format": "uuid"},
        "dry_run_mode": {"type": "boolean"},
        "cost_cap_exceeded": {"type": "boolean"}
    },
    "required": [
        "matched_runbook",
        "pattern_type",
        "action_plan",
        "blast_radius_config",
        "verify_policy",
        "correlation_id",
        "idempotency_key",
        "dry_run_mode"
    ],
    "additionalProperties": False
}

# 6. VerifyRequest Schema
VERIFY_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "VerifyRequest",
    "type": "object",
    "properties": {
        "correlation_id": {"type": "string", "format": "uuid"},
        "idempotency_key": {"type": "string", "format": "uuid"},
        "dry_run_mode": {"type": "boolean"},
        "action_executed": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "target": {"type": "string"},
                "status": {"type": "string", "enum": ["COMPLETED", "FAILED"]},
                "execution_time_seconds": {"type": "integer"}
            },
            "required": ["action", "target", "status"]
        },
        "post_telemetry_window": {
            "type": "array",
            "items": TELEMETRY_SCHEMA
        }
    },
    "required": ["correlation_id", "idempotency_key", "dry_run_mode", "action_executed", "post_telemetry_window"],
    "additionalProperties": False
}

# 7. VerifyResponse Schema
VERIFY_RESPONSE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "VerifyResponse",
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "regression_detected": {"type": "boolean"},
        "next_action": {"type": "string", "enum": ["DONE", "RETRY", "ROLLBACK", "ESCALATE"]},
        "escalation_bundle": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "logs": {"type": "array", "items": {"type": "string"}},
                "metrics": {"type": "object"}
            }
        }
    },
    "required": ["success", "regression_detected", "next_action"],
    "additionalProperties": False
}

def validate_json(data: dict, schema: dict) -> bool:
    """
    Validate JSON data against a schema.
    Raises jsonschema.ValidationError if invalid.
    """
    validate(instance=data, schema=schema)
    return True
