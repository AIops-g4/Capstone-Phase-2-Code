import os
import sys
import json
import argparse

def load_json(filepath):
    if not os.path.exists(filepath):
        print(f"Error: File not found at {filepath}")
        sys.exit(1)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error: Failed to parse JSON from {filepath}: {e}")
        sys.exit(1)

def validate_input_schemas(predictions, ground_truth, schemes_path):
    """
    Validate input data against defined JSON schemas.
    Fallback gracefully if jsonschema package is not installed.
    """
    try:
        import jsonschema
        print("jsonschema package found. Validating I/O schemas...")
        
        schemes = load_json(schemes_path)
        definitions = schemes.get("definitions", {})
        
        pred_schema = definitions.get("predictions_schema")
        gt_schema = definitions.get("ground_truth_schema")
        
        if pred_schema:
            jsonschema.validate(instance=predictions, schema=pred_schema)
            print("  - Predictions file is valid.")
        if gt_schema:
            jsonschema.validate(instance=ground_truth, schema=gt_schema)
            print("  - Ground truth file is valid.")
            
    except ImportError:
        print("Warning: 'jsonschema' package not installed. Skipping strict schema validation.")
        print("Performing basic structural checks...")
        # Basic checks
        if not isinstance(ground_truth, dict):
            print("Error: Ground truth must be a JSON object.")
            sys.exit(1)
        if not isinstance(predictions, dict):
            print("Error: Predictions must be a JSON object.")
            sys.exit(1)
            
        for case_id, gt_val in ground_truth.items():
            required_keys = ["root_cause_service", "fault_type", "system", "candidate_services"]
            if not all(k in gt_val for k in required_keys):
                print(f"Error: Ground truth entry '{case_id}' is missing required fields.")
                sys.exit(1)

def calculate_average_precision(y_true, y_scores):
    """
    Calculate Average Precision (AP) which represents Area Under Precision-Recall Curve (PR-AUC).
    Implemented in pure Python to eliminate dependency on scikit-learn.
    """
    total_positives = sum(y_true)
    if total_positives == 0:
        return 0.0
        
    # Zip, sort by score descending. 
    # To handle identical scores consistently, we sort by score first, then label.
    combined = sorted(zip(y_scores, y_true), key=lambda x: (x[0], x[1]), reverse=True)
    
    ap = 0.0
    positives_seen = 0
    
    for idx, (score, label) in enumerate(combined):
        if label == 1:
            positives_seen += 1
            precision_at_k = positives_seen / (idx + 1)
            ap += precision_at_k
            
    return ap / total_positives

