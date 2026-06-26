#!/usr/bin/env python3
import json
import sys
import urllib.request
import urllib.error
import re
from typing import Dict, Any, Optional, List

# ANSI colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

class APITester:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0

    def _send_request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Dict[str, Any]] = None
    ) -> tuple[int, Any, Dict[str, str]]:
        url = f"{self.base_url}{path}"
        req_headers = {
            "Content-Type": "application/json"
        }
        if headers:
            req_headers.update(headers)

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

        try:
            with urllib.request.urlopen(req) as response:
                status_code = response.status
                resp_body = response.read().decode("utf-8")
                resp_headers = dict(response.info())
                
                if "application/json" in resp_headers.get("Content-Type", "").lower() or resp_body.strip().startswith(("{", "[")):
                    try:
                        resp_body = json.loads(resp_body)
                    except json.JSONDecodeError:
                        pass
                return status_code, resp_body, resp_headers
        except urllib.error.HTTPError as e:
            status_code = e.code
            resp_body = e.read().decode("utf-8")
            resp_headers = dict(e.info())
            if "application/json" in resp_headers.get("Content-Type", "").lower() or resp_body.strip().startswith(("{", "[")):
                try:
                    resp_body = json.loads(resp_body)
                except json.JSONDecodeError:
                    pass
            return status_code, resp_body, resp_headers
        except urllib.error.URLError as e:
            print(f"{RED}[ERROR] Connection failed to {url}: {e.reason}{RESET}")
            sys.exit(1)

    def run_test(self, name: str, method: str, path: str, headers: Optional[Dict[str, str]], body: Optional[Dict[str, Any]], validator) -> None:
        self.total_tests += 1
        print(f"{BLUE}[TEST] {name}...{RESET}")
        
        status_code, resp_body, resp_headers = self._send_request(method, path, headers, body)
        
        try:
            validator(status_code, resp_body, resp_headers)
            print(f"{GREEN}[PASS] {name} - Status: {status_code}{RESET}")
            self.passed_tests += 1
        except AssertionError as e:
            print(f"{RED}[FAIL] {name} - {str(e)}{RESET}")
            print(f"       Response Status: {status_code}")
            print(f"       Response Body: {json.dumps(resp_body, indent=2) if isinstance(resp_body, dict) else resp_body}")
            self.failed_tests += 1
        print("-" * 60)

    def print_summary(self):
        print("\n=== TEST SUMMARY ===")
        print(f"Total Tests Run: {self.total_tests}")
        print(f"Passed: {GREEN}{self.passed_tests}{RESET}")
        print(f"Failed: {RED if self.failed_tests > 0 else GREEN}{self.failed_tests}{RESET}")
        if self.failed_tests > 0:
            sys.exit(1)
        else:
            sys.exit(0)

# Helper validators
def assert_is_string_and_not_empty(val: Any, field_name: str):
    assert isinstance(val, str), f"'{field_name}' must be a string, got {type(val)}"
    assert len(val.strip()) > 0, f"'{field_name}' must not be empty"

def validate_health(status_code: int, body: Any, headers: Dict[str, str]):
    assert status_code == 200, f"Expected status code 200, got {status_code}"
    assert isinstance(body, dict), "Response body is not a JSON object"
    
    assert "status" in body, "Missing required field 'status'"
    assert body["status"] == "healthy", f"Expected 'status' to be 'healthy', got '{body['status']}'"
    assert "timestamp" in body, "Missing required field 'timestamp'"
    assert_is_string_and_not_empty(body["timestamp"], "timestamp")

