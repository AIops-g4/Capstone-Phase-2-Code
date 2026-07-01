import os
import sys
import json
import time
import subprocess
import requests
import uuid
from datetime import datetime, timedelta, timezone

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AI_ENGINE_DIR = os.path.dirname(SCRIPT_DIR)
JSON_DIR = os.path.join(os.path.dirname(os.path.dirname(SCRIPT_DIR)), "dataset", "benchmark_fixtures", "detect_decide_verify")
os.makedirs(JSON_DIR, exist_ok=True)

# Port configuration
API_HOST = "127.0.0.1"
API_PORT = 8999  # Use a separate port for testing to avoid conflicts
BASE_URL = f"http://{API_HOST}:{API_PORT}"


def save_json(data, filename):
    filepath = os.path.join(JSON_DIR, filename)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  [SAVED] {filename}")


def generate_telemetry_point(ts_iso, service, signal, value):
    return {
        "ts": ts_iso,
        "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
        "service": service,
        "signal_name": signal,
        "value": value,
        "labels": {"namespace": "production"}
    }


def generate_mock_telemetry(anomaly=False, anomaly_service="checkoutservice", anomaly_metric="cpu", anomaly_value=0.98):
    """
    Generates a mock telemetry window (150 seconds).
    If anomaly=True, the last 15 seconds will contain anomalous values for anomaly_service.
    """
    telemetry_window = []
    ts_now = datetime.now(timezone.utc)
    
    # 150 seconds of telemetry
    for sec_offset in range(-150, 0):
        t_iso = (ts_now + timedelta(seconds=sec_offset)).isoformat().replace("+00:00", "Z")
        
        # Base healthy metrics for all services
        services = ["checkoutservice", "frontend", "paymentservice", "emailservice"]
        for s in services:
            # CPU (normally low)
            cpu_val = 0.15
            # Error (normally 0)
            err_val = 0.00
            # Latency (normally low)
            lat_val = 0.02
            
            # Inject anomaly in the last 15 seconds
            if anomaly and sec_offset >= -15 and s == anomaly_service:
                if anomaly_metric == "cpu":
                    cpu_val = anomaly_value
                elif anomaly_metric == "error":
                    err_val = anomaly_value
                elif anomaly_metric == "latency":
                    lat_val = anomaly_value
            
            telemetry_window.append(generate_telemetry_point(t_iso, s, f"{s}_cpu", cpu_val))
            telemetry_window.append(generate_telemetry_point(t_iso, s, f"{s}_error", err_val))
            telemetry_window.append(generate_telemetry_point(t_iso, s, f"{s}_latency-50", lat_val))
            
    return telemetry_window


