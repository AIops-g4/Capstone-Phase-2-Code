import os
import sys
import json
import time
import subprocess
import requests
import uuid
import math
from datetime import datetime, timedelta, timezone
import jsonschema

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AI_ENGINE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.append(AI_ENGINE_DIR)

try:
    from src.config import API_HOST, API_PORT, GROUND_TRUTH_PATH, DATASET_DIR
except ImportError:
    API_HOST = "127.0.0.1"
    API_PORT = 8050
    DATASET_DIR = os.path.join(AI_ENGINE_DIR, "dataset")
    GROUND_TRUTH_PATH = os.path.join(DATASET_DIR, "ground_truth.json")

BASE_URL = f"http://{API_HOST}:{API_PORT}"

# =====================================================================
#                      OFFICIAL JSON SCHEMAS FROM CONTRACT
# =====================================================================

DETECT_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DetectRequest",
    "type": "object",
    "properties": {
        "correlation_id": {
            "type": "string",
            "format": "uuid"
        },
        "idempotency_key": {
            "type": "string",
            "format": "uuid"
        },
        "dry_run_mode": {
            "type": "boolean"
        },
        "telemetry_window": {
            "type": "array",
            "items": {
                "type": "object"
            }
        }
    },
    "required": ["idempotency_key", "dry_run_mode", "telemetry_window"],
    "additionalProperties": False
}

DETECT_RESPONSE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DetectResponse",
    "type": "object",
    "properties": {
        "anomaly_detected": { "type": "boolean" },
        "severity": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
        "anomaly_context": {
            "type": "object",
            "properties": {
                "target_service": {
                    "anyOf": [
                        { "type": "string" },
                        { "type": "array", "items": { "type": "string" } }
                    ]
                },
                "suspected_fault_type": { "type": "string" },
                "system": { "type": "string" },
                "namespace": { "type": "string" },
                "deployment": { "type": "string" },
                "trigger_metric": { "type": "string" },
                "trigger_value": { "type": "number" }
            },
            "required": ["target_service", "suspected_fault_type", "system"],
            "additionalProperties": False
        },
        "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
        "reasoning": {
            "type": "string",
            "maxLength": 300
        },
        "correlation_id": { "type": "string", "format": "uuid" }
    },
    "required": ["anomaly_detected", "severity", "confidence", "reasoning", "correlation_id"],
    "additionalProperties": False
}

DECIDE_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DecideRequest",
    "type": "object",
    "properties": {
        "correlation_id": {
            "type": "string",
            "format": "uuid"
        },
        "idempotency_key": {
            "type": "string",
            "format": "uuid"
        },
        "dry_run_mode": {
            "type": "boolean"
        },
        "anomaly_context": {
            "type": "object",
            "properties": {
                "target_service": {
                    "anyOf": [
                        { "type": "string" },
                        { "type": "array", "items": { "type": "string" } }
                    ]
                },
                "suspected_fault_type": { "type": "string" },
                "system": { "type": "string" },
                "namespace": { "type": "string" },
                "deployment": { "type": "string" },
                "trigger_metric": { "type": "string" },
                "trigger_value": { "type": "number" }
            },
            "required": ["target_service", "suspected_fault_type", "system"],
            "additionalProperties": False
        }
    },
    "required": ["correlation_id", "idempotency_key", "dry_run_mode", "anomaly_context"],
    "additionalProperties": False
}