def validate_ready(status_code: int, body: Any, headers: Dict[str, str]):
    assert status_code == 200, f"Expected status code 200, got {status_code}"
    assert isinstance(body, dict), "Response body is not a JSON object"
    
    assert "status" in body, "Missing required field 'status'"
    assert body["status"] in ["ready", "unready"], f"Invalid 'status': {body['status']}"
    assert "dependencies" in body, "Missing required field 'dependencies'"
    
    deps = body["dependencies"]
    assert isinstance(deps, dict), f"Expected 'dependencies' to be dict, got {type(deps)}"
    
    for dep in ["bedrock", "dynamodb_lock", "s3_audit_trail"]:
        assert dep in deps, f"Missing dependency key '{dep}' in dependencies"
        assert_is_string_and_not_empty(deps[dep], f"dependencies.{dep}")

def validate_metrics(status_code: int, body: Any, headers: Dict[str, str]):
    assert status_code == 200, f"Expected status code 200, got {status_code}"
    assert isinstance(body, str), "Expected plain text response for metrics"
    assert "ai_engine_requests_total" in body, "Metrics output should contain 'ai_engine_requests_total'"

def validate_detect_success(status_code: int, body: Any, headers: Dict[str, str]):
    assert status_code == 200, f"Expected status code 200, got {status_code}"
    assert isinstance(body, dict), "Response body is not a JSON object"
    
    # 1. Check required fields
    for field in ["anomaly_detected", "severity", "confidence", "reasoning", "correlation_id"]:
        assert field in body, f"Missing required output field: '{field}'"
        
    assert isinstance(body["anomaly_detected"], bool), f"'anomaly_detected' must be a boolean, got {type(body['anomaly_detected'])}"
    assert_is_string_and_not_empty(body["reasoning"], "reasoning")
    
    # 2. Check numeric ranges
    for field in ["severity", "confidence"]:
        assert isinstance(body[field], (int, float)), f"'{field}' must be a number, got {type(body[field])}"
        assert 0.0 <= body[field] <= 1.0, f"'{field}' must be in range [0.0, 1.0], got {body[field]}"
        
    assert_is_string_and_not_empty(body["correlation_id"], "correlation_id")
    
    # 3. Check anomaly_context if anomaly_detected is True
    if body["anomaly_detected"]:
        assert "anomaly_context" in body, "Missing 'anomaly_context' in response when 'anomaly_detected' is True"
        context = body["anomaly_context"]
        assert isinstance(context, dict), f"'anomaly_context' must be a dict, got {type(context)}"
        
        # Required context fields
        for field in ["target_service", "suspected_fault_type", "system"]:
            assert field in context, f"Missing required field '{field}' in 'anomaly_context'"
            assert_is_string_and_not_empty(context[field], f"anomaly_context.{field}")
            
        # Optional context fields
        for field in ["namespace", "deployment", "trigger_metric"]:
            if field in context and context[field] is not None:
                assert isinstance(context[field], str), f"Optional field '{field}' in 'anomaly_context' must be a string, got {type(context[field])}"
        if "trigger_value" in context and context["trigger_value"] is not None:
            assert isinstance(context["trigger_value"], (int, float)), f"Optional 'trigger_value' in 'anomaly_context' must be a number, got {type(context['trigger_value'])}"

