import json
import os
import sys
import uuid

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DECIDE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, DECIDE_DIR)

from src.config import API_HOST, API_PORT

BASE_URL = f"http://{API_HOST}:{API_PORT}"


def test_decide_cpu():
    print("\n--- POST /v1/decide (cpu fault) ---")
    payload = {
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "dry_run_mode": False,
        "anomaly_context": {
            "target_service": "checkoutservice",
            "suspected_fault_type": "cpu",
            "system": "E-COMMERCE",
            "namespace": "production",
            "deployment": "deployment/checkoutservice",
            "trigger_metric": "cpu",
            "trigger_value": 4.5,
        },
    }
    r = requests.post(f"{BASE_URL}/v1/decide", json=payload, timeout=10)
    print(f"Status: {r.status_code}")
    data = r.json()
    print(json.dumps(data, indent=2))
    assert r.status_code == 200
    assert data["matched_runbook"] == "CPUSaturationRecoveryRunbook"
    assert data["action_plan"][0]["action"] == "SCALE_REPLICAS"
    print("[OK] cpu -> CPUSaturationRecoveryRunbook")


def test_decide_mem():
    print("\n--- POST /v1/decide (mem fault) ---")
    payload = {
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "dry_run_mode": False,
        "anomaly_context": {
            "target_service": "paymentservice",
            "suspected_fault_type": "mem",
            "system": "E-COMMERCE",
            "namespace": "production",
        },
    }
    r = requests.post(f"{BASE_URL}/v1/decide", json=payload, timeout=10)
    data = r.json()
    assert data["matched_runbook"] == "MemoryLeakRecoveryRunbook"
    print("[OK] mem -> MemoryLeakRecoveryRunbook")


if __name__ == "__main__":
    test_decide_cpu()
    test_decide_mem()
    print("\nAll decide API smoke tests passed.")
