import json
import requests
import uuid
from datetime import datetime, timezone

BASE_URL = "http://127.0.0.1:8050"

def test_detect():
    print("\n--- Testing POST /v1/detect ---")
    url = f"{BASE_URL}/v1/detect"
    
    # Generate mock telemetry window with a CPU anomaly in checkoutservice
    telemetry_window = []
    
    # Baseline normal metrics (10 seconds)
    for i in range(10):
        ts = datetime.fromtimestamp(1705353846 + i, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        telemetry_window.append({
            "ts": ts,
            "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "service": "checkoutservice",
            "signal_name": "cpu",
            "value": 0.25,
            "labels": {"namespace": "production"}
        })
        
    # Anomaly spike at the end
    ts_spike = datetime.fromtimestamp(1705353846 + 10, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    telemetry_window.append({
        "ts": ts_spike,
        "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
        "service": "checkoutservice",
        "signal_name": "cpu",
        "value": 4.50,  # Huge spike
        "labels": {"namespace": "production"}
    })
    
    # Correlated error log
    telemetry_window.append({
        "ts": ts_spike,
        "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
        "service": "checkoutservice",
        "signal_name": "application_log_event",
        "value": "checkoutservice: CPU saturation reached, thread pool exhausted!",
        "labels": {"level": "error", "namespace": "production"}
    })
    
    payload = {
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "dry_run_mode": False,
        "telemetry_window": telemetry_window
    }
    
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

def test_decide(anomaly_context):
    print("\n--- Testing POST /v1/decide ---")
    url = f"{BASE_URL}/v1/decide"
    
    payload = {
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "dry_run_mode": False,
        "anomaly_context": anomaly_context
    }
    
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

def test_verify():
    print("\n--- Testing POST /v1/verify ---")
    url = f"{BASE_URL}/v1/verify"
    
    # Mock post-healing telemetry window showing normal values
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    post_telemetry = [
        {
            "ts": ts,
            "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "service": "checkoutservice",
            "signal_name": "service_error_rate",
            "value": 0.00,  # Restored!
            "labels": {"namespace": "production"}
        },
        {
            "ts": ts,
            "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
            "service": "checkoutservice",
            "signal_name": "latency",
            "value": 0.05,  # Restored!
            "labels": {"namespace": "production"}
        }
    ]
    
    payload = {
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "dry_run_mode": False,
        "action_executed": {
            "action": "SCALE_REPLICAS",
            "target": "deployment/checkoutservice",
            "status": "COMPLETED",
            "execution_time_seconds": 45
        },
        "post_telemetry_window": post_telemetry
    }
    
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    return response.json()

if __name__ == "__main__":
    try:
        detect_res = test_detect()
        if detect_res.get("anomaly_detected") and detect_res.get("anomaly_context"):
            test_decide(detect_res["anomaly_context"])
        else:
            # Fallback mock context for decide testing
            mock_context = {
                "target_service": "checkoutservice",
                "suspected_fault_type": "cpu",
                "system": "E-COMMERCE",
                "namespace": "production",
                "deployment": "checkoutservice"
            }
            test_decide(mock_context)
            
        test_verify()
    except Exception as e:
        print(f"Error connecting to server: {e}. Make sure the FastAPI server is running on port 8000.")
