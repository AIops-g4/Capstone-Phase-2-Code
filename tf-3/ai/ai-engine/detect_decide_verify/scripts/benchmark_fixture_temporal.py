"""
Temporal per-service detect benchmark on the benchmark_fixtures dataset.

Unlike scripts/benchmark_fixture_detect.py (which classifies on the mere
*presence* of an anomaly), this script classifies on *timing*, matching the
definition requested by the team:

  TP  (True Positive) : the API detected an anomaly whose detected time is
                        "close enough" to the true injection point, i.e. inside
                        the tolerance band [onset - lead_tolerance, onset + late_tolerance].
                        This is the "detect gan dung voi anomaly nhat" case.

  FP  (False Positive): the API detected an anomaly EITHER
                          (a) too early  -> detected_time < onset - lead_tolerance
                                            ("detect qua som" => spam alert), OR
                          (b) on a run that has NO ground-truth anomaly at all.

  FN  (False Negative): the run DOES contain a ground-truth anomaly but the API
                        either detected nothing, or detected it too late
                        (detected_time > onset + late_tolerance) to be useful.

  TN  (True Negative) : the run has no ground-truth anomaly and the API detected none.

Metrics per service:
  Precision = TP / (TP + FP)
  Recall    = TP / (TP + FN)
  F1        = 2 * P * R / (P + R)

--------------------------------------------------------------------------------
IMPORTANT — how the detected time is obtained
--------------------------------------------------------------------------------
The strict DetectResponse contract does NOT expose the detected timestamp.
The engine, however, embeds it at:

    llm_fault_rank_evidence.bocpd_input_summary.anomaly_time   (unix seconds)

That field survives only when the server returns the *raw* dict (the
`telemetry_source` code path). In normal CDO push mode it is pruned.

To keep this benchmark working against the real contract WITHOUT changing the
server, we recover the detected time using a "detection prefix sweep":

  For a run with a ground-truth anomaly, we send progressively longer prefixes
  of the telemetry window and find the FIRST prefix length at which the API
  flips `anomaly_detected` from False to True. The timestamp of the last point
  in that prefix is treated as the "detected time". This mirrors an online
  detector reacting as data streams in.

The sweep is bounded by --sweep-step and --max-sweeps to keep it cheap. If you
run the server in a bench mode that returns anomaly_time directly, pass
--use-server-time to skip the sweep and read it from the response.

Usage:
  python benchmark_fixture_temporal.py
  python benchmark_fixture_temporal.py --services adservice cartservice
  python benchmark_fixture_temporal.py --run-sizes 100 500 3000
  python benchmark_fixture_temporal.py --lead-tolerance 15 --late-tolerance 60
  python benchmark_fixture_temporal.py --use-server-time
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
from typing import Any, Optional

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
# CSV metric-type -> contract signal_name mapping
# ---------------------------------------------------------------------------
# The strict DetectRequest schema rejects any signal_name outside the 12-value
# telemetry contract enum (adr/telemetry_signal_names.json). The raw CSV columns
# look like "adservice_cpu", "cartservice_delay", etc., where the suffix is one of
# the platform-profile metric_types: cpu, mem, delay, loss, socket, disk.
#
# We split each column into (service, metric_suffix), map the suffix to a valid
# contract signal_name, and send service + signal_name on the wire. The engine's
# telemetry processor then reconstructs an internal column
# f"{service}_{signal_name}" that the BOCPD/EWMA/RCA logic can still attribute:
#   - delay -> service_latency_p95  (column contains "latency" -> routed to EWMA)
#   - loss  -> service_error_rate   (column contains "error"   -> routed to EWMA)
#   - cpu/mem/socket/disk -> container_resource_usage (resource -> routed to BOCPD)
METRIC_SUFFIX_TO_SIGNAL = {
    "cpu": "container_resource_usage",
    "mem": "container_resource_usage",
    "socket": "container_resource_usage",
    "disk": "container_resource_usage",
    "diskio": "container_resource_usage",
    "delay": "service_latency_p95",
    "latency": "service_latency_p95",
    "loss": "service_error_rate",
    "error": "service_error_rate",
}
DEFAULT_SIGNAL = "container_resource_usage"


def _split_column(col: str) -> tuple[str, str]:
    """Split a CSV metric column into (service, metric_suffix).

    e.g. "adservice_cpu" -> ("adservice", "cpu")
         "recommendationservice_delay" -> ("recommendationservice", "delay")
         "redis_mem" -> ("redis", "mem")
    Falls back to (col, col) if there is no underscore.
    """
    if "_" not in col:
        return col, col
    service, suffix = col.rsplit("_", 1)
    return service, suffix


def _map_signal(metric_suffix: str) -> str:
    return METRIC_SUFFIX_TO_SIGNAL.get(metric_suffix.strip().lower(), DEFAULT_SIGNAL)

# Default tolerance band (seconds). These are the "co the set sau" knobs.
DEFAULT_LEAD_TOLERANCE = 30.0   # how far BEFORE the onset a detection may fire and still count as TP
DEFAULT_LATE_TOLERANCE = 60.0   # how far AFTER the onset a detection may fire and still count as TP


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _iso(ts: float) -> str:
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


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def _load_simple_metrics(run_dir: str) -> Optional[list[dict[str, Any]]]:
    """Load simple_metrics.csv rows (sorted by time). Returns None if missing."""
    path = os.path.join(run_dir, "simple_metrics.csv")
    if not os.path.exists(path):
        return None

    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cleaned: dict[str, Any] = {}
            for k, v in row.items():
                k = (k or "").strip()
                v = v.strip() if v else ""
                if k in ("time", "is_anomaly"):
                    cleaned[k] = v
                else:
                    try:
                        cleaned[k] = float(v)
                    except ValueError:
                        cleaned[k] = 0.0
            rows.append(cleaned)

    rows.sort(key=lambda r: float(r.get("time", 0)))
    return rows


def _is_anom_flag(row: dict[str, Any]) -> bool:
    return str(row.get("is_anomaly", "")).strip().lower() in ("true", "1", "yes")


def _ground_truth_onset(rows: list[dict[str, Any]]) -> Optional[float]:
    """Return the time of the FIRST is_anomaly=True row, or None if the run is clean."""
    for r in rows:
        if _is_anom_flag(r):
            return float(r["time"])
    return None


def _rows_to_telemetry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert simple_metrics rows to telemetry points (excludes is_anomaly)."""
    telemetry: list[dict[str, Any]] = []
    for row in rows:
        ts = _iso(float(row["time"]))
        for col, value in row.items():
            if col in ("time", "is_anomaly") or value == "":
                continue
            if not isinstance(value, (int, float)):
                continue
            service, metric_suffix = _split_column(str(col).strip())
            signal_name = _map_signal(metric_suffix)
            telemetry.append({
                "ts": ts,
                "tenant_id": TENANT_ID,
                "service": service,
                "signal_name": signal_name,
                "value": float(value),
                "labels": {"system": "E-COMMERCE"},
            })
    return telemetry