def validate_decide_success(status_code: int, body: Any, headers: Dict[str, str]):
    assert status_code == 200, f"Expected status code 200, got {status_code}"
    assert isinstance(body, dict), "Response body is not a JSON object"
    
    # 1. Check required fields
    for field in ["matched_runbook", "pattern_type", "action_plan", "blast_radius_config", "verify_policy", "correlation_id", "idempotency_key", "dry_run_mode"]:
        assert field in body, f"Missing required output field: '{field}'"
        
    assert_is_string_and_not_empty(body["matched_runbook"], "matched_runbook")
    assert body["pattern_type"] in ["urgent", "deferred"], f"Invalid pattern_type: {body['pattern_type']}"
    assert isinstance(body["dry_run_mode"], bool), "dry_run_mode must be boolean"
    
    # 2. Check action_plan array and item schemas
    plan = body["action_plan"]
    assert isinstance(plan, list), f"'action_plan' must be a list, got {type(plan)}"
    assert len(plan) > 0, "'action_plan' should not be empty"
    
    allowed_actions = ["RESTART_DEPLOYMENT", "SCALE_UP_PODS", "UPDATE_ENV_SECRET", "ADJUST_MEMORY_LIMIT", "DELETE_POD"]
    for idx, step in enumerate(plan):
        assert isinstance(step, dict), f"Action plan step at index {idx} must be a dict"
        assert "step" in step, f"Missing 'step' in action plan step at index {idx}"
        assert isinstance(step["step"], int), f"'step' must be an integer, got {type(step['step'])}"
        assert "action" in step, f"Missing 'action' in action plan step at index {idx}"
        assert step["action"] in allowed_actions, f"Invalid 'action' '{step['action']}' at index {idx}. Must be one of {allowed_actions}"
        assert "target" in step, f"Missing 'target' in action plan step at index {idx}"
        assert_is_string_and_not_empty(step["target"], f"action_plan[{idx}].target")
        
        # Check optional params
        if "params" in step:
            params = step["params"]
            assert isinstance(params, dict), f"'params' in action plan step at index {idx} must be a dict, got {type(params)}"
            if "namespace" in params:
                assert isinstance(params["namespace"], str), f"'namespace' in params at index {idx} must be a string, got {type(params['namespace'])}"
            if "grace_period_seconds" in params:
                assert isinstance(params["grace_period_seconds"], int), f"'grace_period_seconds' in params at index {idx} must be an integer, got {type(params['grace_period_seconds'])}"
        
    # 3. Check blast_radius_config schema
    blast = body["blast_radius_config"]
    assert isinstance(blast, dict), f"'blast_radius_config' must be a dict, got {type(blast)}"
    assert "max_pod_impact_pct" in blast, "Missing 'max_pod_impact_pct' in 'blast_radius_config'"
    assert isinstance(blast["max_pod_impact_pct"], int), f"'max_pod_impact_pct' must be an integer, got {type(blast['max_pod_impact_pct'])}"
    assert "circuit_breaker_error_rate" in blast, "Missing 'circuit_breaker_error_rate' in 'blast_radius_config'"
    assert isinstance(blast["circuit_breaker_error_rate"], (int, float)), f"'circuit_breaker_error_rate' must be a number, got {type(blast['circuit_breaker_error_rate'])}"
    assert "allowed_namespaces" in blast, "Missing 'allowed_namespaces' in 'blast_radius_config'"
    assert isinstance(blast["allowed_namespaces"], list), f"'allowed_namespaces' must be a list, got {type(blast['allowed_namespaces'])}"
    for ns_idx, ns in enumerate(blast["allowed_namespaces"]):
        assert isinstance(ns, str), f"Namespace at index {ns_idx} in 'allowed_namespaces' must be a string, got {type(ns)}"

def validate_verify_success(status_code: int, body: Any, headers: Dict[str, str]):
    assert status_code == 200, f"Expected status code 200, got {status_code}"
    assert isinstance(body, dict), "Response body is not a JSON object"
    
    # 1. Check required fields
    for field in ["success", "regression_detected", "next_action"]:
        assert field in body, f"Missing required output field: '{field}'"
        
    assert isinstance(body["success"], bool), f"'success' must be a boolean, got {type(body['success'])}"
    assert isinstance(body["regression_detected"], bool), f"'regression_detected' must be a boolean, got {type(body['regression_detected'])}"
    
    allowed_next_actions = ["DONE", "RETRY", "ROLLBACK", "ESCALATE"]
    assert body["next_action"] in allowed_next_actions, f"Invalid 'next_action' '{body['next_action']}'. Must be one of {allowed_next_actions}"
    
    # 2. Check optional escalation_bundle if present
    if "escalation_bundle" in body and body["escalation_bundle"] is not None:
        eb = body["escalation_bundle"]
        assert isinstance(eb, dict), f"'escalation_bundle' must be a dict, got {type(eb)}"
        if "reason" in eb:
            assert isinstance(eb["reason"], str), f"'reason' in escalation_bundle must be a string, got {type(eb['reason'])}"
        if "logs" in eb:
            assert isinstance(eb["logs"], list), f"'logs' in escalation_bundle must be a list, got {type(eb['logs'])}"
            for log_idx, log_item in enumerate(eb["logs"]):
                assert isinstance(log_item, str), f"Log item at index {log_idx} in escalation_bundle must be a string, got {type(log_item)}"
        if "metrics" in eb:
            assert isinstance(eb["metrics"], dict), f"'metrics' in escalation_bundle must be a dict, got {type(eb['metrics'])}"

