import os
import json
import time
import uuid
import numpy as np
from pathlib import Path
from config.settings import Settings
from pipeline.ingestor import AIOpsIngestor
from pipeline.detector import AIOpsDetector
from pipeline.decide import RuleBasedDecider, LLMDecider
from pipeline.verify import AIOpsVerifier
from utils.logger import get_logger

logger = get_logger("AIOpsBenchmark")

class AIOpsBenchmark:
    """
    Runs E2E pipeline over dataset splits and computes accuracy, precision, recall,
    F1-score, and processing latencies.
    """

    def __init__(self, settings: Settings = None):
        self.settings = settings or Settings()
        self.ingestor = AIOpsIngestor()
        self.detector = AIOpsDetector(self.settings)
        
        # Select decider
        if self.settings.DECIDER_TYPE == "llm":
            self.decider = LLMDecider(self.settings)
        else:
            self.decider = RuleBasedDecider(self.settings)
            
        self.verifier = AIOpsVerifier(self.settings)

    def run_benchmark(self, split: str = "val") -> dict:
        """
        Runs the benchmark on a specific dataset split (train, val, public_test, private_test).
        """
        logger.info(f"=========================================")
        logger.info(f"Starting AIOps E2E Pipeline Benchmark on split: {split}")
        logger.info(f"=========================================")

        split_dir = self.settings.DATASET_PATH / split
        gt_file = self.settings.DATASET_PATH / f"{split}_gt.json"

        if not gt_file.exists():
            raise FileNotFoundError(f"Ground truth file not found: {gt_file}")
        if not split_dir.exists():
            raise FileNotFoundError(f"Split directory not found: {split_dir}")

        with open(gt_file, "r") as f:
            gt_data = json.load(f)

        results = []
        latencies = {
            "ingest": [],
            "detect": [],
            "decide": [],
            "verify": [],
            "e2e": []
        }

        # Tenant info for mock request headers
        tenant_id = "d3b07384-d113-495f-9f58-20d18d357d75"  # cdo-1
        system = "OB"

        for case_key, gt_info in gt_data.items():
            case_path = split_dir / case_key
            logger.info(f"--- Running case: {case_key} ---")
            
            if not case_path.exists():
                logger.warning(f"Case directory {case_path} does not exist. Skipping.")
                continue

            t_start = time.time()
            correlation_id = str(uuid.uuid4())
            idempotency_key = str(uuid.uuid4())

            # 1. Ingestion
            t0 = time.time()
            try:
                # Ingest raw CSVs to Telemetry Window JSON
                telemetry_window = self.ingestor.ingest(
                    case_path=str(case_path),
                    tenant_id=tenant_id,
                    system=system
                )
                t_ingest = time.time() - t0
                latencies["ingest"].append(t_ingest)
            except Exception as e:
                logger.error(f"Ingestion failed for case {case_key}: {e}")
                continue

            # 2. Anomaly Detection
            t0 = time.time()
            try:
                detect_res = self.detector.detect(telemetry_window, correlation_id)
                t_detect = time.time() - t0
                latencies["detect"].append(t_detect)
            except Exception as e:
                logger.error(f"Detection failed for case {case_key}: {e}")
                continue

            # 3. Decision & Action Plan Recommendation (Decide)
            t_decide = 0.0
            decide_res = None
            if detect_res.get("anomaly_detected", False):
                t0 = time.time()
                try:
                    decide_res = self.decider.decide(
                        anomaly_context=detect_res["anomaly_context"],
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                        dry_run_mode=False
                    )
                    t_decide = time.time() - t0
                    latencies["decide"].append(t_decide)
                except Exception as e:
                    logger.error(f"Decide failed for case {case_key}: {e}")
            else:
                latencies["decide"].append(0.0)

            # 4. Verification (Verify)
            t_verify = 0.0
            verify_res = None
            if decide_res and decide_res.get("action_plan"):
                # Mock executor outcome (successful execution)
                action_executed = {
                    "action": decide_res["action_plan"][0]["action"],
                    "target": decide_res["action_plan"][0]["target"],
                    "status": "COMPLETED",
                    "execution_time_seconds": 45
                }
                
                t0 = time.time()
                try:
                    verify_res = self.verifier.verify(
                        action_executed=action_executed,
                        post_telemetry_window=telemetry_window,  # Mock using the same window for validation
                        correlation_id=correlation_id,
                        idempotency_key=idempotency_key,
                        dry_run_mode=False
                    )
                    t_verify = time.time() - t0
                    latencies["verify"].append(t_verify)
                except Exception as e:
                    logger.error(f"Verification failed for case {case_key}: {e}")
            else:
                latencies["verify"].append(0.0)

            t_e2e = time.time() - t_start
            latencies["e2e"].append(t_e2e)

            # Evaluate predictions
            pred_service = "None"
            pred_fault = "None"
            detected = detect_res.get("anomaly_detected", False)

            if detected:
                context = detect_res.get("anomaly_context", {})
                pred_service = context.get("target_service", "None")
                pred_fault = context.get("suspected_fault_type", "None")

            true_service = gt_info["root_cause_service"]
            true_fault = gt_info["fault_type"]

            service_correct = (pred_service == true_service)
            fault_correct = (pred_fault == true_fault)

            results.append({
                "case": case_key,
                "detected": detected,
                "pred_service": pred_service,
                "true_service": true_service,
                "pred_fault": pred_fault,
                "true_fault": true_fault,
                "service_correct": service_correct,
                "fault_correct": fault_correct,
                "latency_e2e": t_e2e
            })

            logger.info(f"Result for {case_key}: Detected={detected} | "
                        f"Service: Pred={pred_service}/True={true_service} ({'PASS' if service_correct else 'FAIL'}) | "
                        f"Fault: Pred={pred_fault}/True={true_fault} ({'PASS' if fault_correct else 'FAIL'}) | "
                        f"Latency: {t_e2e:.2f}s")

        # 5. Compute Metrics
        total_cases = len(results)
        if total_cases == 0:
            logger.warning("No cases processed successfully.")
            return {}

        # All cases in dataset are anomalous, so:
        # Anomaly Detection Accuracy = fraction of cases detected as anomalous
        detected_cases = sum(1 for r in results if r["detected"])
        detection_accuracy = detected_cases / total_cases

        # Localization metrics (Root Cause Service)
        loc_correct = sum(1 for r in results if r["service_correct"])
        loc_precision = loc_correct / detected_cases if detected_cases > 0 else 0.0
        loc_recall = loc_correct / total_cases
        loc_f1 = (2 * loc_precision * loc_recall / (loc_precision + loc_recall)) if (loc_precision + loc_recall) > 0 else 0.0

        # Fault classification metrics
        fault_correct = sum(1 for r in results if r["fault_correct"])
        fault_precision = fault_correct / detected_cases if detected_cases > 0 else 0.0
        fault_recall = fault_correct / total_cases
        fault_f1 = (2 * fault_precision * fault_recall / (fault_precision + fault_recall)) if (fault_precision + fault_recall) > 0 else 0.0

        # Latency statistics
        mean_latencies = {k: float(np.mean(v)) for k, v in latencies.items() if v}
        p95_latencies = {k: float(np.percentile(v, 95)) for k, v in latencies.items() if v}

        summary = {
            "split": split,
            "decider_type": self.settings.DECIDER_TYPE,
            "total_cases": total_cases,
            "detected_cases": detected_cases,
            "detection_accuracy": float(round(detection_accuracy, 4)),
            "localization": {
                "correct": loc_correct,
                "precision": float(round(loc_precision, 4)),
                "recall": float(round(loc_recall, 4)),
                "f1": float(round(loc_f1, 4))
            },
            "classification": {
                "correct": fault_correct,
                "precision": float(round(fault_precision, 4)),
                "recall": float(round(fault_recall, 4)),
                "f1": float(round(fault_f1, 4))
            },
            "latency_mean": {k: float(round(v, 4)) for k, v in mean_latencies.items()},
            "latency_p95": {k: float(round(v, 4)) for k, v in p95_latencies.items()}
        }

        self._print_summary_report(summary)
        
        # Save summary report
        report_path = self.settings.BASE_DIR / f"benchmark_report_{split}.json"
        with open(report_path, "w") as f:
            json.dump(summary, f, indent=2)
        logger.info(f"Saved benchmark report to: {report_path}")

        return summary

    def _print_summary_report(self, summary: dict):
        """Prints a beautiful markdown-formatted report of the benchmark results."""
        print("\n" + "="*50)
        print("                 AIOPS PIPELINE BENCHMARK REPORT")
        print("="*50)
        print(f"Dataset Split:       {summary['split'].upper()}")
        print(f"Decider Engine:      {summary['decider_type'].upper()}")
        print(f"Total Test Cases:    {summary['total_cases']}")
        print(f"Detected Anomalies:  {summary['detected_cases']}")
        print(f"Detection Accuracy:  {summary['detection_accuracy']*100:.2f}%")
        print("-"*50)
        print("METRIC PERFORMANCE:")
        print(f"| Task | Correct | Precision | Recall | F1-Score |")
        print(f"|---|---|---|---|---|")
        print(f"| Root Cause Localization | {summary['localization']['correct']} | {summary['localization']['precision']:.4f} | {summary['localization']['recall']:.4f} | {summary['localization']['f1']:.4f} |")
        print(f"| Fault Classification    | {summary['classification']['correct']} | {summary['classification']['precision']:.4f} | {summary['classification']['recall']:.4f} | {summary['classification']['f1']:.4f} |")
        print("-"*50)
        print("LATENCY STATISTICS (seconds):")
        print(f"| Stage | Mean Latency | p95 Latency |")
        print(f"|---|---|---|")
        print(f"| Ingest | {summary['latency_mean']['ingest']:.4f}s | {summary['latency_p95']['ingest']:.4f}s |")
        print(f"| Detect | {summary['latency_mean']['detect']:.4f}s | {summary['latency_p95']['detect']:.4f}s |")
        print(f"| Decide | {summary['latency_mean']['decide']:.4f}s | {summary['latency_p95']['decide']:.4f}s |")
        print(f"| Verify | {summary['latency_mean']['verify']:.4f}s | {summary['latency_p95']['verify']:.4f}s |")
        print(f"| **E2E Pipeline** | **{summary['latency_mean']['e2e']:.4f}s** | **{summary['latency_p95']['e2e']:.4f}s** |")
        print("="*50 + "\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run AIOps E2E Pipeline Benchmark")
    parser.file = parser.add_argument("--split", type=str, default="val", help="Dataset split to evaluate (train, val, public_test, private_test)")
    args = parser.parse_args()

    bench = AIOpsBenchmark()
    bench.run_benchmark(split=args.split)
