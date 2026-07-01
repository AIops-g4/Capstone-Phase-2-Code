"""
Benchmark detect on benchmark_fixtures dataset per service.

For each service (adservice, cartservice, etc.), loads all runs from folders
100, 200, 300, 400, 500, 3000 and calls the AI Engine /v1/detect API.

Metrics per service:
  - TP  (True Positive):   API detected anomaly AND the run contains anomaly rows
  - FP  (False Positive):  API detected anomaly BUT the run has NO anomaly rows
  - FN  (False Negative):  API did NOT detect anomaly BUT the run DOES have anomaly rows
  - TN  (True Negative):   API did NOT detect anomaly AND the run has no anomaly rows
  - Precision = TP / (TP + FP)
  - Recall    = TP / (TP + FN)
  - F1        = 2 * Precision * Recall / (Precision + Recall)

Usage:
  python benchmark_fixture_detect.py                          # all services
  python benchmark_fixture_detect.py --services adservice     # single service
  python benchmark_fixture_detect.py --run-sizes 100 500      # specific sizes
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DETECT_DECIDE_DIR = os.path.dirname(SCRIPT_DIR)
AI_ENGINE_ROOT = os.path.dirname(DETECT_DECIDE_DIR)
sys.path.insert(0, DETECT_DECIDE_DIR)

try:
    from src.config import API_HOST, API_PORT, DATASET_DIR
except ImportError:
    API_HOST = os.environ.get("API_HOST", "127.0.0.1")
    API_PORT = int(os.environ.get("API_PORT", "8050"))
    DATASET_DIR = os.environ.get(
        "DATASET_DIR",
        os.path.join(AI_ENGINE_ROOT, "dataset"),
    )

TENANT_ID = "d3b07384-d113-495f-9f58-20d18d357d75"

RUN_SIZES = ["100", "200", "300", "400", "500", "3000"]

SERVICES = [
    "adservice", "cartservice", "checkoutservice", "currencyservice",
    "emailservice", "frontend", "paymentservice", "productcatalogservice",
    "recommendationservice", "redis", "shippingservice",
]

BENCHMARK_FIXTURES_DIR = os.path.join(DATASET_DIR, "benchmark_fixtures")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(ts: int | float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _headers(correlation_id: str, idempotency_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Tenant-Id": TENANT_ID,
        "Authorization": "Bearer benchmark-token",
        "X-Correlation-Id": correlation_id,
        "Idempotency-Key": idempotency_key,
        "X-Dry-Run-Mode": "true",
    }


def _post_json(
    base_url: str,
    path: str,
    payload: dict,
    correlation_id: str,
    idempotency_key: str,
    timeout: float = 900.0,
) -> dict:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(correlation_id, idempotency_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} failed: HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot connect to API server at {base_url}. "
            f"Start it with: python -m src.server"
        ) from exc


def _load_simple_metrics(run_dir: str) -> tuple[list[dict[str, Any]], bool] | None:
    """
    Load simple_metrics.csv from a run directory.
    Returns (rows, has_anomaly) or None if file not found.
    Each row is a dict with 'time', 'is_anomaly', and all metric signal columns.
    """
    path = os.path.join(run_dir, "simple_metrics.csv")
    if not os.path.exists(path):
        return None

    rows: list[dict[str, Any]] = []
    has_anomaly = False

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cleaned: dict[str, Any] = {}
            for k, v in row.items():
                k = k.strip()
                v = v.strip() if v else ""
                if k == "time" or k == "is_anomaly":
                    cleaned[k] = v
                else:
                    try:
                        cleaned[k] = float(v)
                    except ValueError:
                        cleaned[k] = 0.0
            rows.append(cleaned)
            if cleaned.get("is_anomaly", "").strip().lower() in ("true", "1", "yes"):
                has_anomaly = True

    rows.sort(key=lambda r: float(r.get("time", 0)))
    return rows, has_anomaly


def _metric_rows_to_telemetry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert simple_metrics rows to telemetry points for the API."""
    telemetry: list[dict[str, Any]] = []
    for row in rows:
        ts = _iso(float(row["time"]))
        for col, value in row.items():
            if col == "time" or col == "is_anomaly" or value == "":
                continue
            if not isinstance(value, (int, float)):
                continue
            signal_name = str(col).strip()
            service = signal_name.split("_", 1)[0] if "_" in signal_name else signal_name
            telemetry.append({
                "ts": ts,
                "tenant_id": TENANT_ID,
                "service": service,
                "signal_name": signal_name,
                "value": float(value),
                "labels": {},
            })
    return telemetry