def _detect(base_url: str, telemetry: list[dict[str, Any]]) -> dict:
    correlation_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())
    payload = {
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "dry_run_mode": True,
        "telemetry_window": telemetry,
    }
    return _post_json(base_url, "/v1/detect", payload, correlation_id, idempotency_key)


def _server_reported_time(response: dict) -> Optional[float]:
    """Read the detected timestamp from the raw engine response, if present."""
    evidence = response.get("llm_fault_rank_evidence") or {}
    summary = evidence.get("bocpd_input_summary") or {}
    t = summary.get("anomaly_time")
    return float(t) if isinstance(t, (int, float)) else None


# ---------------------------------------------------------------------------
# Detected-time recovery
# ---------------------------------------------------------------------------

def _detected_time_via_sweep(
    base_url: str,
    rows: list[dict[str, Any]],
    sweep_step: int,
    max_sweeps: int,
    min_prefix: int,
) -> tuple[Optional[float], bool, dict]:
    """
    Send growing prefixes of the window until the API first reports an anomaly.

    Returns (detected_time, anomaly_detected_ever, last_response).
    detected_time = time of the last row of the first prefix that flips to True.
    """
    n = len(rows)
    last_response: dict = {}
    prefix = max(min_prefix, sweep_step)
    sweeps = 0

    while prefix <= n and sweeps < max_sweeps:
        prefix_rows = rows[:prefix]
        telemetry = _rows_to_telemetry(prefix_rows)
        last_response = _detect(base_url, telemetry)
        if last_response.get("anomaly_detected"):
            detected_time = float(prefix_rows[-1]["time"])
            # Prefer the engine-reported onset if it leaked through.
            server_t = _server_reported_time(last_response)
            if server_t is not None:
                detected_time = server_t
            return detected_time, True, last_response
        prefix += sweep_step
        sweeps += 1

    # Final full-window check if we never flipped and haven't scanned the tail.
    if prefix - sweep_step < n and sweeps < max_sweeps:
        telemetry = _rows_to_telemetry(rows)
        last_response = _detect(base_url, telemetry)
        if last_response.get("anomaly_detected"):
            server_t = _server_reported_time(last_response)
            detected_time = server_t if server_t is not None else float(rows[-1]["time"])
            return detected_time, True, last_response

    return None, False, last_response