def evaluate(predictions, ground_truth, threshold=0.5):
    """
    Evaluate RCA predictions against ground truth.
    Returns calculated metrics.
    """
    # 1. Align predictions and ground truth for service-level classification
    # We evaluate at service-level: each (case, service) is a binary prediction
    all_y_true = []
    all_y_scores = []
    all_y_pred_threshold = []
    
    # Track top-1 prediction metrics
    top1_tp = 0
    top1_fp = 0
    top1_fn = 0
    top1_tn = 0
    
    num_cases = len(ground_truth)
    
    for case_id, gt in ground_truth.items():
        root_cause = gt["root_cause_service"]
        candidates = gt["candidate_services"]
        
        # Get predictions for this case
        case_preds = predictions.get(case_id, {})
        
        # Determine top-1 prediction
        top1_service = None
        top1_score = -1.0
        
        for svc in candidates:
            score = float(case_preds.get(svc, 0.0))
            if score > top1_score:
                top1_score = score
                top1_service = svc
                
        # Calculate Top-1 counts for this case
        for svc in candidates:
            actual_label = 1 if svc == root_cause else 0
            predicted_label = 1 if svc == top1_service and top1_score > 0.0 else 0
            
            if actual_label == 1 and predicted_label == 1:
                top1_tp += 1
            elif actual_label == 0 and predicted_label == 1:
                top1_fp += 1
            elif actual_label == 1 and predicted_label == 0:
                top1_fn += 1
            elif actual_label == 0 and predicted_label == 0:
                top1_tn += 1
                
        # Calculate Threshold-based list
        for svc in candidates:
            actual_label = 1 if svc == root_cause else 0
            pred_score = float(case_preds.get(svc, 0.0))
            pred_label = 1 if pred_score >= threshold else 0
            
            all_y_true.append(actual_label)
            all_y_scores.append(pred_score)
            all_y_pred_threshold.append(pred_label)

    # 2. Compute Threshold-Based Metrics
    th_tp = sum(1 for t, p in zip(all_y_true, all_y_pred_threshold) if t == 1 and p == 1)
    th_fp = sum(1 for t, p in zip(all_y_true, all_y_pred_threshold) if t == 0 and p == 1)
    th_fn = sum(1 for t, p in zip(all_y_true, all_y_pred_threshold) if t == 1 and p == 0)
    
    th_precision = th_tp / (th_tp + th_fp) if (th_tp + th_fp) > 0 else 0.0
    th_recall = th_tp / (th_tp + th_fn) if (th_tp + th_fn) > 0 else 0.0
    th_f1 = (2 * th_precision * th_recall) / (th_precision + th_recall) if (th_precision + th_recall) > 0 else 0.0
    
    # 3. Compute Top-1 Metrics
    top1_precision = top1_tp / (top1_tp + top1_fp) if (top1_tp + top1_fp) > 0 else 0.0
    top1_recall = top1_tp / (top1_tp + top1_fn) if (top1_tp + top1_fn) > 0 else 0.0
    top1_f1 = (2 * top1_precision * top1_recall) / (top1_precision + top1_recall) if (top1_precision + top1_recall) > 0 else 0.0
    
    # 4. Compute PR-AUC (Average Precision)
    pr_auc = calculate_average_precision(all_y_true, all_y_scores)
    
    return {
        "num_cases": num_cases,
        "threshold_based_metrics": {
            "threshold": threshold,
            "precision": round(th_precision, 4),
            "recall": round(th_recall, 4),
            "f1_score": round(th_f1, 4)
        },
        "top1_based_metrics": {
            "precision": round(top1_precision, 4),
            "recall": round(top1_recall, 4),
            "f1_score": round(top1_f1, 4)
        },
        "pr_auc": round(pr_auc, 4)
    }

def main():
    parser = argparse.ArgumentParser(description="Evaluate RCA Predictions against Ground Truth.")
    parser.add_argument("-p", "--predictions", required=True, help="Path to predictions JSON file")
    parser.add_argument("-g", "--ground-truth", required=True, help="Path to ground truth JSON file")
    parser.add_argument("-o", "--output", default="metrics.json", help="Path to output metrics JSON file")
    parser.add_argument("-t", "--threshold", type=float, default=0.5, help="Decision threshold for threshold-based metrics (default: 0.5)")
    parser.add_argument("-s", "--schemes", default=os.path.join(os.path.dirname(__file__), "SCHEMES.json"), help="Path to SCHEMES.json file")
    
    args = parser.parse_args()
    
    print(f"Loading predictions from {args.predictions}...")
    predictions = load_json(args.predictions)
    
    print(f"Loading ground truth from {args.ground_truth}...")
    ground_truth = load_json(args.ground_truth)
    
    # Validate schemas
    validate_input_schemas(predictions, ground_truth, args.schemes)
    
    # Run evaluation
    print("Evaluating predictions...")
    metrics = evaluate(predictions, ground_truth, threshold=args.threshold)
    
    # Add metadata
    evaluation_mode = os.path.basename(args.ground_truth).replace("_gt.json", "")
    metrics = {"evaluation_mode": evaluation_mode, **metrics}
    
    # Output results
    print("\n--- Evaluation Results ---")
    print(f"Total Cases: {metrics['num_cases']}")
    print(f"PR-AUC (Average Precision): {metrics['pr_auc']:.4f}")
    print("\nThreshold-Based Metrics (threshold={}):".format(args.threshold))
    print(f"  Precision: {metrics['threshold_based_metrics']['precision']:.4f}")
    print(f"  Recall:    {metrics['threshold_based_metrics']['recall']:.4f}")
    print(f"  F1 Score:  {metrics['threshold_based_metrics']['f1_score']:.4f}")
    print("\nTop-1 Based Metrics (only highest score predicted):")
    print(f"  Precision: {metrics['top1_based_metrics']['precision']:.4f}")
    print(f"  Recall:    {metrics['top1_based_metrics']['recall']:.4f}")
    print(f"  F1 Score:  {metrics['top1_based_metrics']['f1_score']:.4f}")
    print("--------------------------\n")
    
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2)
    print(f"Saved evaluation metrics to {args.output}")

if __name__ == "__main__":
    main()