def _anomaly_timestamps(rows: list[dict[str, Any]]) -> list[float]:
    """Return list of timestamps where is_anomaly == True."""
    return [
        float(r["time"])
        for r in rows
        if r.get("is_anomaly", "").strip().lower() in ("true", "1", "yes")
    ]


# ---------------------------------------------------------------------------
# Core: call detect API for one run
# ---------------------------------------------------------------------------

def _call_detect(
    base_url: str,
    service: str,
    run_size: str,
    telemetry: list[dict[str, Any]],
) -> dict:
    """Call /v1/detect with the telemetry data."""
    correlation_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())

    payload = {
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "dry_run_mode": True,
        "telemetry_window": telemetry,
    }

    return _post_json(base_url, "/v1/detect", payload, correlation_id, idempotency_key)


# ---------------------------------------------------------------------------
# Per-service benchmark
# ---------------------------------------------------------------------------

def benchmark_service(
    base_url: str,
    service: str,
    run_sizes: list[str] | None = None,
) -> dict:
    """
    Run benchmark for a single service across all run sizes.

    Returns a dict with per-run details + aggregate metrics.
    """
    if run_sizes is None:
        run_sizes = RUN_SIZES

    per_run: list[dict[str, Any]] = []
    tp = 0
    fp = 0
    fn = 0
    tn = 0
    errors = 0

    for run_size in run_sizes:
        run_dir = os.path.join(BENCHMARK_FIXTURES_DIR, service, run_size)
        if not os.path.isdir(run_dir):
            print(f"  [SKIP] {service}/{run_size}: directory not found")
            continue

        loaded = _load_simple_metrics(run_dir)
        if loaded is None:
            print(f"  [SKIP] {service}/{run_size}: no simple_metrics.csv")
            continue

        rows, ground_truth_anomaly = loaded
        anomaly_times = _anomaly_timestamps(rows)

        # Build telemetry payload (exclude is_anomaly column)
        telemetry = _metric_rows_to_telemetry(rows)

        # Call detect API
        try:
            response = _call_detect(base_url, service, run_size, telemetry)
            anomaly_detected = response.get("anomaly_detected", False)
            severity = response.get("severity", 0.0)
            confidence = response.get("confidence", 0.0)
            reasoning = response.get("reasoning", "")
            anomaly_context = response.get("anomaly_context") or {}
            predicted_service = anomaly_context.get("target_service", "unknown")
        except Exception as exc:
            print(f"  [ERROR] {service}/{run_size}: {exc}")
            per_run.append({
                "service": service,
                "run_size": run_size,
                "ground_truth_anomaly": ground_truth_anomaly,
                "anomaly_detected": None,
                "error": str(exc),
            })
            errors += 1
            continue

        # Classify
        if anomaly_detected and ground_truth_anomaly:
            tp += 1
            classification = "TP"
        elif anomaly_detected and not ground_truth_anomaly:
            fp += 1
            classification = "FP"
        elif not anomaly_detected and ground_truth_anomaly:
            fn += 1
            classification = "FN"
        else:
            tn += 1
            classification = "TN"

        per_run.append({
            "service": service,
            "run_size": run_size,
            "ground_truth_anomaly": ground_truth_anomaly,
            "anomaly_timestamps": anomaly_times,
            "anomaly_detected": bool(anomaly_detected),
            "severity": severity,
            "confidence": confidence,
            "predicted_service": predicted_service,
            "reasoning": reasoning,
            "classification": classification,
        })

        print(
            f"  [{classification}] {service}/{run_size}: "
            f"gt_anomaly={ground_truth_anomaly}, "
            f"detected={anomaly_detected}, "
            f"conf={confidence:.3f}"
        )

    total = len(per_run) - errors
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 0.0

    metrics = {
        "service": service,
        "total_runs": total,
        "errors": errors,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
    }
    return {"metrics": metrics, "per_run": per_run}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark /v1/detect on benchmark_fixtures per service"
    )
    parser.add_argument(
        "--api-url",
        default=f"http://{API_HOST}:{API_PORT}",
        help="AI Engine API base URL",
    )
    parser.add_argument(
        "--services",
        nargs="+",
        default=None,
        help="Specific services to benchmark (default: all)",
    )
    parser.add_argument(
        "--run-sizes",
        nargs="+",
        default=RUN_SIZES,
        help=f"Run sizes to include (default: {' '.join(RUN_SIZES)})",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(
            AI_ENGINE_ROOT,
            "dataset",
            "benchmark_reports",
            "benchmark_fixture_detect.json",
        ),
        help="Output JSON report path",
    )
    args = parser.parse_args()

    services = args.services or SERVICES
    base_url = args.api_url

    print("=" * 70)
    print("  BENCHMARK FIXTURE DETECT — PER SERVICE")
    print("=" * 70)
    print(f"  API URL:        {base_url}")
    print(f"  Services:       {', '.join(services)}")
    print(f"  Run sizes:      {', '.join(args.run_sizes)}")
    print("=" * 70)

    start = time.perf_counter()
    results: list[dict] = []
    all_per_run: list[dict] = []

    for idx, service in enumerate(services, 1):
        print(f"\n[{idx}/{len(services)}] Benchmarking: {service}")
        result = benchmark_service(base_url, service, args.run_sizes)
        results.append(result)
        all_per_run.extend(result["per_run"])

        m = result["metrics"]
        print(f"  --- {service} Summary ---")
        print(f"  TP={m['tp']}  FP={m['fp']}  FN={m['fn']}  TN={m['tn']}")
        print(f"  Precision={m['precision']:.4f}  Recall={m['recall']:.4f}  F1={m['f1']:.4f}  Accuracy={m['accuracy']:.4f}")

    duration = time.perf_counter() - start

    # Aggregate metrics
    all_metrics = [r["metrics"] for r in results]
    total_tp = sum(m["tp"] for m in all_metrics)
    total_fp = sum(m["fp"] for m in all_metrics)
    total_fn = sum(m["fn"] for m in all_metrics)
    total_tn = sum(m["tn"] for m in all_metrics)
    total_runs = sum(m["total_runs"] for m in all_metrics)
    total_errors = sum(m["errors"] for m in all_metrics)

    # Macro average: mean of per-service scores
    macro_n = len(all_metrics)
    macro_precision = sum(m["precision"] for m in all_metrics) / macro_n if macro_n > 0 else 0.0
    macro_recall = sum(m["recall"] for m in all_metrics) / macro_n if macro_n > 0 else 0.0
    macro_f1 = sum(m["f1"] for m in all_metrics) / macro_n if macro_n > 0 else 0.0

    # Micro average: global counts
    micro_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    micro_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = (2 * micro_precision * micro_recall / (micro_precision + micro_recall)
                if (micro_precision + micro_recall) > 0 else 0.0)
    micro_accuracy = (total_tp + total_tn) / total_runs if total_runs > 0 else 0.0

    report = {
        "benchmark": "benchmark_fixture_detect",
        "api_url": base_url,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(duration, 2),
        "total_runs": total_runs,
        "total_errors": total_errors,
        "aggregate": {
            "micro": {
                "tp": total_tp,
                "fp": total_fp,
                "fn": total_fn,
                "tn": total_tn,
                "precision": round(micro_precision, 4),
                "recall": round(micro_recall, 4),
                "f1": round(micro_f1, 4),
                "accuracy": round(micro_accuracy, 4),
            },
            "macro": {
                "precision": round(macro_precision, 4),
                "recall": round(macro_recall, 4),
                "f1": round(macro_f1, 4),
            },
        },
        "per_service": [r["metrics"] for r in results],
        "per_run": all_per_run,
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    # Print final summary
    print("\n" + "=" * 70)
    print("  FINAL SUMMARY")
    print("=" * 70)
    print(f"  Total runs:      {total_runs}")
    print(f"  Total errors:    {total_errors}")
    print(f"  Duration:        {duration:.2f}s")
    print()
    print(f"  MICRO AVERAGE:")
    print(f"    TP={total_tp}  FP={total_fp}  FN={total_fn}  TN={total_tn}")
    print(f"    Precision={micro_precision:.4f}  Recall={micro_recall:.4f}  F1={micro_f1:.4f}  Accuracy={micro_accuracy:.4f}")
    print()
    for m in all_metrics:
        print(f"  {m['service']:25s}  TP={m['tp']}  FP={m['fp']}  FN={m['fn']}  TN={m['tn']}  "
              f"P={m['precision']:.4f}  R={m['recall']:.4f}  F1={m['f1']:.4f}")
    print("=" * 70)
    print(f"  Report saved: {args.output}")


if __name__ == "__main__":
    main()