def _detected_time_single_call(
    base_url: str,
    rows: list[dict[str, Any]],
    use_server_time: bool,
) -> tuple[Optional[float], bool, dict]:
    """One full-window call. Detected time from server field or last row."""
    telemetry = _rows_to_telemetry(rows)
    response = _detect(base_url, telemetry)
    detected = bool(response.get("anomaly_detected"))
    if not detected:
        return None, False, response
    if use_server_time:
        t = _server_reported_time(response)
        if t is not None:
            return t, True, response
    # Fallback: no timestamp available -> use last row time as an upper bound.
    return float(rows[-1]["time"]), True, response


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(
    ground_truth_onset: Optional[float],
    detected_time: Optional[float],
    lead_tolerance: float,
    late_tolerance: float,
) -> tuple[str, Optional[float]]:
    """
    Returns (classification, lead_time_seconds).
    lead_time = onset - detected_time  (positive => detected before onset = early).
    """
    has_gt = ground_truth_onset is not None
    detected = detected_time is not None

    if not has_gt:
        # Clean run: any detection is spam (FP), otherwise TN.
        return ("FP" if detected else "TN"), None

    # Run contains a real anomaly.
    if not detected:
        return "FN", None

    lead = float(ground_truth_onset) - float(detected_time)  # >0 early, <0 late
    lower = ground_truth_onset - lead_tolerance
    upper = ground_truth_onset + late_tolerance

    if detected_time < lower:
        return "FP", lead          # too early -> spam alert
    if detected_time > upper:
        return "FN", lead          # too late to be useful
    return "TP", lead              # inside the tolerance band


# ---------------------------------------------------------------------------
# Per-service benchmark
# ---------------------------------------------------------------------------