# Validator for Input Validation Errors (422)
def validate_error_422(status_code: int, body: Any, headers: Dict[str, str]):
    assert status_code == 422, f"Expected validation error status code 422, got {status_code}"
    assert isinstance(body, dict), "Error response body is not a JSON object"
    assert "detail" in body, "Missing 'detail' array in 422 error response"
    assert isinstance(body["detail"], list), f"'detail' must be a list of validation errors, got {type(body['detail'])}"
    assert len(body["detail"]) > 0, "Validation error details list is empty"
    print(f"       {YELLOW}[INFO] Correctly caught input error. Validation Details: {json.dumps(body['detail'])}{RESET}")

def main():
    base_url = "http://localhost:8540"
    if len(sys.argv) > 1:
        base_url = sys.argv[1]

    print(f"Starting Multi-Field Strict Schema & Robust Input Validation Tests: {YELLOW}{base_url}{RESET}\n")
    tester = APITester(base_url)

    # Base valid payloads for testing
    valid_detect_payload = {
        "idempotency_key": "123e4567-e89b-12d3-a456-426614174001",
        "correlation_id": "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o",
        "dry_run_mode": True,
        "telemetry_window": [
            {
                "ts": "2026-06-25T10:00:00.123Z",
                "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
                "service": "order-service",
                "signal_name": "service_error_rate",
                "value": 0.15,
                "labels": {
                    "system": "E-COMMERCE",
                    "namespace": "production",
                    "deployment": "order-service"
                }
            }
        ]
    }

    valid_decide_payload = {
        "idempotency_key": "test-idem-key-123",
        "correlation_id": "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o",
        "dry_run_mode": True,
        "anomaly_context": {
            "target_service": "order-service",
            "suspected_fault_type": "database_connection_failure",
            "system": "E-COMMERCE",
            "namespace": "production",
            "deployment": "order-service",
            "trigger_metric": "service_error_rate",
            "trigger_value": 0.15
        }
    }

    valid_verify_payload = {
        "idempotency_key": "test-idem-key-456",
        "correlation_id": "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o",
        "dry_run_mode": True,
        "action_executed": {
            "action": "RESTART_DEPLOYMENT",
            "target": "deployment/order-service",
            "status": "COMPLETED",
            "execution_time_seconds": 45
        },
        "post_telemetry_window": [
            {
                "ts": "2026-06-25T10:02:00.000Z",
                "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
                "service": "order-service",
                "signal_name": "service_error_rate",
                "value": 0.00,
                "labels": {
                    "system": "E-COMMERCE",
                    "namespace": "production",
                    "deployment": "order-service"
                }
            }
        ]
    }

    # =========================================================================
    # PART 1: POSITIVE TESTS (STRICT SCHEMAS)
    # =========================================================================
    print(f"{YELLOW}=== PART 1: POSITIVE TESTS (STRICT SCHEMAS) ==={RESET}\n")

    tester.run_test(
        name="GET /health - Liveness Check Schema",
        method="GET",
        path="/health",
        headers=None,
        body=None,
        validator=validate_health
    )

    tester.run_test(
        name="GET /ready - Readiness Check Schema",
        method="GET",
        path="/ready",
        headers=None,
        body=None,
        validator=validate_ready
    )

    tester.run_test(
        name="GET /metrics - Prometheus Output Check",
        method="GET",
        path="/metrics",
        headers=None,
        body=None,
        validator=validate_metrics
    )

    tester.run_test(
        name="POST /v1/detect - Verify All Required & Optional Schema Fields",
        method="POST",
        path="/v1/detect",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=valid_detect_payload,
        validator=validate_detect_success
    )

    tester.run_test(
        name="POST /v1/decide - Verify All Required & Optional Schema Fields",
        method="POST",
        path="/v1/decide",
        headers={
            "X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "Idempotency-Key": "test-idem-key-123"
        },
        body=valid_decide_payload,
        validator=validate_decide_success
    )

    tester.run_test(
        name="POST /v1/verify - Verify All Required & Optional Schema Fields",
        method="POST",
        path="/v1/verify",
        headers={
            "X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "Idempotency-Key": "test-idem-key-456"
        },
        body=valid_verify_payload,
        validator=validate_verify_success
    )

    # =========================================================================
    # PART 2: NEGATIVE TESTS (ALL REQUIRED FIELDS IN ALL ENDPOINTS)
    # =========================================================================
    print(f"\n{YELLOW}=== PART 2: NEGATIVE TESTS (ALL CONTRACT REQUIRED FIELDS) ==={RESET}\n")

    # --- ENDPOINT: /v1/detect ---
    # Missing idempotency_key
    bad_detect_no_idem = valid_detect_payload.copy()
    bad_detect_no_idem.pop("idempotency_key")
    tester.run_test(
        name="POST /v1/detect - Missing 'idempotency_key' (Expects 422)",
        method="POST",
        path="/v1/detect",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_detect_no_idem,
        validator=validate_error_422
    )

    # Missing correlation_id
    bad_detect_no_corr = valid_detect_payload.copy()
    bad_detect_no_corr.pop("correlation_id")
    tester.run_test(
        name="POST /v1/detect - Missing 'correlation_id' (Expects 422)",
        method="POST",
        path="/v1/detect",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_detect_no_corr,
        validator=validate_error_422
    )

    # Missing dry_run_mode
    bad_detect_no_dry = valid_detect_payload.copy()
    bad_detect_no_dry.pop("dry_run_mode")
    tester.run_test(
        name="POST /v1/detect - Missing 'dry_run_mode' (Expects 422)",
        method="POST",
        path="/v1/detect",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_detect_no_dry,
        validator=validate_error_422
    )

    # Missing telemetry_window
    bad_detect_no_window = valid_detect_payload.copy()
    bad_detect_no_window.pop("telemetry_window")
    tester.run_test(
        name="POST /v1/detect - Missing 'telemetry_window' (Expects 422)",
        method="POST",
        path="/v1/detect",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_detect_no_window,
        validator=validate_error_422
    )

    # --- ENDPOINT: /v1/decide ---
    # Missing idempotency_key
    bad_decide_no_idem = valid_decide_payload.copy()
    bad_decide_no_idem.pop("idempotency_key")
    tester.run_test(
        name="POST /v1/decide - Missing 'idempotency_key' (Expects 422)",
        method="POST",
        path="/v1/decide",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_decide_no_idem,
        validator=validate_error_422
    )

    # Missing correlation_id
    bad_decide_no_corr = valid_decide_payload.copy()
    bad_decide_no_corr.pop("correlation_id")
    tester.run_test(
        name="POST /v1/decide - Missing 'correlation_id' (Expects 422)",
        method="POST",
        path="/v1/decide",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_decide_no_corr,
        validator=validate_error_422
    )

    # Missing anomaly_context
    bad_decide_no_context = valid_decide_payload.copy()
    bad_decide_no_context.pop("anomaly_context")
    tester.run_test(
        name="POST /v1/decide - Missing 'anomaly_context' (Expects 422)",
        method="POST",
        path="/v1/decide",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_decide_no_context,
        validator=validate_error_422
    )

    # Missing dry_run_mode
    bad_decide_no_dry = valid_decide_payload.copy()
    bad_decide_no_dry.pop("dry_run_mode")
    tester.run_test(
        name="POST /v1/decide - Missing 'dry_run_mode' (Expects 422)",
        method="POST",
        path="/v1/decide",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_decide_no_dry,
        validator=validate_error_422
    )

    # --- ENDPOINT: /v1/verify ---
    # Missing idempotency_key
    bad_verify_no_idem = valid_verify_payload.copy()
    bad_verify_no_idem.pop("idempotency_key")
    tester.run_test(
        name="POST /v1/verify - Missing 'idempotency_key' (Expects 422)",
        method="POST",
        path="/v1/verify",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_verify_no_idem,
        validator=validate_error_422
    )

    # Missing correlation_id
    bad_verify_no_corr = valid_verify_payload.copy()
    bad_verify_no_corr.pop("correlation_id")
    tester.run_test(
        name="POST /v1/verify - Missing 'correlation_id' (Expects 422)",
        method="POST",
        path="/v1/verify",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_verify_no_corr,
        validator=validate_error_422
    )

    # Missing dry_run_mode
    bad_verify_no_dry = valid_verify_payload.copy()
    bad_verify_no_dry.pop("dry_run_mode")
    tester.run_test(
        name="POST /v1/verify - Missing 'dry_run_mode' (Expects 422)",
        method="POST",
        path="/v1/verify",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_verify_no_dry,
        validator=validate_error_422
    )

    # Missing action_executed
    bad_verify_no_action = valid_verify_payload.copy()
    bad_verify_no_action.pop("action_executed")
    tester.run_test(
        name="POST /v1/verify - Missing 'action_executed' (Expects 422)",
        method="POST",
        path="/v1/verify",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_verify_no_action,
        validator=validate_error_422
    )

    # Missing post_telemetry_window
    bad_verify_no_window = valid_verify_payload.copy()
    bad_verify_no_window.pop("post_telemetry_window")
    tester.run_test(
        name="POST /v1/verify - Missing 'post_telemetry_window' (Expects 422)",
        method="POST",
        path="/v1/verify",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_verify_no_window,
        validator=validate_error_422
    )

    # =========================================================================
    # PART 3: WRONG DATA TYPES (CORRUPTING MULTIPLE FIELDS)
    # =========================================================================
    print(f"\n{YELLOW}=== PART 3: TYPE CORRUPTION TESTS (INVALID DATA TYPES) ==={RESET}\n")

    # dry_run_mode sent as array
    bad_type_dry_run = valid_detect_payload.copy()
    bad_type_dry_run["dry_run_mode"] = [True]
    tester.run_test(
        name="POST /v1/detect - 'dry_run_mode' as array (Expects 422)",
        method="POST",
        path="/v1/detect",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_type_dry_run,
        validator=validate_error_422
    )

    # telemetry_window sent as string
    bad_type_window = valid_detect_payload.copy()
    bad_type_window["telemetry_window"] = "not-a-list"
    tester.run_test(
        name="POST /v1/detect - 'telemetry_window' as string (Expects 422)",
        method="POST",
        path="/v1/detect",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_type_window,
        validator=validate_error_422
    )

    # anomaly_context sent as int
    bad_type_context = valid_decide_payload.copy()
    bad_type_context["anomaly_context"] = 12345
    tester.run_test(
        name="POST /v1/decide - 'anomaly_context' as integer (Expects 422)",
        method="POST",
        path="/v1/decide",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_type_context,
        validator=validate_error_422
    )

    # action_executed sent as boolean
    bad_type_action = valid_verify_payload.copy()
    bad_type_action["action_executed"] = False
    tester.run_test(
        name="POST /v1/verify - 'action_executed' as boolean (Expects 422)",
        method="POST",
        path="/v1/verify",
        headers={"X-Tenant-Id": "d3b07384-d113-495f-9f58-20d18d357d75"},
        body=bad_type_action,
        validator=validate_error_422
    )

    tester.print_summary()

if __name__ == "__main__":
    main()
