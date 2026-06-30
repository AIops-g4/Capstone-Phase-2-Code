import os
import json
import sys

# Define paths relative to this script's location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AI_ENGINE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.append(AI_ENGINE_DIR)

from src.config import DATASET_DIR, GROUND_TRUTH_PATH, RUNBOOKS_PATH

def validate_and_generate():
    print(f"Dataset Directory: {DATASET_DIR}")
    if not os.path.exists(DATASET_DIR):
        print(f"Error: Dataset directory {DATASET_DIR} does not exist.")
        return False

    fault_runbook_mapping = {
        "cpu": "CPUSaturationRecoveryRunbook",
        "mem": "MemoryLeakRecoveryRunbook",
        "delay": "NetworkLatencyRecoveryRunbook",
        "loss": "PacketLossRecoveryRunbook",
        "disk": "DiskIORecoveryRunbook",
        "socket": "SocketExhaustionRecoveryRunbook"
    }

    ground_truth = {}
    missing_files_count = 0
    total_runs_count = 0

    print("\n--- Scanning Service-Fault Folders ---")
    for item in sorted(os.listdir(DATASET_DIR)):
        item_path = os.path.join(DATASET_DIR, item)
        if not os.path.isdir(item_path) or "_" not in item:
            continue
        
        parts = item.split("_")
        if len(parts) < 2:
            continue
            
        target_service = parts[0]
        suspected_fault_type = "_".join(parts[1:])
        
        print(f"Folder: {item} (Service: {target_service}, Fault: {suspected_fault_type})")
        
        # Scan for run subdirectories (1, 2, 3)
        for run_dir in sorted(os.listdir(item_path)):
            run_path = os.path.join(item_path, run_dir)
            if not os.path.isdir(run_path) or not run_dir.isdigit():
                continue
                
            run_id = run_dir
            total_runs_count += 1
            
            # Required files in each run folder
            inject_time_file = os.path.join(run_path, "inject_time.txt")
            metrics_file = os.path.join(run_path, "metrics.csv")
            simple_metrics_file = os.path.join(run_path, "simple_metrics.csv")
            logs_file = os.path.join(run_path, "logs.csv")
            
            # Check existences
            files_to_check = {
                "inject_time.txt": inject_time_file,
                "metrics.csv": metrics_file,
                "simple_metrics.csv": simple_metrics_file,
                "logs.csv": logs_file
            }
            
            missing_in_run = []
            for name, path in files_to_check.items():
                if not os.path.exists(path):
                    missing_in_run.append(name)
                    missing_files_count += 1
                    
            if missing_in_run:
                print(f"  [WARNING] Run {run_id} is missing files: {', '.join(missing_in_run)}")
            
            # Read inject time
            inject_time = 0
            if os.path.exists(inject_time_file):
                with open(inject_time_file, "r") as f:
                    try:
                        inject_time = int(f.read().strip())
                    except ValueError:
                        print(f"  [ERROR] Invalid inject time in {inject_time_file}")
            
            run_key = f"{item}_{run_id}"
            ground_truth[run_key] = {
                "service_fault": item,
                "run_id": run_id,
                "target_service": target_service,
                "suspected_fault_type": suspected_fault_type,
                "inject_time": inject_time,
                "metrics_path": os.path.relpath(metrics_file, DATASET_DIR),
                "simple_metrics_path": os.path.relpath(simple_metrics_file, DATASET_DIR),
                "logs_path": os.path.relpath(logs_file, DATASET_DIR),
                "matched_runbook": fault_runbook_mapping.get(suspected_fault_type, "DefaultRecoveryRunbook")
            }
            
    print(f"\nScan complete. Total Runs found: {total_runs_count}, Total missing files: {missing_files_count}")

    # Write ground truth to file
    with open(GROUND_TRUTH_PATH, "w") as f:
        json.dump(ground_truth, f, indent=2)
    print(f"Generated/Updated {len(ground_truth)} ground truth labels in: {GROUND_TRUTH_PATH}")

    # Generate the solutions (runbooks)
    generate_runbooks()
    return missing_files_count == 0

