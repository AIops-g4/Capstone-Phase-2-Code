"""
End-to-end offline benchmark: detect (anomaly + BARO RCA) → decide (runbook matching).

For each run in ground_truth.json:
  1. Load RE2 metrics/logs, run anomaly detection (same as evaluate.py)
  2. Localize root-cause service + fault via BARO RCA
  3. Feed RCA output into SelfHealer.decide() (same logic as POST /v1/decide)
  4. Compare predicted runbook vs ground-truth matched_runbook

No HTTP server required.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from types import SimpleNamespace

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DETECT_DECIDE_DIR = os.path.dirname(SCRIPT_DIR)
AI_ENGINE_ROOT = os.path.dirname(DETECT_DECIDE_DIR)
sys.path.insert(0, DETECT_DECIDE_DIR)

from src.anomaly_detector import run_metric_anomaly_detection
from src.config import (
    BASELINE_LENGTH,
    DATASET_DIR,
    EVAL_BOCPD_BASELINE_LENGTH,
    EVAL_BOCPD_WINDOW_AFTER,
    EVAL_BOCPD_WINDOW_BEFORE,
    GROUND_TRUTH_PATH,
    RUNBOOKS_PATH,
)
from src.correlation_analyzer import CorrelationAnalyzer
from src.log_parser import Drain3LogParser
from src.self_healer import SelfHealer
from src.verifier import VerificationEngine


def _sample_run_keys(ground_truth: dict, sample_size: int | None) -> list[str]:
    run_keys = sorted(ground_truth.keys())
    if not sample_size or sample_size >= len(run_keys):
        return run_keys

    np.random.seed(42)
    sampled_keys: list[str] = []
    fault_types = ["cpu", "mem", "delay", "loss", "disk", "socket"]
    runs_per_fault = max(1, sample_size // len(fault_types))

    for ft in fault_types:
        ft_keys = [k for k in run_keys if ground_truth[k]["suspected_fault_type"] == ft]
        if ft_keys:
            chosen = np.random.choice(ft_keys, min(len(ft_keys), runs_per_fault), replace=False)
            sampled_keys.extend(chosen)

    return sorted(sampled_keys)[:sample_size]


def _find_detection_idx(
    df_metrics: pd.DataFrame,
    inject_time: int,
    detection_results: dict,
    use_bocpd: bool,
    inject_row_idx_sliced: int | None,
) -> tuple[int, int]:
    """Return (detection_idx, num_anomaly_points). detection_idx is -1 if not found."""
    mif_anoms = detection_results["multivariate"]["anomalies"]
    ewma_all_anoms = np.zeros(len(mif_anoms), dtype=bool)
    for res in detection_results["ewma"].values():
        ewma_all_anoms |= res["anomalies"]

    combined_anoms = mif_anoms | ewma_all_anoms
    num_anomaly_points = int(np.sum(combined_anoms))

    if use_bocpd and inject_row_idx_sliced is not None:
        search_start = max(0, inject_row_idx_sliced - 30)
        search_end = len(combined_anoms)
    else:
        inject_row_idx = df_metrics[df_metrics["time"] >= inject_time].index.min()
        if pd.isna(inject_row_idx):
            inject_row_idx = len(df_metrics) - 100
        search_start = max(0, int(inject_row_idx) - 30)
        search_end = len(df_metrics)

    for i in range(search_start, search_end):
        if combined_anoms[i]:
            return i, num_anomaly_points

    return -1, num_anomaly_points


def _configure_rca(
    engine: str,
    top_k: int | None,
    use_rrcf: bool,
    use_bocpd: bool,
) -> CorrelationAnalyzer:
    if use_rrcf:
        import src.anomaly_detector

        src.anomaly_detector.USE_RRCF = True
    if use_bocpd:
        import src.anomaly_detector

        src.anomaly_detector.USE_BOCPD = True

    rca = CorrelationAnalyzer(correlation_threshold=0.5)
    if engine == "baro":
        rca.use_baro = True
    elif engine == "default":
        rca.use_baro = False

    if top_k is not None:
        rca.baro_top_k = top_k

    return rca


def run_e2e_benchmark(
    sample_size: int | None = None,
    engine: str = "baro",
    top_k: int | None = 3,
    use_rrcf: bool = False,
    use_bocpd: bool = True,
    verbose: bool = False,
) -> dict:
    if not os.path.exists(GROUND_TRUTH_PATH):
        print(f"Error: {GROUND_TRUTH_PATH} not found.")
        sys.exit(1)

    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    run_keys = _sample_run_keys(ground_truth, sample_size)
    rca = _configure_rca(engine, top_k, use_rrcf, use_bocpd)
    healer = SelfHealer(RUNBOOKS_PATH)
    verifier = VerificationEngine()
    eval_top_k = rca.baro_top_k

    total = 0
    detected = 0
    service_top1 = 0
    service_topk = 0
    fault_correct = 0
    runbook_e2e = 0
    runbook_oracle = 0
    verify_success = 0
    pipeline_success = 0
    latencies_ms: list[float] = []
    verify_latencies_ms: list[float] = []
    per_run: list[dict] = []
    y_true: list[str] = []
    y_pred: list[str] = []

    t0_all = time.perf_counter()

    print("\n=======================================================")
    print("         E2E BENCHMARK: DETECT → DECIDE (OFFLINE)      ")
    print("=======================================================\n")
    print(f"Runs: {len(run_keys)} | RCA engine: {engine} | top-k: {eval_top_k} | BOCPD: {use_bocpd}")

    for idx, run_key in enumerate(run_keys):
        gt = ground_truth[run_key]
        true_service = gt["target_service"]
        true_fault = gt["suspected_fault_type"]
        expected_runbook = gt["matched_runbook"]
        service_fault = gt["service_fault"]
        run_id = gt["run_id"]
        inject_time = gt["inject_time"]

        run_dir = os.path.join(DATASET_DIR, service_fault, run_id)
        metrics_path = os.path.join(run_dir, "simple_metrics.csv")
        logs_path = os.path.join(run_dir, "logs.csv")

        total += 1
        row: dict = {
            "run_key": run_key,
            "true_service": true_service,
            "true_fault": true_fault,
            "expected_runbook": expected_runbook,
            "detected": False,
        }

        if not os.path.exists(metrics_path) or not os.path.exists(logs_path):
            row["error"] = "missing dataset files"
            per_run.append(row)
            y_true.append(true_service)
            y_pred.append("missing_data")
            if verbose:
                print(f"[{idx + 1}/{len(run_keys)}] {run_key} — SKIP (missing data)")
            continue

        df_metrics = pd.read_csv(metrics_path).sort_values("time").reset_index(drop=True)
        df_logs = pd.read_csv(logs_path)

        inject_row_idx = df_metrics[df_metrics["time"] >= inject_time].index.min()
        inject_row_idx_sliced = None

        if use_bocpd:
            start_idx = max(0, int(inject_row_idx) - EVAL_BOCPD_WINDOW_BEFORE)
            end_idx = min(len(df_metrics) - 1, int(inject_row_idx) + EVAL_BOCPD_WINDOW_AFTER)
            df_sliced = df_metrics.iloc[start_idx : end_idx + 1].reset_index(drop=True)
            inject_row_idx_sliced = df_sliced[df_sliced["time"] >= inject_time].index.min()
            if pd.isna(inject_row_idx_sliced):
                inject_row_idx_sliced = len(df_sliced) - 1
            baseline_len = min(EVAL_BOCPD_BASELINE_LENGTH, int(inject_row_idx_sliced))
            detection_results = run_metric_anomaly_detection(df_sliced, baseline_len)
            detection_idx, _ = _find_detection_idx(
                df_sliced, inject_time, detection_results, True, int(inject_row_idx_sliced)
            )
            if detection_idx >= 0:
                detect_time = df_sliced.iloc[detection_idx]["time"]
                detection_idx = int(df_metrics[df_metrics["time"] == detect_time].index[0])
        else:
            detection_results = run_metric_anomaly_detection(df_metrics, BASELINE_LENGTH)
            detection_idx, _ = _find_detection_idx(
                df_metrics, inject_time, detection_results, False, None
            )

        if detection_idx < 0:
            y_true.append(true_service)
            y_pred.append("undetected")
            per_run.append(row)
            if verbose:
                print(f"[{idx + 1}/{len(run_keys)}] {run_key} — NOT DETECTED")
            continue

        detected += 1
        row["detected"] = True

        time_start = int(df_metrics["time"].min())
        time_end = int(df_metrics["time"].max())
        log_parser = Drain3LogParser(service_aware=True)
        df_log_ts, temp_info = log_parser.parse_logs(df_logs, time_start, time_end)

        rca.baseline_len = BASELINE_LENGTH
        pred_service, pred_fault, reasoning, confidence = rca.analyze(
            df_metrics=df_metrics,
            df_logs=df_log_ts,
            template_info=temp_info,
            anomaly_idx=detection_idx,
            window_size=120,
        )

        top_k_candidates = rca.last_top_k[:eval_top_k]
        service_ok = pred_service == true_service
        in_top_k = true_service in top_k_candidates
        fault_ok = pred_fault == true_fault

        if service_ok:
            service_top1 += 1
        if in_top_k:
            service_topk += 1
        if fault_ok:
            fault_correct += 1

        y_true.append(true_service)
        y_pred.append(pred_service)

        anomaly_context = {
            "target_service": pred_service,
            "suspected_fault_type": pred_fault,
            "system": "E-COMMERCE",
            "namespace": "production",
            "deployment": f"deployment/{pred_service}",
        }

        t_decide = time.perf_counter()
        decide_result = healer.decide(anomaly_context)
        latencies_ms.append((time.perf_counter() - t_decide) * 1000)

        pred_runbook = decide_result["matched_runbook"]
        runbook_ok = pred_runbook == expected_runbook

        if runbook_ok:
            runbook_e2e += 1

        first_action = decide_result["action_plan"][0] if decide_result["action_plan"] else None
        verify_ok = False
        verify_next_action = "ESCALATE"
        verify_regression = False

        if first_action:
            action_executed = SimpleNamespace(
                action=first_action["action"],
                target=first_action["target"],
                status="COMPLETED",
                execution_time_seconds=45,
            )
            target_service = first_action["target"].split("/")[-1]
            post_telemetry = [
                SimpleNamespace(
                    service=target_service,
                    signal_name="service_error_rate",
                    value=0.0,
                ),
                SimpleNamespace(
                    service=target_service,
                    signal_name="service_latency_p95",
                    value=0.03,
                ),
            ]
            t_verify = time.perf_counter()
            verify_ok, verify_regression, verify_next_action, _ = verifier.verify_action(
                action_executed,
                post_telemetry,
            )
            verify_latencies_ms.append((time.perf_counter() - t_verify) * 1000)

        if verify_ok and verify_next_action == "DONE":
            verify_success += 1
        if service_ok and runbook_ok and verify_ok:
            pipeline_success += 1

        oracle_ctx = dict(anomaly_context)
        oracle_ctx["suspected_fault_type"] = true_fault
        oracle_runbook = healer.decide(oracle_ctx)["matched_runbook"]
        oracle_ok = oracle_runbook == expected_runbook
        if oracle_ok:
            runbook_oracle += 1

        row.update(
            {
                "pred_service": pred_service,
                "pred_fault": pred_fault,
                "pred_runbook": pred_runbook,
                "oracle_runbook": oracle_runbook,
                "service_correct": service_ok,
                "service_in_top_k": in_top_k,
                "top_k_candidates": top_k_candidates,
                "fault_correct": fault_ok,
                "runbook_correct_e2e": runbook_ok,
                "runbook_correct_oracle": oracle_ok,
                "verify_success": verify_ok,
                "verify_next_action": verify_next_action,
                "verify_regression_detected": verify_regression,
                "pipeline_success": service_ok and runbook_ok,
                "confidence": round(confidence, 3),
                "decide_latency_ms": round(latencies_ms[-1], 2),
                "verify_latency_ms": round(verify_latencies_ms[-1], 2) if verify_latencies_ms else 0,
            }
        )
        per_run.append(row)

        if verbose:
            print(
                f"[{idx + 1}/{len(run_keys)}] {run_key} | "
                f"svc={'OK' if service_ok else pred_service} | "
                f"fault={'OK' if fault_ok else pred_fault} | "
                f"runbook={'OK' if runbook_ok else pred_runbook}"
            )

    duration_s = time.perf_counter() - t0_all

    from sklearn.metrics import precision_recall_fscore_support

    # Align with evaluate.py: macro metrics over ground-truth service labels only
    unique_true_classes = sorted(list(set(y_true)))
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=unique_true_classes,
        average="macro",
        zero_division=0,
    )

    p99 = sorted(latencies_ms)[int(0.99 * len(latencies_ms)) - 1] if latencies_ms else 0.0

    report = {
        "benchmark": "detect_decide_e2e",
        "total_runs": total,
        "detect": {
            "detection_rate": round(detected / total, 4) if total else 0,
            "detected_runs": detected,
            "service_top1_accuracy": round(service_top1 / total, 4) if total else 0,
            "service_top1_on_detected": round(service_top1 / detected, 4) if detected else 0,
            f"service_top{eval_top_k}_accuracy": round(service_topk / total, 4) if total else 0,
            "fault_type_accuracy_on_detected": round(fault_correct / detected, 4) if detected else 0,
            "macro_precision": round(float(precision), 4),
            "macro_recall": round(float(recall), 4),
            "macro_f1": round(float(f1), 4),
        },
        "decide": {
            "runbook_accuracy_e2e": round(runbook_e2e / detected, 4) if detected else 0,
            "runbook_accuracy_e2e_over_all": round(runbook_e2e / total, 4) if total else 0,
            "runbook_accuracy_oracle_fault": round(runbook_oracle / detected, 4) if detected else 0,
            "correct_runbook_e2e": runbook_e2e,
            "pipeline_success_rate": round(pipeline_success / total, 4) if total else 0,
            "pipeline_success_count": pipeline_success,
        },
        "verify": {
            "success_rate_on_detected": round(verify_success / detected, 4) if detected else 0,
            "success_count": verify_success,
        },
        "latency_ms": {
            "decide_mean": round(sum(latencies_ms) / len(latencies_ms), 2) if latencies_ms else 0,
            "decide_p99": round(p99, 2),
            "verify_mean": round(sum(verify_latencies_ms) / len(verify_latencies_ms), 2)
            if verify_latencies_ms
            else 0,
        },
        "config": {
            "rca_engine": engine,
            "top_k": eval_top_k,
            "use_bocpd": use_bocpd,
            "use_rrcf": use_rrcf,
        },
        "duration_seconds": round(duration_s, 2),
        "per_run": per_run,
    }
    return report


def _print_summary(report: dict) -> None:
    d = report["detect"]
    c = report["decide"]
    v = report["verify"]
    top_k = report["config"]["top_k"]

    print("\n=======================================================")
    print("              E2E BENCHMARK SUMMARY REPORT               ")
    print("=======================================================")
    print(f"Duration:                    {report['duration_seconds']}s")
    print(f"Total runs:                  {report['total_runs']}")
    print("--- Detect (RCA) ---")
    print(f"Anomaly detection rate:      {d['detection_rate'] * 100:.1f}% ({d['detected_runs']}/{report['total_runs']})")
    print(f"Service Top-1 accuracy:      {d['service_top1_accuracy'] * 100:.1f}%")
    print(f"Service Top-{top_k} accuracy:     {d[f'service_top{top_k}_accuracy'] * 100:.1f}%")
    print(f"Macro-Precision:             {d['macro_precision']:.3f}")
    print(f"Macro-Recall:                {d['macro_recall']:.3f}")
    print(f"Macro-F1:                      {d['macro_f1']:.3f} (threshold: 0.85)")
    if d["macro_f1"] >= 0.85:
        print("[SUCCESS] Macro-F1 passes the 0.85 specification threshold.")
    else:
        print("[WARNING] Macro-F1 is below the 0.85 specification threshold.")
    print("--- Decide (chained from detect output) ---")
    print(f"Fault type accuracy:         {d['fault_type_accuracy_on_detected'] * 100:.1f}% (on detected)")
    print(f"Runbook accuracy (E2E):      {c['runbook_accuracy_e2e'] * 100:.1f}% ({c['correct_runbook_e2e']}/{d['detected_runs']} detected)")
    print(f"Runbook accuracy (oracle):   {c['runbook_accuracy_oracle_fault'] * 100:.1f}% (GT fault, on detected)")
    print("--- Verify (mock post-healing telemetry) ---")
    print(f"Verify success rate:         {v['success_rate_on_detected'] * 100:.1f}% ({v['success_count']}/{d['detected_runs']} detected)")
    print(f"Full pipeline success:       {c['pipeline_success_rate'] * 100:.1f}% (detect+svc+runbook)")
    print(f"Decide latency (mean):       {report['latency_ms']['decide_mean']} ms")
    print("=======================================================\n")


def main():
    parser = argparse.ArgumentParser(
        description="E2E offline benchmark: detect (BARO RCA) → decide (runbook)"
    )
    parser.add_argument("--sample-size", type=int, default=90, help="Runs to evaluate (default: 90)")
    parser.add_argument("--engine", choices=["config", "default", "baro"], default="baro")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--use-rrcf", action="store_true")
    parser.add_argument("--no-bocpd", action="store_true", help="Disable BOCPD window slicing (default: BOCPD on)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Per-run log lines")
    parser.add_argument(
        "--output",
        default=os.path.join(
            AI_ENGINE_ROOT,
            "dataset",
            "benchmark_reports",
            "benchmark_e2e.json",
        ),
    )
    args = parser.parse_args()

    use_bocpd = not args.no_bocpd

    report = run_e2e_benchmark(
        sample_size=args.sample_size,
        engine=args.engine,
        top_k=args.top_k,
        use_rrcf=args.use_rrcf,
        use_bocpd=use_bocpd,
        verbose=args.verbose,
    )

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    _print_summary(report)
    print(f"Report saved: {args.output}")


if __name__ == "__main__":
    main()
