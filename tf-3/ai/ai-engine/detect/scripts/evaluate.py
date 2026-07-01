import os
import sys
import json
import argparse
import time
from collections import defaultdict
import pandas as pd
import numpy as np

# Add src to Python path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AI_ENGINE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.append(AI_ENGINE_DIR)

from src.anomaly_detector import run_metric_anomaly_detection
from src.log_parser import Drain3LogParser
from src.correlation_analyzer import CorrelationAnalyzer
from src.config import (
    DATASET_DIR, 
    GROUND_TRUTH_PATH, 
    BASELINE_LENGTH,
    EVAL_BOCPD_WINDOW_BEFORE,
    EVAL_BOCPD_WINDOW_AFTER,
    EVAL_BOCPD_BASELINE_LENGTH
)


AI_ENGINE_ROOT = os.path.dirname(AI_ENGINE_DIR)
DEFAULT_REPORT_PATH = os.path.join(
    AI_ENGINE_ROOT,
    "dataset",
    "benchmark_reports",
    "benchmark_detect.json",
)

def run_evaluation(sample_size=None, top_k=None, output_path=None):
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
    correlation_analyzer = CorrelationAnalyzer(correlation_threshold=0.5)
    
    # Detect-only benchmark always uses BOCPD + BARO.
    correlation_analyzer.use_baro = True
    print("  [EVAL CONFIG] Detect stage uses BOCPD for detection and BARO for RCA.")
        
    if top_k is not None:
        correlation_analyzer.baro_top_k = top_k
        print(f"  [EVAL CONFIG] Forcing top-K candidates to retrieve: {top_k}")
        
    eval_top_k = correlation_analyzer.baro_top_k
    print(f"  [EVAL CONFIG] Top-K Accuracy will be computed for K = {eval_top_k}")
    
    results = []
    
    total_eval = 0
    correct_detection = 0
    correct_service = 0
    correct_fault = 0
    correct_top_k = 0
    rto_list = []
    anomaly_points_list = []
    confidence_list = []
    
    y_true = []
    y_pred = []
    fault_confusion = defaultdict(lambda: defaultdict(int))
    fault_totals = defaultdict(int)
    fault_correct_by_type = defaultdict(int)
    
    start_eval_time = time.time()
    
    print("\n=======================================================")
    print("           STARTING OFFLINE AIOPS EVALUATION           ")
    print("=======================================================\n")
    
    for idx, run_key in enumerate(run_keys):
        gt_info = ground_truth[run_key]
        service_fault = gt_info["service_fault"]
        run_id = gt_info["run_id"]
        true_service = gt_info["target_service"]
        true_fault = gt_info.get("suspected_fault_type", "unknown")
        inject_time = gt_info["inject_time"]
        
        print(f"[{idx+1}/{len(run_keys)}] Evaluating Run: {run_key}")
        print(f"  True Fault Service: {true_service} injected at {inject_time}")
        
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
        
        # Determine injection row index in metrics DataFrame
        inject_row_idx = df_metrics[df_metrics["time"] >= inject_time].index.min()
        if pd.isna(inject_row_idx):
            inject_row_idx = len(df_metrics) - 100 # Fallback
            
        # Slice time window for BOCPD to accelerate detect-only evaluation.
        start_idx = max(0, inject_row_idx - EVAL_BOCPD_WINDOW_BEFORE)
        end_idx = min(len(df_metrics) - 1, inject_row_idx + EVAL_BOCPD_WINDOW_AFTER)
        df_metrics_sliced = df_metrics.iloc[start_idx:end_idx+1].reset_index(drop=True)
        inject_row_idx_sliced = df_metrics_sliced[df_metrics_sliced["time"] >= inject_time].index.min()
        if pd.isna(inject_row_idx_sliced):
            inject_row_idx_sliced = len(df_metrics_sliced) - 1
        baseline_len_sliced = min(EVAL_BOCPD_BASELINE_LENGTH, inject_row_idx_sliced)
        detection_results = run_metric_anomaly_detection(df_metrics_sliced, baseline_len_sliced)
            
        mif_anoms = detection_results["multivariate"]["anomalies"]
        
        # Search for detection point starting from the injection time.
        detection_idx = -1
        combined_anoms = mif_anoms
        num_anomaly_points = int(np.sum(combined_anoms))
        anomaly_points_list.append(num_anomaly_points)
        
        # Search for the first anomaly after or near the injection point
        # Allow up to 30 seconds before injection (in case of clock skew) up to end of timeseries
        search_start = max(0, inject_row_idx_sliced - 30)
        for i in range(search_start, len(df_metrics_sliced)):
            if combined_anoms[i]:
                detection_idx = i
                break
                
        if detection_idx == -1:
            print(f"  [RESULT] Anomaly Detection FAILED (False Negative). Anomaly points flagged: {num_anomaly_points}")
            y_true.append(true_service)
            y_pred.append("undetected")
            results.append({
                "run_key": run_key,
                "detected": False,
                "service_correct": False,
                "rto": None
            })
            total_eval += 1
            continue
            
        # Anomaly detected successfully!
        correct_detection += 1
        
        # Map sliced BOCPD detection index back to full metrics.
        detect_time = df_metrics_sliced.iloc[detection_idx]["time"]
        detection_idx = df_metrics[df_metrics["time"] == detect_time].index[0]
        
        detect_time = df_metrics.iloc[detection_idx]["time"]
        rto = int(detect_time - inject_time)
        rto_list.append(rto)
        print(f"  [DETECTED] Anomaly flagged at second {detection_idx} (Time: {detect_time}, RTO: {rto}s). Anomaly points in run: {num_anomaly_points}")
        
        # 3. Parse logs with Drain3 around the detection time
        # We parse the logs for the entire run to simulate realistic logging
        time_start = int(df_metrics["time"].min())
        time_end = int(df_metrics["time"].max())
        log_parser_run = Drain3LogParser(service_aware=True)
        df_log_ts, temp_info = log_parser_run.parse_logs(df_logs, time_start, time_end)
        
        # 4. Correlation-based Root Cause Localization
        correlation_analyzer.baseline_len = BASELINE_LENGTH
        pred_service, pred_fault, reasoning, confidence = correlation_analyzer.analyze(
            df_metrics=df_metrics,
            df_logs=df_log_ts,
            template_info=temp_info,
            anomaly_idx=detection_idx,
            window_size=120
        )
        
        # Check correctness
        service_ok = (pred_service == true_service)
        fault_ok = (pred_fault == true_fault)
        fault_confusion[true_fault][pred_fault] += 1
        fault_totals[true_fault] += 1
        if fault_ok:
            correct_fault += 1
            fault_correct_by_type[true_fault] += 1
        
        if service_ok:
            correct_service += 1
            
        y_true.append(true_service)
        y_pred.append(pred_service)
        
        # Top-K check
        top_k_candidates = correlation_analyzer.last_top_k[:eval_top_k]
        in_top_k = true_service in top_k_candidates
        if in_top_k:
            correct_top_k += 1
            
        confidence_list.append(confidence)
        print(f"  [DIAGNOSIS] Predicted Service: {pred_service} [{'OK' if service_ok else 'WRONG'}]")
        print(f"  [DIAGNOSIS] Predicted Fault:   {pred_fault} [{'OK' if fault_ok else 'WRONG'}]")
        print(f"  [DIAGNOSIS] Top-{eval_top_k} Candidates: {', '.join(top_k_candidates)} [{'OK' if in_top_k else 'WRONG'}]")
        print(f"  [DIAGNOSIS] Confidence Score:  {confidence:.2f}")
        print(f"  [REASONING] {reasoning}\n")
        
        results.append({
            "run_key": run_key,
            "detected": True,
            "service_correct": service_ok,
            "true_fault": true_fault,
            "pred_fault": pred_fault,
            "fault_correct": fault_ok,
            "rto": rto
        })
        total_eval += 1
        
    # Compute overall metrics
    eval_duration = time.time() - start_eval_time
    
    detection_rate = correct_detection / total_eval if total_eval > 0 else 0
    service_accuracy = correct_service / correct_detection if correct_detection > 0 else 0
    avg_rto = np.mean(rto_list) if rto_list else 0
    avg_confidence = np.mean(confidence_list) if confidence_list else 0.0
    
    total_anomaly_points = int(np.sum(anomaly_points_list)) if anomaly_points_list else 0
    avg_anomaly_points = np.mean(anomaly_points_list) if anomaly_points_list else 0
    
    # Top-1 and Top-K accuracy (out of all evaluated runs)
    top1_accuracy = correct_service / total_eval if total_eval > 0 else 0
    topk_accuracy = correct_top_k / total_eval if total_eval > 0 else 0
    fault_accuracy = correct_fault / correct_detection if correct_detection > 0 else 0
    fault_confusion_matrix = {
        true_fault: dict(sorted(pred_counts.items()))
        for true_fault, pred_counts in sorted(fault_confusion.items())
    }
    fault_accuracy_by_type = {
        fault: round(fault_correct_by_type.get(fault, 0) / total_count, 4)
        for fault, total_count in sorted(fault_totals.items())
        if total_count
    }
    
    # Calculate Macro-Averaged Precision, Recall, F1 for root cause service localization
    from sklearn.metrics import precision_recall_fscore_support
    unique_true_classes = sorted(list(set(y_true)))
    precision, recall, f1_score, _ = precision_recall_fscore_support(
        y_true, 
        y_pred, 
        labels=unique_true_classes, 
        average="macro", 
        zero_division=0
    )
    
    print("\n=======================================================")
    print("                EVALUATION SUMMARY REPORT              ")
    print("=======================================================")
    print(f"Evaluation completed in:           {eval_duration:.2f} seconds")
    print(f"Total Runs Evaluated:              {total_eval}")
    print(f"Total Alerts Triggered:            {correct_detection}")
    print(f"Anomaly Detection Rate:            {detection_rate * 100:.1f}% ({correct_detection}/{total_eval})")
    print(f"Service Localization Accuracy (Top-1, Detections): {service_accuracy * 100:.1f}% ({correct_service}/{correct_detection})")
    print(f"Service Localization Accuracy (Top-1, Total):      {top1_accuracy * 100:.1f}% ({correct_service}/{total_eval})")
    print(f"Service Localization Accuracy (Top-{eval_top_k}, Total):      {topk_accuracy * 100:.1f}% ({correct_top_k}/{total_eval})")
    print(f"Fault Type Accuracy (Detections): {fault_accuracy * 100:.1f}% ({correct_fault}/{correct_detection})")
    print(f"Average Recovery Time (RTO):       {avg_rto:.1f} seconds")
    print(f"Average Confidence Score:          {avg_confidence:.2f}")
    print(f"Total Anomaly Points Detected:     {total_anomaly_points}")
    print(f"Average Anomaly Points per Run:    {avg_anomaly_points:.1f}")
    print("-------------------------------------------------------")
    print(f"Macro-Precision (Service Root Cause): {precision:.3f}")
    print(f"Macro-Recall (Service Root Cause):    {recall:.3f}")
    print(f"Macro-F1-Score (Service Root Cause):  {f1_score:.3f} (Threshold: 0.85)")
    print("=======================================================\n")
    
    if f1_score >= 0.85:
        print("[SUCCESS] AI Engine passes the F1-Score specification threshold of 0.85!")
    else:
        print("[WARNING] F1-Score is below the target threshold. Consider tuning thresholds.")

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        report = {
            "benchmark": "detect",
            "total_runs": total_eval,
            "detected_runs": correct_detection,
            "detection_rate": round(detection_rate, 4),
            "service_top1_accuracy_on_detected": round(service_accuracy, 4),
            "service_top1_accuracy_total": round(top1_accuracy, 4),
            f"service_top{eval_top_k}_accuracy_total": round(topk_accuracy, 4),
            "fault_type_accuracy_on_detected": round(fault_accuracy, 4),
            "fault_accuracy_by_type": fault_accuracy_by_type,
            "fault_confusion_matrix": fault_confusion_matrix,
            "average_rto_seconds": round(float(avg_rto), 2),
            "average_confidence": round(float(avg_confidence), 4),
            "macro_precision": round(float(precision), 4),
            "macro_recall": round(float(recall), 4),
            "macro_f1": round(float(f1_score), 4),
            "duration_seconds": round(float(eval_duration), 2),
            "per_run": results,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate AIOps AI Engine Offline")
    parser.add_argument("--sample-size", type=int, default=10, help="Number of runs to sample (default: 10, use 90 for full eval)")
    parser.add_argument("--top-k", type=int, default=None, help="Number of top-K candidates to retrieve and check for accuracy (defaults to .env value)")
    parser.add_argument("--output", default=DEFAULT_REPORT_PATH, help="Output JSON path")
    args = parser.parse_args()
    
    run_evaluation(
        sample_size=args.sample_size,
        top_k=args.top_k,
        output_path=args.output,
    )