def generate_runbooks():
    runbooks = {
        "CPUSaturationRecoveryRunbook": {
            "name": "CPUSaturationRecoveryRunbook",
            "description": "Runbook to handle high CPU usage or CPU saturation anomalies by scaling up replicas.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "SCALE_REPLICAS",
                    "target": "deployment/{{target_service}}",
                    "params": {
                        "namespace": "production",
                        "replicas": 3
                    }
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"]
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": [
                    "pod_ready == true",
                    "container_resource_usage < 80000000"
                ]
            }
        },
        "MemoryLeakRecoveryRunbook": {
            "name": "MemoryLeakRecoveryRunbook",
            "description": "Runbook to handle memory leaks or OOM-killed containers by patching memory limits.",
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
                        "memory_limit_mb": 1024
                    }
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"]
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": [
                    "pod_ready == true",
                    "restart_count_no_increase == true"
                ]
            }
        },
        "NetworkLatencyRecoveryRunbook": {
            "name": "NetworkLatencyRecoveryRunbook",
            "description": "Runbook to handle network delay or high latency by performing a rolling restart of the deployment.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": "deployment/{{target_service}}",
                    "params": {
                        "namespace": "production",
                        "grace_period_seconds": 30
                    }
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"]
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": [
                    "pod_ready == true",
                    "service_latency_p95 < 200.0"
                ]
            }
        },
        "PacketLossRecoveryRunbook": {
            "name": "PacketLossRecoveryRunbook",
            "description": "Runbook to handle network packet loss by performing a rolling restart of the deployment.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": "deployment/{{target_service}}",
                    "params": {
                        "namespace": "production",
                        "grace_period_seconds": 30
                    }
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"]
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": [
                    "pod_ready == true",
                    "service_error_rate < 0.01"
                ]
            }
        },
        "DiskIORecoveryRunbook": {
            "name": "DiskIORecoveryRunbook",
            "description": "Runbook to handle disk I/O bottlenecks by performing a rolling restart of the deployment to clear buffers.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": "deployment/{{target_service}}",
                    "params": {
                        "namespace": "production",
                        "grace_period_seconds": 30
                    }
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"]
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": [
                    "pod_ready == true"
                ]
            }
        },
        "SocketExhaustionRecoveryRunbook": {
            "name": "SocketExhaustionRecoveryRunbook",
            "description": "Runbook to handle socket connection exhaustion by scaling up replicas to distribute the connection load.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "SCALE_REPLICAS",
                    "target": "deployment/{{target_service}}",
                    "params": {
                        "namespace": "production",
                        "replicas": 3
                    }
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"]
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": [
                    "pod_ready == true"
                ]
            }
        },
        "DefaultRecoveryRunbook": {
            "name": "DefaultRecoveryRunbook",
            "description": "Default fallback runbook that restarts the anomalous deployment.",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": "deployment/{{target_service}}",
                    "params": {
                        "namespace": "production",
                        "grace_period_seconds": 30
                    }
                }
            ],
            "blast_radius_config": {
                "max_pod_impact_pct": 25,
                "circuit_breaker_error_rate": 0.20,
                "allowed_namespaces": ["production", "default"]
            },
            "verify_policy": {
                "window_seconds": 120,
                "success_conditions": [
                    "pod_ready == true"
                ]
            }
        }
    }

    with open(RUNBOOKS_PATH, "w") as f:
        json.dump(runbooks, f, indent=2)
    print(f"Generated/Updated {len(runbooks)} solutions in: {RUNBOOKS_PATH}")

if __name__ == "__main__":
    success = validate_and_generate()
    if success:
        print("\n[SUCCESS] Dataset is complete! All history, labels, and solutions are available.")
    else:
        print("\n[WARNING] Dataset validation finished with some warnings/missing files.")