def run_all_simulations():
    print("=======================================================")
    print("       STARTING CDOPS ENGINE CLOSED-LOOP SIMULATOR      ")
    print("=======================================================")
    
    # Start the FastAPI server in a subprocess
    env = os.environ.copy()
    env["API_PORT"] = str(API_PORT)
    env["API_HOST"] = API_HOST
    env["USE_BOCPD"] = "True"
    
    server_cmd = ["/home/duckq1u/miniconda3/envs/capstone/bin/python", "-m", "src.server"]
    print(f"Launching FastAPI Server: {' '.join(server_cmd)} on port {API_PORT}...")
    
    server_process = subprocess.Popen(
        server_cmd,
        cwd=AI_ENGINE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True
    )
    
    # Wait for the server to spin up
    ready = False
    for _ in range(30):
        try:
            res = requests.get(f"{BASE_URL}/docs", timeout=1.0)
            if res.status_code == 200:
                ready = True
                print("[SUCCESS] API server is ready and listening!\n")
                break
        except Exception:
            pass
        time.sleep(0.5)
        
    if not ready:
        print("[ERROR] FastAPI server failed to start within timeout.")
        server_process.kill()
        return
        
    try:
        # Define common states
        corr_id_primary = None
        idempotency_key = str(uuid.uuid4())
        
        # -----------------------------------------------------------------
        # SCENARIO 1: Primary Anomaly Detection & Successful Chữa lành (Verify DONE)
        # -----------------------------------------------------------------
        print("-------------------------------------------------------")
        print("SCENARIO 1: Primary Anomaly Detection & Successful Chữa lành")
        print("-------------------------------------------------------")
        
        # 1.1 Detect
        telemetry_s1 = generate_mock_telemetry(anomaly=True, anomaly_service="checkoutservice", anomaly_metric="cpu", anomaly_value=0.95)
        detect_req_s1 = {
            "correlation_id": str(uuid.uuid4()),
            "idempotency_key": idempotency_key,
            "dry_run_mode": False,
            "telemetry_window": telemetry_s1
        }
        save_json(detect_req_s1, "scenario1_detect_request.json")
        
        res_detect = requests.post(f"{BASE_URL}/v1/detect", json=detect_req_s1).json()
        save_json(res_detect, "scenario1_detect_response.json")
        corr_id_primary = res_detect["correlation_id"]
        print(f"  Result: Anomaly Detected = {res_detect['anomaly_detected']}, Correlation ID = {corr_id_primary}")
        
        # 1.2 Decide
        decide_req_s1 = {
            "correlation_id": corr_id_primary,
            "idempotency_key": idempotency_key,
            "dry_run_mode": False,
            "anomaly_context": res_detect["anomaly_context"]
        }
        save_json(decide_req_s1, "scenario1_decide_request.json")
        
        res_decide = requests.post(f"{BASE_URL}/v1/decide", json=decide_req_s1).json()
        save_json(res_decide, "scenario1_decide_response.json")
        print(f"  Result: Matched Runbook = {res_decide['matched_runbook']}, Steps Count = {len(res_decide['action_plan'])}")
        
        # 1.3 Verify (Healed metrics show recovery: error = 0, latency = 0.02)
        post_telemetry_s1 = []
        ts_now = datetime.now(timezone.utc)
        for i in range(10):
            t_iso = (ts_now + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
            post_telemetry_s1.append(generate_telemetry_point(t_iso, "checkoutservice", "checkoutservice_error", 0.00))
            post_telemetry_s1.append(generate_telemetry_point(t_iso, "checkoutservice", "checkoutservice_latency-50", 0.02))
            
        verify_req_s1 = {
            "correlation_id": corr_id_primary,
            "idempotency_key": idempotency_key,
            "dry_run_mode": False,
            "action_executed": {
                "action": "RESTART_DEPLOYMENT",
                "target": "deployment/checkoutservice",
                "status": "COMPLETED",
                "execution_time_seconds": 12
            },
            "post_telemetry_window": post_telemetry_s1
        }
        save_json(verify_req_s1, "scenario1_verify_request.json")
        
        res_verify = requests.post(f"{BASE_URL}/v1/verify", json=verify_req_s1).json()
        save_json(res_verify, "scenario1_verify_response.json")
        print(f"  Result: Success = {res_verify['success']}, Next Action = {res_verify['next_action']}")
        
        
        # -----------------------------------------------------------------
        # SCENARIO 2: Downstream Symptom Correlation & Suppression
        # -----------------------------------------------------------------
        print("\n-------------------------------------------------------")
        print("SCENARIO 2: Downstream Symptom Correlation & Suppression")
        print("-------------------------------------------------------")
        
        # Trigger an active incident on checkoutservice again to make it the active primary
        telemetry_s2_init = generate_mock_telemetry(anomaly=True, anomaly_service="checkoutservice", anomaly_metric="cpu", anomaly_value=0.98)
        detect_req_s2_init = {
            "correlation_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "telemetry_window": telemetry_s2_init
        }
        res_detect_s2_init = requests.post(f"{BASE_URL}/v1/detect", json=detect_req_s2_init).json()
        corr_id_active = res_detect_s2_init["correlation_id"]
        print(f"  [Active Incident] Created primary incident for checkoutservice: {corr_id_active}")
        
        # Now, alert on downstream service 'frontend' (frontend is downstream of checkoutservice)
        telemetry_s2_sym = generate_mock_telemetry(anomaly=True, anomaly_service="frontend", anomaly_metric="error", anomaly_value=0.25)
        detect_req_s2_sym = {
            "correlation_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "telemetry_window": telemetry_s2_sym
        }
        save_json(detect_req_s2_sym, "scenario2_detect_request.json")
        
        res_detect_s2_sym = requests.post(f"{BASE_URL}/v1/detect", json=detect_req_s2_sym).json()
        save_json(res_detect_s2_sym, "scenario2_detect_response.json")
        print(f"  Result: Symptom Correlated ID = {res_detect_s2_sym['correlation_id']} (Matches primary ID: {res_detect_s2_sym['correlation_id'] == corr_id_active})")
        
        # Call decide on this symptom (should be suppressed, returning empty action plan)
        decide_req_s2_sym = {
            "correlation_id": res_detect_s2_sym["correlation_id"],
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "anomaly_context": res_detect_s2_sym["anomaly_context"]
        }
        save_json(decide_req_s2_sym, "scenario2_decide_request.json")
        
        res_decide_s2_sym = requests.post(f"{BASE_URL}/v1/decide", json=decide_req_s2_sym).json()
        save_json(res_decide_s2_sym, "scenario2_decide_response.json")
        print(f"  Result: Matched Runbook = {res_decide_s2_sym['matched_runbook']}, Action Plan Steps = {len(res_decide_s2_sym['action_plan'])} (SUPPRESSED)")
        
        
        # -----------------------------------------------------------------
        # SCENARIO 3: Duplicate Alert Suppression
        # -----------------------------------------------------------------
        print("\n-------------------------------------------------------")
        print("SCENARIO 3: Duplicate Alert Suppression")
        print("-------------------------------------------------------")
        
        # Primary is active on checkoutservice. Send another alert on checkoutservice
        telemetry_s3 = generate_mock_telemetry(anomaly=True, anomaly_service="checkoutservice", anomaly_metric="cpu", anomaly_value=0.99)
        detect_req_s3 = {
            "correlation_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "telemetry_window": telemetry_s3
        }
        save_json(detect_req_s3, "scenario3_detect_request.json")
        
        res_detect_s3 = requests.post(f"{BASE_URL}/v1/detect", json=detect_req_s3).json()
        save_json(res_detect_s3, "scenario3_detect_response.json")
        print(f"  Result: Duplicate Correlated ID = {res_detect_s3['correlation_id']} (Matches active primary ID: {res_detect_s3['correlation_id'] == corr_id_active})")
        
        # Decide for this duplicate alert (first decide for primary was completed, so this duplicate is suppressed)
        # First, complete the decide for the primary to make duplicate suppression trigger
        decide_req_primary = {
            "correlation_id": corr_id_active,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "anomaly_context": res_detect_s2_init["anomaly_context"]
        }
        requests.post(f"{BASE_URL}/v1/decide", json=decide_req_primary)
        
        # Now decide for duplicate
        decide_req_s3 = {
            "correlation_id": corr_id_active,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "anomaly_context": res_detect_s3["anomaly_context"]
        }
        save_json(decide_req_s3, "scenario3_decide_request.json")
        
        res_decide_s3 = requests.post(f"{BASE_URL}/v1/decide", json=decide_req_s3).json()
        save_json(res_decide_s3, "scenario3_decide_response.json")
        print(f"  Result: Matched Runbook = {res_decide_s3['matched_runbook']}, Action Plan Steps = {len(res_decide_s3['action_plan'])} (SUPPRESSED)")
        
        
        # -----------------------------------------------------------------
        # SCENARIO 4: Action Execution Failure (Verify RETRY)
        # -----------------------------------------------------------------
        print("\n-------------------------------------------------------")
        print("SCENARIO 4: Action Execution Failure")
        print("-------------------------------------------------------")
        
        # CDO executor reports status: "FAILED"
        verify_req_s4 = {
            "correlation_id": corr_id_active,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "action_executed": {
                "action": "RESTART_DEPLOYMENT",
                "target": "deployment/checkoutservice",
                "status": "FAILED",
                "execution_time_seconds": 2
            },
            "post_telemetry_window": []  # Telemetry doesn't matter when execution failed
        }
        save_json(verify_req_s4, "scenario4_verify_request.json")
        
        res_verify_s4 = requests.post(f"{BASE_URL}/v1/verify", json=verify_req_s4).json()
        save_json(res_verify_s4, "scenario4_verify_response.json")
        print(f"  Result: Success = {res_verify_s4['success']}, Next Action = {res_verify_s4['next_action']}")
        
        
        # -----------------------------------------------------------------
        # SCENARIO 5: Verification Failure with Target Not Recovered (Verify ESCALATE)
        # -----------------------------------------------------------------
        print("\n-------------------------------------------------------")
        print("SCENARIO 5: Verification Failure with Target Not Recovered")
        print("-------------------------------------------------------")
        
        # Post-telemetry shows checkoutservice still has high error rate (e.g. 0.08 > 0.05 threshold)
        post_telemetry_s5 = []
        for i in range(10):
            t_iso = (ts_now + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
            post_telemetry_s5.append(generate_telemetry_point(t_iso, "checkoutservice", "checkoutservice_error", 0.08))
            post_telemetry_s5.append(generate_telemetry_point(t_iso, "checkoutservice", "checkoutservice_latency-50", 0.02))
            
        verify_req_s5 = {
            "correlation_id": corr_id_active,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "action_executed": {
                "action": "RESTART_DEPLOYMENT",
                "target": "deployment/checkoutservice",
                "status": "COMPLETED",
                "execution_time_seconds": 15
            },
            "post_telemetry_window": post_telemetry_s5
        }
        save_json(verify_req_s5, "scenario5_verify_request.json")
        
        res_verify_s5 = requests.post(f"{BASE_URL}/v1/verify", json=verify_req_s5).json()
        save_json(res_verify_s5, "scenario5_verify_response.json")
        print(f"  Result: Success = {res_verify_s5['success']}, Next Action = {res_verify_s5['next_action']}")
        
        
        # -----------------------------------------------------------------
        # SCENARIO 6: Verification Failure with Downstream Regression (Verify ROLLBACK)
        # -----------------------------------------------------------------
        print("\n-------------------------------------------------------")
        print("SCENARIO 6: Verification Failure with Downstream Regression")
        print("-------------------------------------------------------")
        
        # Post-telemetry shows checkoutservice recovered (error = 0), but frontend has high error rate (e.g. 0.15 > 0.10 threshold)
        post_telemetry_s6 = []
        for i in range(10):
            t_iso = (ts_now + timedelta(seconds=i)).isoformat().replace("+00:00", "Z")
            post_telemetry_s6.append(generate_telemetry_point(t_iso, "checkoutservice", "checkoutservice_error", 0.00))
            post_telemetry_s6.append(generate_telemetry_point(t_iso, "frontend", "frontend_error", 0.15))
            
        verify_req_s6 = {
            "correlation_id": corr_id_active,
            "idempotency_key": str(uuid.uuid4()),
            "dry_run_mode": False,
            "action_executed": {
                "action": "RESTART_DEPLOYMENT",
                "target": "deployment/checkoutservice",
                "status": "COMPLETED",
                "execution_time_seconds": 15
            },
            "post_telemetry_window": post_telemetry_s6
        }
        save_json(verify_req_s6, "scenario6_verify_request.json")
        
        res_verify_s6 = requests.post(f"{BASE_URL}/v1/verify", json=verify_req_s6).json()
        save_json(res_verify_s6, "scenario6_verify_response.json")
        print(f"  Result: Success = {res_verify_s6['success']}, Regression Detected = {res_verify_s6['regression_detected']}, Next Action = {res_verify_s6['next_action']}")
        
        print("\n=======================================================")
        print(" [SUCCESS] All 6 CDOps scenarios successfully simulated!")
        print("=======================================================\n")
        
    finally:
        # Shutdown the FastAPI server process
        print("Terminating FastAPI Server...")
        server_process.terminate()
        server_process.wait()
        print("FastAPI Server stopped successfully.")


if __name__ == "__main__":
    run_all_simulations()