DECIDE_RESPONSE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "DecideResponse",
    "type": "object",
    "properties": {
        "matched_runbook": { "type": "string" },
        "pattern_type": { 
            "type": "string", 
            "enum": ["urgent", "deferred"] 
        },
        "action_plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": { "type": "integer" },
                    "action": { 
                        "type": "string", 
                        "enum": ["RESTART_DEPLOYMENT", "PATCH_MEMORY_LIMIT", "SCALE_REPLICAS", "ROLLOUT_UNDO", "ROTATE_SECRET"] 
                    },
                    "target": { "type": "string" },
                    "params": {
                        "type": "object",
                        "properties": {
                            "namespace": { "type": "string" },
                            "container": { "type": "string" },
                            "memory_request_mb": { "type": "integer" },
                            "memory_limit_mb": { "type": "integer" },
                            "replicas": { "type": "integer" },
                            "secret_name": { "type": "string" },
                            "grace_period_seconds": { "type": "integer" }
                        },
                        "required": ["namespace"]
                    }
                },
                "required": ["step", "action", "target", "params"],
                "additionalProperties": False
            }
        },
        "blast_radius_config": {
            "type": "object",
            "properties": {
                "max_pod_impact_pct": { "type": "integer" },
                "circuit_breaker_error_rate": { "type": "number" },
                "allowed_namespaces": {
                    "type": "array",
                    "items": { "type": "string" }
                }
            },
            "required": ["max_pod_impact_pct", "circuit_breaker_error_rate", "allowed_namespaces"],
            "additionalProperties": False
        },
        "verify_policy": {
            "type": "object",
            "properties": {
                "window_seconds": { "type": "integer" },
                "success_conditions": {
                    "type": "array",
                    "items": { "type": "string" }
                }
            },
            "required": ["window_seconds"],
            "additionalProperties": False
        },
        "correlation_id": { "type": "string", "format": "uuid" },
        "idempotency_key": { "type": "string", "format": "uuid" },
        "dry_run_mode": { "type": "boolean" },
        "cost_cap_exceeded": { "type": "boolean" }
    },
    "required": ["matched_runbook", "pattern_type", "action_plan", "blast_radius_config", "verify_policy", "correlation_id", "idempotency_key", "dry_run_mode"],
    "additionalProperties": False
}

VERIFY_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "VerifyRequest",
    "type": "object",
    "properties": {
        "correlation_id": {
            "type": "string",
            "format": "uuid"
        },
        "idempotency_key": {
            "type": "string",
            "format": "uuid"
        },
        "dry_run_mode": {
            "type": "boolean"
        },
        "action_executed": {
            "type": "object",
            "properties": {
                "action": { "type": "string" },
                "target": { "type": "string" },
                "status": { "type": "string", "enum": ["COMPLETED", "FAILED"] },
                "execution_time_seconds": { "type": "integer" }
            },
            "required": ["action", "target", "status"],
            "additionalProperties": False
        },
        "post_telemetry_window": {
            "type": "array",
            "items": {
                "type": "object"
            }
        }
    },
    "required": ["correlation_id", "idempotency_key", "dry_run_mode", "action_executed", "post_telemetry_window"],
    "additionalProperties": False
}

VERIFY_RESPONSE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "VerifyResponse",
    "type": "object",
    "properties": {
        "success": { "type": "boolean" },
        "regression_detected": { "type": "boolean" },
        "next_action": { 
            "type": "string", 
            "enum": ["DONE", "RETRY", "ROLLBACK", "ESCALATE"] 
        },
        "escalation_bundle": {
            "type": "object",
            "properties": {
                "reason": { "type": "string" },
                "logs": { "type": "array", "items": { "type": "string" } },
                "metrics": { "type": "object" }
            },
            "additionalProperties": False
        }
    },
    "required": ["success", "regression_detected", "next_action"],
    "additionalProperties": False
}

# =====================================================================
#                           HELPER FUNCTIONS
# =====================================================================

def validate_json(data, schema, label):
    """Validates data against a schema and raises clean errors on failure."""
    try:
        jsonschema.validate(instance=data, schema=schema)
        print(f"  [PASS] Schema validation for {label} succeeded.")
        return True
    except jsonschema.exceptions.ValidationError as err:
        print(f"  [FAIL] Schema validation for {label} failed!")
        print(f"         Error message: {err.message}")
        print(f"         Path: {list(err.path)}")
        print(f"         Data: {json.dumps(data, indent=2)}")
        return False

