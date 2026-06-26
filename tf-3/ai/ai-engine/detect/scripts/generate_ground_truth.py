import os
import json
import sys

# Setup paths relative to the script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AI_ENGINE_DIR = os.path.dirname(SCRIPT_DIR)

# Attempt to load path definitions from config, otherwise fallback to standard layout
try:
    sys.path.append(AI_ENGINE_DIR)
    from src.config import DATASET_DIR, GROUND_TRUTH_PATH
except ImportError:
    DATASET_DIR = os.path.join(AI_ENGINE_DIR, "dataset")
    GROUND_TRUTH_PATH = os.path.join(DATASET_DIR, "ground_truth.json")

def generate_ground_truth():
    print("=======================================================")
    print("        AIOPS DATASET GROUND TRUTH EXTRACTOR           ")
    print("=======================================================")
    print(f"Dataset Source Directory: {DATASET_DIR}")
    print(f"Output Catalog Path:      {GROUND_TRUTH_PATH}\n")

    if not os.path.exists(DATASET_DIR):
        print(f"[ERROR] Dataset directory not found at: {DATASET_DIR}")
        return False

    # Mapping of fault types to runbook names
    fault_runbook_mapping = {
        "cpu": "CPUSaturationRecoveryRunbook",
        "mem": "MemoryLeakRecoveryRunbook",
        "delay": "NetworkLatencyRecoveryRunbook",
        "loss": "PacketLossRecoveryRunbook",
        "disk": "DiskIORecoveryRunbook",
        "socket": "SocketExhaustionRecoveryRunbook"
    }

    ground_truth = {}
    total_runs = 0

    # 1. Scan service-fault folders (e.g. checkoutservice_cpu)
    for folder in sorted(os.listdir(DATASET_DIR)):
        folder_path = os.path.join(DATASET_DIR, folder)
        if not os.path.isdir(folder_path) or "_" not in folder:
            continue
            
        parts = folder.split("_")
        if len(parts) < 2:
            continue
            
        target_service = parts[0]
        suspected_fault_type = "_".join(parts[1:])
        
        # 2. Scan run subdirectories (e.g. 1, 2, 3)
        for run_dir in sorted(os.listdir(folder_path)):
            run_path = os.path.join(folder_path, run_dir)
            if not os.path.isdir(run_path) or not run_dir.isdigit():
                continue
                
            run_id = run_dir
            
            # Paths to metrics, simple metrics, logs, and inject_time
            inject_time_file = os.path.join(run_path, "inject_time.txt")
            metrics_file = os.path.join(run_path, "metrics.csv")
            simple_metrics_file = os.path.join(run_path, "simple_metrics.csv")
            logs_file = os.path.join(run_path, "logs.csv")
            
            # Read inject time from inject_time.txt
            inject_time = 0
            if os.path.exists(inject_time_file):
                with open(inject_time_file, "r") as f:
                    try:
                        inject_time = int(f.read().strip())
                    except ValueError:
                        print(f"  [WARNING] Invalid integer in {inject_time_file}. Defaulting to 0.")
            
            # Add to ground truth catalog
            run_key = f"{folder}_{run_id}"
            ground_truth[run_key] = {
                "service_fault": folder,
                "run_id": run_id,
                "target_service": target_service,
                "suspected_fault_type": suspected_fault_type,
                "inject_time": inject_time,
                "metrics_path": os.path.relpath(metrics_file, DATASET_DIR),
                "simple_metrics_path": os.path.relpath(simple_metrics_file, DATASET_DIR),
                "logs_path": os.path.relpath(logs_file, DATASET_DIR),
                "matched_runbook": fault_runbook_mapping.get(suspected_fault_type, "DefaultRecoveryRunbook")
            }
            total_runs += 1

    # 3. Write compiled ground truth to ground_truth.json
    with open(GROUND_TRUTH_PATH, "w") as f:
        json.dump(ground_truth, f, indent=2)
        
    print(f"[SUCCESS] Extracted {total_runs} runs from dataset.")
    print(f"[SUCCESS] Compiled catalog saved to: {GROUND_TRUTH_PATH}\n")
    
    # 4. Print a concise console summary of first 10 runs
    print("--- FIRST 10 RUNS PREVIEW ---")
    print(f"{'Run Key':<30} | {'Service':<20} | {'Fault':<8} | {'Inject Time':<12}")
    print("-" * 78)
    for key in list(ground_truth.keys())[:10]:
        val = ground_truth[key]
        print(f"{key:<30} | {val['target_service']:<20} | {val['suspected_fault_type']:<8} | {val['inject_time']:<12}")
    if len(ground_truth) > 10:
        print(f"... and {len(ground_truth) - 10} more runs.")
    print("-" * 78)
    
    return True

if __name__ == "__main__":
    generate_ground_truth()