def benchmark_service(
    base_url: str,
    service: str,
    run_sizes: list[str],
    lead_tolerance: float,
    late_tolerance: float,
    use_server_time: bool,
    no_sweep: bool,
    sweep_step: int,
    max_sweeps: int,
    min_prefix: int,
) -> dict:
    per_run: list[dict[str, Any]] = []
    tp = fp = fn = tn = errors = 0

    for run_size in run_sizes:
        run_dir = os.path.join(BENCHMARK_FIXTURES_DIR, service, run_size)
        if not os.path.isdir(run_dir):
            print(f"  [SKIP] {service}/{run_size}: directory not found")
            continue

        rows = _load_simple_metrics(run_dir)
        if rows is None or not rows:
            print(f"  [SKIP] {service}/{run_size}: no simple_metrics.csv")
            continue

        onset = _ground_truth_onset(rows)

        try:
            if use_server_time or no_sweep:
                detected_time, detected, _resp = _detected_time_single_call(
                    base_url, rows, use_server_time
                )
            else:
                detected_time, detected, _resp = _detected_time_via_sweep(
                    base_url, rows, sweep_step, max_sweeps, min_prefix
                )
        except Exception as exc:
            print(f"  [ERROR] {service}/{run_size}: {exc}")
            per_run.append({
                "service": service,
                "run_size": run_size,
                "ground_truth_onset": onset,
                "detected_time": None,
                "classification": "ERROR",
                "error": str(exc),
            })
            errors += 1
            continue

        classification, lead = _classify(onset, detected_time, lead_tolerance, late_tolerance)

        if classification == "TP":
            tp += 1
        elif classification == "FP":
            fp += 1
        elif classification == "FN":
            fn += 1
        elif classification == "TN":
            tn += 1

        per_run.append({
            "service": service,
            "run_size": run_size,
            "ground_truth_onset": onset,
            "detected": detected,
            "detected_time": detected_time,
            "lead_time_seconds": round(lead, 2) if lead is not None else None,
            "classification": classification,
        })

        lead_str = f"lead={lead:+.1f}s" if lead is not None else "lead=n/a"
        print(
            f"  [{classification}] {service}/{run_size}: "
            f"onset={onset}, detected_time={detected_time}, {lead_str}"
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
        description="Temporal per-service detect benchmark on benchmark_fixtures"
    )
    parser.add_argument("--api-url", default=f"http://{API_HOST}:{API_PORT}")
    parser.add_argument("--services", nargs="+", default=None,
                        help="Services to benchmark (default: all)")
    parser.add_argument("--run-sizes", nargs="+", default=RUN_SIZES,
                        help=f"Run sizes (default: {' '.join(RUN_SIZES)})")
    parser.add_argument("--lead-tolerance", type=float, default=DEFAULT_LEAD_TOLERANCE,
                        help="Seconds a detection may fire BEFORE onset and still be TP "
                             "(earlier than this => FP/spam)")
    parser.add_argument("--late-tolerance", type=float, default=DEFAULT_LATE_TOLERANCE,
                        help="Seconds a detection may fire AFTER onset and still be TP "
                             "(later than this => FN)")
    parser.add_argument("--use-server-time", action="store_true",
                        help="Read anomaly_time from the response instead of sweeping "
                             "(requires the raw engine response to expose it)")
    parser.add_argument("--no-sweep", action="store_true",
                        help="Single full-window call; detected time = last row time "
                             "unless --use-server-time is also set")
    parser.add_argument("--sweep-step", type=int, default=25,
                        help="Rows added per sweep iteration (default: 25)")
    parser.add_argument("--max-sweeps", type=int, default=40,
                        help="Max sweep iterations per run (default: 40)")
    parser.add_argument("--min-prefix", type=int, default=50,
                        help="Minimum prefix length for the first sweep call (default: 50)")
    parser.add_argument("--output", default=os.path.join(
        AI_ENGINE_ROOT, "dataset", "benchmark_reports",
        "benchmark_fixture_temporal.json",
    ))
    args = parser.parse_args()

    services = args.services or SERVICES
    base_url = args.api_url

    print("=" * 72)
    print("  BENCHMARK FIXTURE — TEMPORAL DETECT (PER SERVICE)")
    print("=" * 72)
    print(f"  API URL:         {base_url}")
    print(f"  Services:        {', '.join(services)}")
    print(f"  Run sizes:       {', '.join(args.run_sizes)}")
    print(f"  Lead tolerance:  {args.lead_tolerance}s (earlier => FP/spam)")
    print(f"  Late tolerance:  {args.late_tolerance}s (later   => FN)")
    print(f"  Mode:            "
          f"{'server-time' if args.use_server_time else ('single-call' if args.no_sweep else 'prefix-sweep')}")
    print("=" * 72)

    start = time.perf_counter()
    results: list[dict] = []
    all_per_run: list[dict] = []

    for idx, service in enumerate(services, 1):
        print(f"\n[{idx}/{len(services)}] Benchmarking: {service}")
        result = benchmark_service(
            base_url, service, args.run_sizes,
            args.lead_tolerance, args.late_tolerance,
            args.use_server_time, args.no_sweep,
            args.sweep_step, args.max_sweeps, args.min_prefix,
        )
        results.append(result)
        all_per_run.extend(result["per_run"])

        m = result["metrics"]
        print(f"  --- {service} Summary ---")
        print(f"  TP={m['tp']}  FP={m['fp']}  FN={m['fn']}  TN={m['tn']}")
        print(f"  Precision={m['precision']:.4f}  Recall={m['recall']:.4f}  F1={m['f1']:.4f}")

    duration = time.perf_counter() - start

    all_metrics = [r["metrics"] for r in results]
    total_tp = sum(m["tp"] for m in all_metrics)
    total_fp = sum(m["fp"] for m in all_metrics)
    total_fn = sum(m["fn"] for m in all_metrics)
    total_tn = sum(m["tn"] for m in all_metrics)
    total_runs = sum(m["total_runs"] for m in all_metrics)
    total_errors = sum(m["errors"] for m in all_metrics)

    macro_n = len(all_metrics) or 1
    macro_precision = sum(m["precision"] for m in all_metrics) / macro_n
    macro_recall = sum(m["recall"] for m in all_metrics) / macro_n
    macro_f1 = sum(m["f1"] for m in all_metrics) / macro_n

    micro_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    micro_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = (2 * micro_precision * micro_recall / (micro_precision + micro_recall)
                if (micro_precision + micro_recall) > 0 else 0.0)

    report = {
        "benchmark": "benchmark_fixture_temporal",
        "api_url": base_url,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(duration, 2),
        "tolerance": {"lead_seconds": args.lead_tolerance, "late_seconds": args.late_tolerance},
        "detection_mode": ("server-time" if args.use_server_time
                           else ("single-call" if args.no_sweep else "prefix-sweep")),
        "total_runs": total_runs,
        "total_errors": total_errors,
        "aggregate": {
            "micro": {
                "tp": total_tp, "fp": total_fp, "fn": total_fn, "tn": total_tn,
                "precision": round(micro_precision, 4),
                "recall": round(micro_recall, 4),
                "f1": round(micro_f1, 4),
            },
            "macro": {
                "precision": round(macro_precision, 4),
                "recall": round(macro_recall, 4),
                "f1": round(macro_f1, 4),
            },
        },
        "per_service": all_metrics,
        "per_run": all_per_run,
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print("\n" + "=" * 72)
    print("  FINAL SUMMARY (TEMPORAL)")
    print("=" * 72)
    print(f"  Total runs:   {total_runs}   Errors: {total_errors}   Duration: {duration:.2f}s")
    print(f"  MICRO:  TP={total_tp} FP={total_fp} FN={total_fn} TN={total_tn}  "
          f"P={micro_precision:.4f} R={micro_recall:.4f} F1={micro_f1:.4f}")
    print(f"  MACRO:  P={macro_precision:.4f} R={macro_recall:.4f} F1={macro_f1:.4f}")
    print()
    for m in all_metrics:
        print(f"  {m['service']:24s}  TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']}  "
              f"P={m['precision']:.4f} R={m['recall']:.4f} F1={m['f1']:.4f}")
    print("=" * 72)
    print(f"  Report saved: {args.output}")


if __name__ == "__main__":
    main()