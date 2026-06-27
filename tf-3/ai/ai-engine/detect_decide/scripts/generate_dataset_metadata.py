import os
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "..", "dataset")
GROUND_TRUTH_PATH = os.path.join(DATASET_DIR, "ground_truth.json")
RUNBOOKS_PATH = os.path.join(DATASET_DIR, "runbooks.json")

def scan_dataset():
    print(f"Scanning dataset directory: {DATASET_DIR}")
    if not os.path.exists(DATASET_DIR):
        print(f"Error: Dataset directory {DATASET_DIR} does not exist.")
        return

    ground_truth = {}
    
    # Predefined mapping of fault types to runbook keys
    fault_runbook_mapping = {
        "cpu": "CPUSaturationRecoveryRunbook",
        "mem": "MemoryLeakRecoveryRunbook",
        "delay": "NetworkLatencyRecoveryRunbook",
        "loss": "PacketLossRecoveryRunbook",
        "disk": "DiskIORecoveryRunbook",
        "socket": "SocketExhaustionRecoveryRunbook"
    }

    # Scan the directory
    for item in os.listdir(DATASET_DIR):
        item_path = os.path.join(DATASET_DIR, item)
        if not os.path.isdir(item_path):
            continue
        
        # Folder name is usually in the format: <service>_<fault_type>
        if "_" not in item:
            continue
        
        parts = item.split("_")
        if len(parts) < 2:
            continue
            
        target_service = parts[0]
        suspected_fault_type = "_".join(parts[1:])  # handles multiple underscores if any
        
        print(f"Found service-fault folder: {item} (Service: {target_service}, Fault: {suspected_fault_type})")
        
        # Scan for runs (subdirectories 1, 2, 3, etc.)
        for run_dir in os.listdir(item_path):
            run_path = os.path.join(item_path, run_dir)
            if not os.path.isdir(run_path) or not run_dir.isdigit():
                continue
                
            run_id = run_dir
            
            # Check for files
            inject_time_file = os.path.join(run_path, "inject_time.txt")
            metrics_file = os.path.join(run_path, "metrics.csv")
            logs_file = os.path.join(run_path, "logs.csv")
            
            if not os.path.exists(inject_time_file):
                print(f"  Warning: Run {run_id} in {item} is missing inject_time.txt")
                continue
            if not os.path.exists(metrics_file):
                print(f"  Warning: Run {run_id} in {item} is missing metrics.csv")
                continue
            if not os.path.exists(logs_file):
                print(f"  Warning: Run {run_id} in {item} is missing logs.csv")
                continue
                
            # Read inject time
            with open(inject_time_file, "r") as f:
                try:
                    inject_time = int(f.read().strip())
                except ValueError:
                    print(f"  Error: Invalid inject time in {inject_time_file}")
                    continue
            
            run_key = f"{item}_{run_id}"
            ground_truth[run_key] = {
                "service_fault": item,
                "run_id": run_id,
                "target_service": target_service,
                "suspected_fault_type": suspected_fault_type,
                "inject_time": inject_time,
                "metrics_path": os.path.relpath(metrics_file, DATASET_DIR),
                "logs_path": os.path.relpath(logs_file, DATASET_DIR),
                "matched_runbook": fault_runbook_mapping.get(suspected_fault_type, "DefaultRecoveryRunbook")
            }
            print(f"  Registered Run {run_id} with Inject Time: {inject_time}")
            
    # Write ground truth to file
    with open(GROUND_TRUTH_PATH, "w") as f:
        json.dump(ground_truth, f, indent=2)
    print(f"\nSaved {len(ground_truth)} ground truth labels to: {GROUND_TRUTH_PATH}")

    # Generate the solutions (runbooks)
    generate_runbooks()

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
    print(f"Saved {len(runbooks)} solutions to: {RUNBOOKS_PATH}")

if __name__ == "__main__":
    scan_dataset()