def load_telemetry_data(run_key):
    """Loads and packages metrics and logs into a compliant telemetry window."""
    with open(GROUND_TRUTH_PATH, "r") as f:
        ground_truth = json.load(f)
    
    run_info = ground_truth[run_key]
    service_fault = run_info["service_fault"]
    run_id = run_info["run_id"]
    inject_time = run_info["inject_time"]
    
    run_dir = os.path.join(DATASET_DIR, service_fault, run_id)
    
    # Import pandas locally to avoid startup lag
    import pandas as pd
    df_metrics = pd.read_csv(os.path.join(run_dir, "simple_metrics.csv"))
    df_logs = pd.read_csv(os.path.join(run_dir, "logs.csv"))
    
    # Find anomaly injection row
    inject_row_idx = df_metrics[df_metrics["time"] >= inject_time].index.min()
    if pd.isna(inject_row_idx):
        inject_row_idx = 720
        
    # Standard sliding window ending 15s after anomaly
    stream_end_idx = inject_row_idx + 15
    stream_start_idx = max(0, stream_end_idx - 150)
    
    df_window_metrics = df_metrics.iloc[stream_start_idx:stream_end_idx+1]
    time_start = int(df_window_metrics["time"].min())
    time_end = int(df_window_metrics["time"].max())
    
    df_logs["time_sec"] = (df_logs["timestamp"] // 1000000000).astype(int)
    df_window_logs = df_logs[(df_logs["time_sec"] >= time_start) & (df_logs["time_sec"] <= time_end)]
    
    telemetry_payload = []
    
    # Metrics
    for _, row in df_window_metrics.iterrows():
        t_sec = int(row["time"])
        ts_iso = datetime.fromtimestamp(t_sec, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        for col in df_window_metrics.columns:
            if col == "time":
                continue
            val = float(row[col])
            if math.isnan(val) or math.isinf(val):
                continue
            service_name = col.split("_")[0]
            metric_name = "_".join(col.split("_")[1:])
            telemetry_payload.append({
                "ts": ts_iso,
                "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
                "service": service_name,
                "signal_name": metric_name,
                "value": val,
                "labels": {"namespace": "production"}
            })
            
    # Logs
    for _, row in df_window_logs.iterrows():
        t_sec = int(row["time_sec"])
        ts_iso = datetime.fromtimestamp(t_sec, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        telemetry_payload.append({
            "ts": ts_iso,
            "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "service": str(row["container_name"]),
            "signal_name": "application_log_event",
            "value": str(row["message"]),
            "labels": {"level": str(row["level"]), "namespace": "production"}
        })
        
    return telemetry_payload

def load_symptom_telemetry():
    """Loads anomalous telemetry and maps the service name to 'frontend' to simulate a downstream symptom."""
    telemetry = load_telemetry_data("checkoutservice_cpu_1")
    mapped_telemetry = []
    for point in telemetry:
        # Discard original healthy frontend telemetry to prevent it from overwriting the mapped spiked metrics
        if point["service"] == "frontend":
            continue
        if point["service"] == "checkoutservice":
            point["service"] = "frontend"
        if point["signal_name"] == "application_log_event":
            point["value"] = point["value"].replace("checkoutservice", "frontend")
        mapped_telemetry.append(point)
    return mapped_telemetry

def generate_healthy_telemetry():
    """Generates a perfectly normal telemetry window (no anomaly)."""
    telemetry_payload = []
    ts_base = datetime.now(timezone.utc)
    for i in range(10):
        ts_iso = (ts_base + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
        # Normal CPU metric (0.1 to 0.3)
        telemetry_payload.append({
            "ts": ts_iso,
            "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "service": "checkoutservice",
            "signal_name": "cpu",
            "value": 0.15 + (i * 0.01),
            "labels": {"namespace": "production"}
        })
        # Normal info log
        telemetry_payload.append({
            "ts": ts_iso,
            "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "service": "checkoutservice",
            "signal_name": "application_log_event",
            "value": "Checkout completed successfully.",
            "labels": {"level": "info", "namespace": "production"}
        })
    return telemetry_payload

def generate_post_healing_telemetry(service_name):
    """Generates restored telemetry for verification phase."""
    telemetry_payload = []
    ts_base = datetime.now(timezone.utc)
    for i in range(10):
        ts_iso = (ts_base + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
        telemetry_payload.append({
            "ts": ts_iso,
            "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "service": service_name,
            "signal_name": "service_error_rate",
            "value": 0.0,
            "labels": {"namespace": "production"}
        })
        telemetry_payload.append({
            "ts": ts_iso,
            "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "service": service_name,
            "signal_name": "latency",
            "value": 0.03,
            "labels": {"namespace": "production"}
        })
    return telemetry_payload

# =====================================================================
#                          VERIFICATION SUITE
# =====================================================================

def run_contract_verification():
    print("=====================================================================")
    print("           AI ENGINE API CONTRACT COMPLIANCE TEST SUITE             ")
    print("=====================================================================\n")
    
    # 1. Start FastAPI Server in the background
    print("[SERVER] Starting FastAPI server on port 8050...")
    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.server:app", "--host", "127.0.0.1", "--port", "8050"],
        cwd=AI_ENGINE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to start up
    time.sleep(3.0)
    
    # Verify server is responsive
    try:
        health_check = requests.get(f"{BASE_URL}/docs")
        if health_check.status_code == 200:
            print("[SERVER] Server started successfully and is responsive on port 8050.\n")
        else:
            print(f"[SERVER ERROR] Server returned status code {health_check.status_code}.\n")
            server_process.terminate()
            return False
    except Exception as e:
        print(f"[SERVER ERROR] Could not connect to server: {e}\n")
        server_process.terminate()
        return False
        
    results = {
        "scenarios": [],
        "all_passed": True
    }
    
    def log_scenario(name, passed):
        results["scenarios"].append({"name": name, "passed": passed})
        if not passed:
            results["all_passed"] = False

    # Variables to carry over states
    primary_corr_id = None
    primary_anomaly_context = None
    primary_executed_action = None

    try:
        # -----------------------------------------------------------------
        # SCENARIO 1: Healthy Window (No Anomaly)
        # -----------------------------------------------------------------
        print("-----------------------------------------------------------------")
        print("SCENARIO 1: Telemetry Window for a HEALTHY system")
        print("-----------------------------------------------------------------")
        
        healthy_telemetry = generate_healthy_telemetry()
        detect_req = {
            "correlation_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "telemetry_window": healthy_telemetry
        }
        
        # Validate Request Schema
        req_valid = validate_json(detect_req, DETECT_REQUEST_SCHEMA, "DetectRequest (Healthy)")
        
        # Send Request
        res = requests.post(f"{BASE_URL}/v1/detect", json=detect_req)
        print(f"  Response Status: {res.status_code}")
        
        # Validate Response Schema
        res_data = res.json()
        res_valid = validate_json(res_data, DETECT_RESPONSE_SCHEMA, "DetectResponse (Healthy)")
        
        # Specific business assertions (anomaly_context MUST NOT BE PRESENT in output JSON)
        business_valid = (
            res.status_code == 200 and
            res_data["anomaly_detected"] is False and
            res_data["severity"] == 0.0 and
            "anomaly_context" not in res_data  # Verified that None-value field is completely omitted!
        )
        print(f"  Business logic check: {'[PASS]' if business_valid else '[FAIL]'}")
        
        scenario_passed = req_valid and res_valid and business_valid
        log_scenario("Scenario 1: Healthy Window", scenario_passed)
        print(f"Scenario 1 Result: {'SUCCESS' if scenario_passed else 'FAILED'}\n")


        # -----------------------------------------------------------------
        # SCENARIO 2: Primary Anomaly Detection
        # -----------------------------------------------------------------
        print("-----------------------------------------------------------------")
        print("SCENARIO 2: Anomalous Window (Primary Incident: CPU Saturation)")
        print("-----------------------------------------------------------------")
        
        anomaly_telemetry = load_telemetry_data("checkoutservice_cpu_1")
        primary_correlation_id = str(uuid.uuid4())
        
        detect_req = {
            "correlation_id": primary_correlation_id,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "telemetry_window": anomaly_telemetry
        }
        
        # Validate Request Schema
        req_valid_detect = validate_json(detect_req, DETECT_REQUEST_SCHEMA, "DetectRequest (Anomaly)")
        
        # Send /v1/detect
        res_detect = requests.post(f"{BASE_URL}/v1/detect", json=detect_req)
        print(f"  Detect Response Status: {res_detect.status_code}")
        
        # Validate Response Schema
        res_detect_data = res_detect.json()
        res_valid_detect = validate_json(res_detect_data, DETECT_RESPONSE_SCHEMA, "DetectResponse (Anomaly)")
        
        target_service = res_detect_data["anomaly_context"]["target_service"] if res_detect_data.get("anomaly_context") else None
        if isinstance(target_service, list) and target_service:
            target_service = target_service[0]
            
        detect_business_valid = (
            res_detect.status_code == 200 and
            res_detect_data["anomaly_detected"] is True and
            res_detect_data["anomaly_context"] is not None and
            target_service == "checkoutservice" and
            res_detect_data["anomaly_context"]["suspected_fault_type"] == "cpu"
        )
        print(f"  Detect Business logic check: {'[PASS]' if detect_business_valid else '[FAIL]'}")
        
        # Save states for subsequent scenarios
        primary_corr_id = res_detect_data["correlation_id"]
        primary_anomaly_context = res_detect_data["anomaly_context"]
        
        scenario_passed = req_valid_detect and res_valid_detect and detect_business_valid
        log_scenario("Scenario 2: Primary Anomaly Detection", scenario_passed)
        print(f"Scenario 2 Result: {'SUCCESS' if scenario_passed else 'FAILED'}\n")


        # -----------------------------------------------------------------
        # SCENARIO 3: Primary Incident Decision
        # -----------------------------------------------------------------
        print("-----------------------------------------------------------------")
        print("SCENARIO 3: Primary Incident Action Plan Decision")
        print("-----------------------------------------------------------------")
        
        decide_req = {
            "correlation_id": primary_corr_id,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "anomaly_context": primary_anomaly_context
        }
        
        # Validate Request Schema
        req_valid_decide = validate_json(decide_req, DECIDE_REQUEST_SCHEMA, "DecideRequest (Primary)")
        
        # Send /v1/decide
        res_decide = requests.post(f"{BASE_URL}/v1/decide", json=decide_req)
        print(f"  Decide Response Status: {res_decide.status_code}")
        
        # Validate Response Schema
        res_decide_data = res_decide.json()
        res_valid_decide = validate_json(res_decide_data, DECIDE_RESPONSE_SCHEMA, "DecideResponse (Primary)")
        
        decide_business_valid = (
            res_decide.status_code == 200 and
            res_decide_data["matched_runbook"] == "CPUSaturationRecoveryRunbook" and
            res_decide_data["pattern_type"] == "urgent" and
            len(res_decide_data["action_plan"]) > 0 and
            res_decide_data["action_plan"][0]["action"] == "SCALE_REPLICAS"
        )
        print(f"  Decide Business logic check: {'[PASS]' if decide_business_valid else '[FAIL]'}")
        
        # Save executed action for later verification scenario
        primary_executed_action = res_decide_data["action_plan"][0]
        
        scenario_passed = req_valid_decide and res_valid_decide and decide_business_valid
        log_scenario("Scenario 3: Primary Incident Decision", scenario_passed)
        print(f"Scenario 3 Result: {'SUCCESS' if scenario_passed else 'FAILED'}\n")


        # -----------------------------------------------------------------
        # SCENARIO 4: Duplicate Alert Deduplication (Incident is active!)
        # -----------------------------------------------------------------
        print("-----------------------------------------------------------------")
        print("SCENARIO 4: Duplicate Alert for Active Incident (Deduplication)")
        print("-----------------------------------------------------------------")
        
        # A new alarm is raised on checkoutservice while the primary incident is still active.
        dup_idempotency_key = str(uuid.uuid4())
        detect_req_dup = {
            "correlation_id": str(uuid.uuid4()),  # Client tries to send new correlation ID
            "idempotency_key": dup_idempotency_key,
            "dry_run_mode": False,
            "telemetry_window": anomaly_telemetry
        }
        
        # Send /v1/detect
        res_detect_dup = requests.post(f"{BASE_URL}/v1/detect", json=detect_req_dup)
        print(f"  Detect Duplicate Status: {res_detect_dup.status_code}")
        
        # Validate Response Schema
        res_detect_dup_data = res_detect_dup.json()
        res_valid_detect_dup = validate_json(res_detect_dup_data, DETECT_RESPONSE_SCHEMA, "DetectResponse (Duplicate)")
        
        # Verify deduplication correlated it to the primary correlation ID
        detect_dup_valid = (
            res_detect_dup.status_code == 200 and
            res_detect_dup_data["correlation_id"] == primary_corr_id and
            "[CORRELATED ALERT]" in res_detect_dup_data["reasoning"]
        )
        print(f"  Deduplication Correlation check (returned ID == primary ID): {'[PASS]' if detect_dup_valid else '[FAIL]'}")
        
        # Now, call /v1/decide with this duplicate context
        decide_req_dup = {
            "correlation_id": primary_corr_id,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "anomaly_context": res_detect_dup_data["anomaly_context"]
        }
        
        # Validate Request Schema
        req_valid_decide_dup = validate_json(decide_req_dup, DECIDE_REQUEST_SCHEMA, "DecideRequest (Duplicate)")
        
        # Send /v1/decide
        res_decide_dup = requests.post(f"{BASE_URL}/v1/decide", json=decide_req_dup)
        print(f"  Decide Duplicate Status: {res_decide_dup.status_code}")
        
        # Validate Response Schema
        res_decide_dup_data = res_decide_dup.json()
        res_valid_decide_dup = validate_json(res_decide_dup_data, DECIDE_RESPONSE_SCHEMA, "DecideResponse (Duplicate)")
        
        # Verify action plan is empty for duplicate suppression
        decide_dup_business_valid = (
            res_decide_dup.status_code == 200 and
            res_decide_dup_data["matched_runbook"] == "CorrelatedSymptomSuppression" and
            res_decide_dup_data["action_plan"] == []  # Empty action plan!
        )
        print(f"  Duplicate Suppression check (action_plan is empty): {'[PASS]' if decide_dup_business_valid else '[FAIL]'}")
        
        scenario_passed = res_valid_detect_dup and detect_dup_valid and req_valid_decide_dup and res_valid_decide_dup and decide_dup_business_valid
        log_scenario("Scenario 4: Duplicate Alert Deduplication", scenario_passed)
        print(f"Scenario 4 Result: {'SUCCESS' if scenario_passed else 'FAILED'}\n")


        # -----------------------------------------------------------------
        # SCENARIO 5: Downstream Symptom Alert (Correlation Check)
        # -----------------------------------------------------------------
        print("-----------------------------------------------------------------")
        print("SCENARIO 5: Downstream Symptom Alert Correlation")
        print("-----------------------------------------------------------------")
        
        # We send telemetry window showing high CPU on frontend (downstream of checkoutservice)
        frontend_telemetry = load_symptom_telemetry()
        
        detect_req_sym = {
            "correlation_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "telemetry_window": frontend_telemetry
        }
        
        # Send /v1/detect
        res_detect_sym = requests.post(f"{BASE_URL}/v1/detect", json=detect_req_sym)
        print(f"  Detect Symptom Status: {res_detect_sym.status_code}")
        
        # Validate Response Schema
        res_detect_sym_data = res_detect_sym.json()
        print(f"  Returned Correlation ID: {res_detect_sym_data.get('correlation_id')}")
        print(f"  Returned target_service: {res_detect_sym_data.get('anomaly_context', {}).get('target_service') if res_detect_sym_data.get('anomaly_context') else None}")
        print(f"  Returned reasoning:      {res_detect_sym_data.get('reasoning')}")
        res_valid_detect_sym = validate_json(res_detect_sym_data, DETECT_RESPONSE_SCHEMA, "DetectResponse (Symptom)")
        
        # Verify correlation correlated it to the active checkoutservice incident (returned primary_corr_id)
        target_service_sym = res_detect_sym_data["anomaly_context"]["target_service"] if res_detect_sym_data.get("anomaly_context") else None
        if isinstance(target_service_sym, list) and target_service_sym:
            target_service_sym = target_service_sym[0]
            
        detect_sym_valid = (
            res_detect_sym.status_code == 200 and
            res_detect_sym_data["correlation_id"] == primary_corr_id and
            target_service_sym == "frontend" and
            "[CORRELATED ALERT]" in res_detect_sym_data["reasoning"]
        )
        print(f"  Symptom Correlation check (returned ID == primary ID): {'[PASS]' if detect_sym_valid else '[FAIL]'}")
        
        # Now, call /v1/decide with this symptom context
        decide_req_sym = {
            "correlation_id": primary_corr_id,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "anomaly_context": res_detect_sym_data["anomaly_context"]
        }
        
        # Validate Request Schema
        req_valid_decide_sym = validate_json(decide_req_sym, DECIDE_REQUEST_SCHEMA, "DecideRequest (Symptom)")
        
        # Send /v1/decide
        res_decide_sym = requests.post(f"{BASE_URL}/v1/decide", json=decide_req_sym)
        print(f"  Decide Symptom Status: {res_decide_sym.status_code}")
        
        # Validate Response Schema
        res_decide_sym_data = res_decide_sym.json()
        res_valid_decide_sym = validate_json(res_decide_sym_data, DECIDE_RESPONSE_SCHEMA, "DecideResponse (Symptom)")
        
        # Verify action plan is empty for symptom suppression
        decide_sym_business_valid = (
            res_decide_sym.status_code == 200 and
            res_decide_sym_data["matched_runbook"] == "CorrelatedSymptomSuppression" and
            res_decide_sym_data["action_plan"] == []  # Empty action plan!
        )
        print(f"  Symptom Suppression check (action_plan is empty): {'[PASS]' if decide_sym_business_valid else '[FAIL]'}")
        
        scenario_passed = res_valid_detect_sym and detect_sym_valid and req_valid_decide_sym and res_valid_decide_sym and decide_sym_business_valid
        log_scenario("Scenario 5: Downstream Symptom Correlation", scenario_passed)
        print(f"Scenario 5 Result: {'SUCCESS' if scenario_passed else 'FAILED'}\n")


        # -----------------------------------------------------------------
        # SCENARIO 6: Primary Incident Verification & Incident Closure
        # -----------------------------------------------------------------
        print("-----------------------------------------------------------------")
        print("SCENARIO 6: Primary Incident Verification & Closure")
        print("-----------------------------------------------------------------")
        
        post_telemetry = generate_post_healing_telemetry("checkoutservice")
        verify_req = {
            "correlation_id": primary_corr_id,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "action_executed": {
                "action": primary_executed_action["action"],
                "target": primary_executed_action["target"],
                "status": "COMPLETED",
                "execution_time_seconds": 45
            },
            "post_telemetry_window": post_telemetry
        }
        
        # Validate Request Schema
        req_valid_verify = validate_json(verify_req, VERIFY_REQUEST_SCHEMA, "VerifyRequest (Verification)")
        
        # Send /v1/verify
        res_verify = requests.post(f"{BASE_URL}/v1/verify", json=verify_req)
        print(f"  Verify Response Status: {res_verify.status_code}")
        
        # Validate Response Schema
        res_verify_data = res_verify.json()
        res_valid_verify = validate_json(res_verify_data, VERIFY_RESPONSE_SCHEMA, "VerifyResponse (Verification)")
        
        # Verify success & next_action="DONE" and escalation_bundle is completely omitted (since success=True)
        verify_business_valid = (
            res_verify.status_code == 200 and
            res_verify_data["success"] is True and
            res_verify_data["next_action"] == "DONE" and
            "escalation_bundle" not in res_verify_data  # Verified that None-value optional field is omitted!
        )
        print(f"  Verify Business logic check: {'[PASS]' if verify_business_valid else '[FAIL]'}")
        
        scenario_passed = req_valid_verify and res_valid_verify and verify_business_valid
        log_scenario("Scenario 6: Primary Incident Verification & Closure", scenario_passed)
        print(f"Scenario 6 Result: {'SUCCESS' if scenario_passed else 'FAILED'}\n")


        # -----------------------------------------------------------------
        # SCENARIO 7: Schema Violations (Negative Testing)
        # -----------------------------------------------------------------
        print("-----------------------------------------------------------------")
        print("SCENARIO 7: Schema Violations (Negative Testing)")
        print("-----------------------------------------------------------------")
        
        # Test case A: Send request with missing required field
        bad_req_missing = {
            "dry_run_mode": False,
            "telemetry_window": []
        }  # Missing idempotency_key
        
        res_bad_missing = requests.post(f"{BASE_URL}/v1/detect", json=bad_req_missing)
        print(f"  Missing required field: Status Code = {res_bad_missing.status_code} (Expected: 422)")
        missing_passed = res_bad_missing.status_code == 422
        
        # Test case B: Send request with an extra forbidden field (tests additionalProperties: false)
        bad_req_extra = {
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "telemetry_window": [],
            "unknown_extra_field": "some_value"  # Forbidden!
        }
        
        res_bad_extra = requests.post(f"{BASE_URL}/v1/detect", json=bad_req_extra)
        print(f"  Forbidden extra field:  Status Code = {res_bad_extra.status_code} (Expected: 422)")
        extra_passed = res_bad_extra.status_code == 422
        
        # Test case C: Send decide response with invalid enum value (Simulated via client inputs if server validated it)
        bad_req_enum = {
            "correlation_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "action_executed": {
                "action": "SCALE_REPLICAS",
                "target": "deployment/checkoutservice",
                "status": "INVALID_STATUS_VALUE",  # Forbidden enum value!
                "execution_time_seconds": 45
            },
            "post_telemetry_window": []
        }
        
        res_bad_enum = requests.post(f"{BASE_URL}/v1/verify", json=bad_req_enum)
        print(f"  Invalid enum value:     Status Code = {res_bad_enum.status_code} (Expected: 422)")
        enum_passed = res_bad_enum.status_code == 422
        
        scenario_passed = missing_passed and extra_passed and enum_passed
        log_scenario("Scenario 7: Schema Violations (Negative Testing)", scenario_passed)
        print(f"Scenario 7 Result: {'SUCCESS' if scenario_passed else 'FAILED'}\n")

    except Exception as e:
        print(f"[UNEXPECTED EXCEPTION] {e}")
        import traceback
        traceback.print_exc()
        results["all_passed"] = False
        
    finally:
        # Shut down the server cleanly
        print("[SERVER] Terminating FastAPI server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
            print("[SERVER] Server terminated successfully.")
        except subprocess.TimeoutExpired:
            print("[SERVER] Server did not exit in time. Killing it...")
            server_process.kill()
            server_process.wait()
            print("[SERVER] Server killed.")
            
    # =====================================================================
    #                         FINAL SUMMARY REPORT
    # =====================================================================
    print("\n=====================================================================")
    print("                      CONTRACT COMPLIANCE SUMMARY                     ")
    print("=====================================================================")
    
    for sc in results["scenarios"]:
        status_label = "✅ PASSED" if sc["passed"] else "❌ FAILED"
        print(f" - {sc['name']}: {status_label}")
        
    if results["all_passed"]:
        print("\n🎉 ALL TESTS PASSED! The AI Engine satisfies 100% of the API contracts!")
        print("   Both requests and responses strictly comply with schemas and enums.")
        print("=====================================================================\n")
        return True
    else:
        print("\n❌ SOME TESTS FAILED! Review the log above for schema or business violations.")
        print("=====================================================================\n")
        return False

if __name__ == "__main__":
    success = run_contract_verification()
    sys.exit(0 if success else 1)
