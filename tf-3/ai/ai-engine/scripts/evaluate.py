import os
import sys
import json
import argparse
import time
import pandas as pd
import numpy as np

# Add src to Python path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AI_ENGINE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.append(AI_ENGINE_DIR)

from src.anomaly_detector import run_metric_anomaly_detection
from src.log_parser import Drain3LogParser
from src.correlation_analyzer import CorrelationAnalyzer
from src.self_healer import SelfHealer
from src.config import DATASET_DIR, GROUND_TRUTH_PATH, RUNBOOKS_PATH, BASELINE_LENGTH

def run_evaluation(sample_size=None):
    if not os.path.exists(GROUND_TRUTH_PATH):
        print(f"Error: Ground truth file not found at {GROUND_TRUTH_PATH}. Please run validate_dataset.py first.")
        return
        
    with open(GROUND_TRUTH_PATH, "r") as f:
        ground_truth = json.load(f)
        
    print(f"Total runs available in ground truth: {len(ground_truth)}")
    
    # Filter runs if a sample size is specified
    run_keys = sorted(list(ground_truth.keys()))
    if sample_size and sample_size < len(run_keys):
        # Sample representatively across different fault types
        np.random.seed(42)
        sampled_keys = []
        fault_types = ["cpu", "mem", "delay", "loss", "disk", "socket"]
        runs_per_fault = max(1, sample_size // len(fault_types))
        
        for ft in fault_types:
            ft_keys = [k for k in run_keys if ground_truth[k]["suspected_fault_type"] == ft]
            if ft_keys:
                sampled = np.random.choice(ft_keys, min(len(ft_keys), runs_per_fault), replace=False)
                sampled_keys.extend(sampled)
                
        run_keys = sorted(sampled_keys)[:sample_size]
        print(f"Sampled {len(run_keys)} runs for evaluation across fault types.")

    # Initialize modules
    healer = SelfHealer(RUNBOOKS_PATH)
    correlation_analyzer = CorrelationAnalyzer(correlation_threshold=0.5)
    
    results = []
    
    total_eval = 0
    correct_detection = 0
    correct_service = 0
    correct_fault = 0
    correct_runbook = 0
    rto_list = []
    
    start_eval_time = time.time()
    
    print("\n=======================================================")
    print("           STARTING OFFLINE AIOPS EVALUATION           ")
    print("=======================================================\n")
    
    for idx, run_key in enumerate(run_keys):
        gt_info = ground_truth[run_key]
        service_fault = gt_info["service_fault"]
        run_id = gt_info["run_id"]
        true_service = gt_info["target_service"]
        true_fault = gt_info["suspected_fault_type"]
        inject_time = gt_info["inject_time"]
        true_runbook = gt_info["matched_runbook"]
        
        print(f"[{idx+1}/{len(run_keys)}] Evaluating Run: {run_key}")
        print(f"  True Fault: {true_service} ({true_fault}) injected at {inject_time}")
        
        # Load dataset files for this run
        run_dir = os.path.join(DATASET_DIR, service_fault, run_id)
        simple_metrics_path = os.path.join(run_dir, "simple_metrics.csv")
        logs_path = os.path.join(run_dir, "logs.csv")
        
        if not os.path.exists(simple_metrics_path) or not os.path.exists(logs_path):
            print(f"  [ERROR] Missing metrics or logs files for {run_key}. Skipping.")
            continue
            
        # 1. Load Data
        df_metrics = pd.read_csv(simple_metrics_path).sort_values("time").reset_index(drop=True)
        df_logs = pd.read_csv(logs_path)
        
        time_start = int(df_metrics["time"].min())
        time_end = int(df_metrics["time"].max())
        
        # Determine injection row index in metrics DataFrame
        inject_row_idx = df_metrics[df_metrics["time"] >= inject_time].index.min()
        if pd.isna(inject_row_idx):
            inject_row_idx = len(df_metrics) - 100 # Fallback
            
        # 2. Anomaly Detection on Metrics
        # Baseline is defined in config
        baseline_len = BASELINE_LENGTH
        detection_results = run_metric_anomaly_detection(df_metrics, baseline_len)
        
        mif_anoms = detection_results["multivariate"]["anomalies"]
        
        # Search for detection point starting from the injection time
        detection_idx = -1
        # We also check EWMA anomalies for latency and error columns
        ewma_all_anoms = np.zeros(len(df_metrics), dtype=bool)
        for col, res in detection_results["ewma"].items():
            ewma_all_anoms = ewma_all_anoms | res["anomalies"]
            
        # Combined anomaly signal (Multivariate Isolation Forest OR EWMA SLIs)
        combined_anoms = mif_anoms | ewma_all_anoms
        
        # Search for the first anomaly after or near the injection point
        # Allow up to 30 seconds before injection (in case of clock skew) up to end of timeseries
        search_start = max(0, inject_row_idx - 30)
        for i in range(search_start, len(df_metrics)):
            if combined_anoms[i]:
                detection_idx = i
                break
                
        if detection_idx == -1:
            print("  [RESULT] Anomaly Detection FAILED (False Negative).")
            results.append({
                "run_key": run_key,
                "detected": False,
                "service_correct": False,
                "fault_correct": False,
                "runbook_correct": False,
                "rto": None
            })
            total_eval += 1
            continue
            
        # Anomaly detected successfully!
        correct_detection += 1
        detect_time = df_metrics.iloc[detection_idx]["time"]
        rto = int(detect_time - inject_time)
        rto_list.append(rto)
        print(f"  [DETECTED] Anomaly flagged at second {detection_idx} (Time: {detect_time}, RTO: {rto}s)")
        
        # 3. Parse logs with Drain3 around the detection time
        # We parse the logs for the entire run to simulate realistic logging
        log_parser_run = Drain3LogParser(service_aware=True)
        df_log_ts, temp_info = log_parser_run.parse_logs(df_logs, time_start, time_end)
        
        # 4. Correlation-based Root Cause Localization
        pred_service, pred_fault, reasoning, confidence = correlation_analyzer.analyze(
            df_metrics=df_metrics,
            df_logs=df_log_ts,
            template_info=temp_info,
            anomaly_idx=detection_idx,
            window_size=120
        )
        
        # 5. Match Runbook
        decision = healer.decide(pred_service, pred_fault)
        pred_runbook = decision["matched_runbook"]
        
        # Check correctness
        service_ok = (pred_service == true_service)
        fault_ok = (pred_fault == true_fault)
        runbook_ok = (pred_runbook == true_runbook)
        
        if service_ok:
            correct_service += 1
        if fault_ok:
            correct_fault += 1
        if runbook_ok:
            correct_runbook += 1
            
        print(f"  [DIAGNOSIS] Predicted Service: {pred_service} [{'OK' if service_ok else 'WRONG'}]")
        print(f"  [DIAGNOSIS] Predicted Fault:   {pred_fault} [{'OK' if fault_ok else 'WRONG'}]")
        print(f"  [HEALING]   Matched Runbook:   {pred_runbook} [{'OK' if runbook_ok else 'WRONG'}]")
        print(f"  [REASONING] {reasoning}\n")
        
        results.append({
            "run_key": run_key,
            "detected": True,
            "service_correct": service_ok,
            "fault_correct": fault_ok,
            "runbook_correct": runbook_ok,
            "rto": rto
        })
        total_eval += 1
        
    # Compute overall metrics
    eval_duration = time.time() - start_eval_time
    
    detection_rate = correct_detection / total_eval if total_eval > 0 else 0
    service_accuracy = correct_service / correct_detection if correct_detection > 0 else 0
    fault_accuracy = correct_fault / correct_detection if correct_detection > 0 else 0
    runbook_accuracy = correct_runbook / correct_detection if correct_detection > 0 else 0
    avg_rto = np.mean(rto_list) if rto_list else 0
    
    # Calculate Precision, Recall, F1 for root cause service localization
    # Recall = detected & correct / total
    # Precision = correct / detected
    recall = correct_service / total_eval if total_eval > 0 else 0
    precision = service_accuracy  # Out of all detections, how many were correct service
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    
    print("\n=======================================================")
    print("                EVALUATION SUMMARY REPORT              ")
    print("=======================================================")
    print(f"Evaluation completed in:           {eval_duration:.2f} seconds")
    print(f"Total Runs Evaluated:              {total_eval}")
    print(f"Anomaly Detection Rate:            {detection_rate * 100:.1f}% ({correct_detection}/{total_eval})")
    print(f"Service Localization Accuracy:     {service_accuracy * 100:.1f}% ({correct_service}/{correct_detection})")
    print(f"Fault Type Localization Accuracy:  {fault_accuracy * 100:.1f}% ({correct_fault}/{correct_detection})")
    print(f"Runbook Matching Accuracy:         {runbook_accuracy * 100:.1f}% ({correct_runbook}/{correct_detection})")
    print(f"Average Recovery Time (RTO):       {avg_rto:.1f} seconds")
    print("-------------------------------------------------------")
    print(f"Precision (Service Root Cause):    {precision:.3f}")
    print(f"Recall (Service Root Cause):       {recall:.3f}")
    print(f"F1-Score (Service Root Cause):      {f1_score:.3f} (Threshold: 0.85)")
    print("=======================================================\n")
    
    if f1_score >= 0.85:
        print("[SUCCESS] AI Engine passes the F1-Score specification threshold of 0.85!")
    else:
        print("[WARNING] F1-Score is below the target threshold. Consider tuning thresholds.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate AIOps AI Engine Offline")
    parser.add_argument("--sample-size", type=int, default=10, help="Number of runs to sample (default: 10, use 90 for full eval)")
    args = parser.parse_args()
    
    run_evaluation(sample_size=args.sample_size)
