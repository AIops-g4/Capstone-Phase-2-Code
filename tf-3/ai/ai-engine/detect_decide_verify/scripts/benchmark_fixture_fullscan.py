"""
Full-scan per-service detect benchmark on the benchmark_fixtures dataset.

Unlike benchmark_fixture_temporal.py (which stops at the FIRST alert per run),
this script SCANS THE ENTIRE run and treats EVERY detection as its own event.
This measures alert spam: each wrong detection counts as one separate FP.

Alert-level classification (per detection event):
  TP : a detection whose time falls INSIDE the ground-truth anomaly region
       (or within the tolerance band around the onset). We count at most one TP
       per contiguous ground-truth region so multiple hits on the same real
       anomaly are not double-rewarded; extra in-region hits are ignored.
  FP : any detection that does NOT fall in a ground-truth region
       (fired too early, on a clean run, or between/after regions) => spam.

Run-level miss:
  FN : a ground-truth anomaly region that received NO detection at all.

TN is not counted at the alert level (there is no natural "negative event");
accuracy is therefore omitted and the meaningful metrics are Precision / Recall / F1:
  Precision = TP / (TP + FP)
  Recall    = TP / (TP + FN)
  F1        = 2 * P * R / (P + R)

--------------------------------------------------------------------------------
How the full scan works
--------------------------------------------------------------------------------
The /v1/detect API reports only the FIRST anomaly in whatever window it is given
(the engine breaks on the first flagged point). To enumerate every detection
across a run WITHOUT changing the server, we slide a fixed-size window across the
run and call detect once per window position. Each window that returns
anomaly_detected=True yields one detection event, timestamped at the detected
point (server-reported anomaly_time if available, else the window's last point).
De-duplication collapses detections that map to the same source timestamp.

Usage:
  python benchmark_fixture_fullscan.py
  python benchmark_fixture_fullscan.py --services adservice cartservice
  python benchmark_fixture_fullscan.py --run-sizes 100 500 3000
  python benchmark_fixture_fullscan.py --window 60 --stride 10
  python benchmark_fixture_fullscan.py --lead-tolerance 15 --late-tolerance 60
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

# CSV metric-type -> contract signal_name (see benchmark_fixture_temporal.py).
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

# Default tolerance expressed in POINTS (not seconds), so it is robust whether
# the dataset samples per-second or per-minute. A detection counts as TP if it
# lands within LEAD points before a region's start or LATE points after its end.
# The script also measures the median sampling interval (dt) per run and converts
# these point tolerances into a seconds-based band for the actual comparison.
DEFAULT_LEAD_POINTS = 2         # detection this many POINTS before region start still TP
DEFAULT_LATE_POINTS = 3         # detection this many POINTS after region end still TP

# Auto-scaling tolerance: instead of a fixed point count, derive tolerance from
# the size of each run so larger datasets get proportionally wider bands. The
# result is clamped so tiny runs stay strict and huge runs don't become absurd.
AUTO_LEAD_RATIO = 0.02         # lead tolerance = 2% of the run's point count
AUTO_LATE_RATIO = 0.03         # late tolerance = 3% of the run's point count
AUTO_LEAD_MIN = 1              # never fewer than 1 point
AUTO_LATE_MIN = 2
AUTO_LEAD_MAX = 60             # cap so 3000-point runs stay reasonable
AUTO_LATE_MAX = 90

# Default sliding-window geometry.
DEFAULT_WINDOW = 60             # points per detect call
DEFAULT_STRIDE = 10             # points advanced between successive calls


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
# Dataset loading + signal mapping
# ---------------------------------------------------------------------------

def _split_column(col: str) -> tuple[str, str]:
    if "_" not in col:
        return col, col
    service, suffix = col.rsplit("_", 1)
    return service, suffix


def _map_signal(metric_suffix: str) -> str:
    return METRIC_SUFFIX_TO_SIGNAL.get(metric_suffix.strip().lower(), DEFAULT_SIGNAL)


def _load_simple_metrics(run_dir: str) -> Optional[list[dict[str, Any]]]:
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


def _count_log_lines(run_dir: str) -> int:
    """Count log lines in the run directory if a log file is present.

    Looks for common log filenames; returns 0 if none found. simple_metrics.csv
    carries metrics only, so log volume must be read separately.
    """
    candidates = ["simple_logs.csv", "logs.csv", "log.csv", "logs.json", "logs.txt"]
    for name in candidates:
        path = os.path.join(run_dir, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                count = sum(1 for _ in f)
            # Subtract a header row for CSV files.
            if name.endswith(".csv") and count > 0:
                count -= 1
            return max(0, count)
        except Exception:
            return 0
    return 0


def _auto_tolerance_points(n: int) -> tuple[int, int]:
    """Derive (lead_points, late_points) from a run's point count n.

    Scales with dataset size and clamps to configured min/max so 100-point runs
    stay strict while 3000-point runs get a proportionally wider band.
    """
    import math
    lead = int(math.ceil(n * AUTO_LEAD_RATIO))
    late = int(math.ceil(n * AUTO_LATE_RATIO))
    lead = max(AUTO_LEAD_MIN, min(AUTO_LEAD_MAX, lead))
    late = max(AUTO_LATE_MIN, min(AUTO_LATE_MAX, late))
    return lead, late


def _fmt_duration(seconds: float) -> str:
    """Format a duration in seconds as a readable 'Xm Ys' (or 'Ys' when < 60s)."""
    total = int(round(seconds))
    m, s = divmod(total, 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _median_dt(rows: list[dict[str, Any]]) -> float:
    """Median time step between consecutive rows (seconds). Falls back to 1.0."""
    times = [float(r["time"]) for r in rows if r.get("time") not in (None, "")]
    if len(times) < 2:
        return 1.0
    times.sort()
    diffs = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not diffs:
        return 1.0
    diffs.sort()
    mid = len(diffs) // 2
    if len(diffs) % 2:
        return diffs[mid]
    return (diffs[mid - 1] + diffs[mid]) / 2.0


def _ground_truth_regions(rows: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Return contiguous (start_time, end_time) regions where is_anomaly is True."""
    regions: list[tuple[float, float]] = []
    start: Optional[float] = None
    prev_t: Optional[float] = None
    for r in rows:
        t = float(r["time"])
        if _is_anom_flag(r):
            if start is None:
                start = t
            prev_t = t
        else:
            if start is not None:
                regions.append((start, prev_t if prev_t is not None else start))
                start = None
                prev_t = None
    if start is not None:
        regions.append((start, prev_t if prev_t is not None else start))
    return regions


def _rows_to_telemetry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    evidence = response.get("llm_fault_rank_evidence") or {}
    summary = evidence.get("bocpd_input_summary") or {}
    t = summary.get("anomaly_time")
    return float(t) if isinstance(t, (int, float)) else None


# ---------------------------------------------------------------------------
# Full scan: enumerate every detection across a run
# ---------------------------------------------------------------------------

def _scan_detections(
    base_url: str,
    rows: list[dict[str, Any]],
    window: int,
    stride: int,
) -> tuple[list[float], int]:
    """
    Slide a fixed window across the run; each window that flags an anomaly
    contributes one detection event. Returns (sorted_unique_detection_times,
    num_calls).
    """
    n = len(rows)
    detection_times: set[float] = set()
    calls = 0

    if n == 0:
        return [], 0

    win = min(window, n)
    start = 0
    while start < n:
        end = min(start + win, n)
        window_rows = rows[start:end]
        telemetry = _rows_to_telemetry(window_rows)
        response = _detect(base_url, telemetry)
        calls += 1
        if response.get("anomaly_detected"):
            t = _server_reported_time(response)
            if t is None:
                t = float(window_rows[-1]["time"])
            detection_times.add(round(t, 3))
        if end >= n:
            break
        start += stride

    return sorted(detection_times), calls


# ---------------------------------------------------------------------------
# Alert-level classification
# ---------------------------------------------------------------------------

def _classify_detections(
    detection_times: list[float],
    regions: list[tuple[float, float]],
    lead_tolerance: float,
    late_tolerance: float,
) -> tuple[int, int, int, list[dict[str, Any]]]:
    """
    Classify every detection event as TP or FP, and count missed regions as FN.

    - EVERY detection inside [start - lead, end + late] of ANY region is a TP:
      each correct alert adds one TP (an alert that correctly fires during a real
      anomaly is rewarded every time).
    - Any detection matching no region is an FP (spam / too early).
    - Any region with zero hits is an FN.
    """
    region_hit = [False] * len(regions)
    tp = 0
    fp = 0
    events: list[dict[str, Any]] = []

    for dt in detection_times:
        matched_region = -1
        for idx, (start, end) in enumerate(regions):
            if (start - lead_tolerance) <= dt <= (end + late_tolerance):
                matched_region = idx
                break
        if matched_region == -1:
            fp += 1
            events.append({"detected_time": dt, "classification": "FP", "region": None})
        else:
            region_hit[matched_region] = True
            tp += 1
            events.append({"detected_time": dt, "classification": "TP", "region": matched_region})

    fn = sum(1 for hit in region_hit if not hit)
    return tp, fp, fn, events


# ---------------------------------------------------------------------------
# Per-service benchmark
# ---------------------------------------------------------------------------

def benchmark_service(
    base_url: str,
    service: str,
    run_sizes: list[str],
    window: int,
    stride: int,
    lead_points: float,
    late_points: float,
    auto_tolerance: bool,
) -> dict:
    per_run: list[dict[str, Any]] = []
    tp = fp = fn = errors = 0
    total_calls = 0

    for run_size in run_sizes:
        run_dir = os.path.join(BENCHMARK_FIXTURES_DIR, service, run_size)
        if not os.path.isdir(run_dir):
            print(f"  [SKIP] {service}/{run_size}: directory not found")
            continue

        rows = _load_simple_metrics(run_dir)
        if rows is None or not rows:
            print(f"  [SKIP] {service}/{run_size}: no simple_metrics.csv")
            continue

        # Resolve tolerance in points: auto-scale to this run's size unless the
        # user forced fixed values via --lead-points/--late-points.
        if auto_tolerance:
            run_lead_points, run_late_points = _auto_tolerance_points(len(rows))
        else:
            run_lead_points, run_late_points = lead_points, late_points

        # Convert point-based tolerance to seconds using this run's own dt.
        dt = _median_dt(rows)
        lead_tolerance = run_lead_points * dt
        late_tolerance = run_late_points * dt
        regions = _ground_truth_regions(rows)

        # Time window = max(time) - min(time). Used to report the observed span
        # and the density of metric points / log lines within it.
        times = [float(r["time"]) for r in rows if r.get("time") not in (None, "")]
        t_min = min(times) if times else 0.0
        t_max = max(times) if times else 0.0
        time_window = t_max - t_min
        num_metric_points = len(rows)
        # simple_metrics.csv holds metrics only; logs (if any) live alongside it.
        num_log_lines = _count_log_lines(run_dir)

        try:
            detection_times, calls = _scan_detections(base_url, rows, window, stride)
        except Exception as exc:
            print(f"  [ERROR] {service}/{run_size}: {exc}")
            per_run.append({
                "service": service,
                "run_size": run_size,
                "error": str(exc),
            })
            errors += 1
            continue

        total_calls += calls
        r_tp, r_fp, r_fn, events = _classify_detections(
            detection_times, regions, lead_tolerance, late_tolerance
        )
        tp += r_tp
        fp += r_fp
        fn += r_fn

        per_run.append({
            "service": service,
            "run_size": run_size,
            "dt_seconds": round(dt, 3),
            "dt_human": _fmt_duration(dt),
            "time_window_seconds": round(time_window, 3),
            "time_window_human": _fmt_duration(time_window),
            "time_min": t_min,
            "time_max": t_max,
            "num_metric_points": num_metric_points,
            "num_log_lines": num_log_lines,
            "lead_points": run_lead_points,
            "late_points": run_late_points,
            "lead_tolerance_seconds": round(lead_tolerance, 3),
            "late_tolerance_seconds": round(late_tolerance, 3),
            "gt_regions": [{"start": s, "end": e} for s, e in regions],
            "num_detections": len(detection_times),
            "detect_calls": calls,
            "tp": r_tp,
            "fp": r_fp,
            "fn": r_fn,
            "events": events,
        })

        print(
            f"  {service}/{run_size}: window={_fmt_duration(time_window)} dt={_fmt_duration(dt)} "
            f"metrics={num_metric_points} logs={num_log_lines} "

            f"tol=[-{run_lead_points}pt,+{run_late_points}pt]"
            f"=[-{_fmt_duration(lead_tolerance)},+{_fmt_duration(late_tolerance)}] "
            f"regions={len(regions)} detections={len(detection_times)} "
            f"TP={r_tp} FP={r_fp} FN={r_fn} (calls={calls})"
        )

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    metrics = {
        "service": service,
        "runs": len(per_run) - errors,
        "errors": errors,
        "detect_calls": total_calls,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
    return {"metrics": metrics, "per_run": per_run}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full-scan per-service detect benchmark (every wrong detect = 1 FP)"
    )
    parser.add_argument("--api-url", default=f"http://{API_HOST}:{API_PORT}")
    parser.add_argument("--run-sizes", nargs="+", default=RUN_SIZES,
                        help=f"Run sizes (default: {' '.join(RUN_SIZES)})")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help=f"Sliding window size in points (default: {DEFAULT_WINDOW})")
    parser.add_argument("--stride", type=int, default=DEFAULT_STRIDE,
                        help=f"Points advanced between windows (default: {DEFAULT_STRIDE})")
    parser.add_argument("--lead-points", type=float, default=DEFAULT_LEAD_POINTS,
                        help="POINTS before a region start still counted as TP "
                             "(converted to seconds via each run's median dt)")
    parser.add_argument("--late-points", type=float, default=DEFAULT_LATE_POINTS,
                        help="POINTS after a region end still counted as TP "
                             "(converted to seconds via each run's median dt)")
    parser.add_argument("--output", default=os.path.join(
        AI_ENGINE_ROOT, "dataset", "benchmark_reports",
        "benchmark_fixture_fullscan.json",
    ))
    args = parser.parse_args()

    services = args.services or SERVICES
    base_url = args.api_url

    print("=" * 72)
    print("  BENCHMARK FIXTURE — FULL SCAN (every wrong detect = 1 FP)")
    print("=" * 72)
    print(f"  API URL:         {base_url}")
    print(f"  Services:        {', '.join(services)}")
    print(f"  Run sizes:       {', '.join(args.run_sizes)}")
    print(f"  Window / stride: {args.window} / {args.stride}")
    print(f"  Tolerance:       lead={args.lead_points} pts  late={args.late_points} pts "
          f"(converted to seconds per-run via median dt)")
    print("=" * 72)

    start = time.perf_counter()
    results: list[dict] = []
    all_per_run: list[dict] = []

    for idx, service in enumerate(services, 1):
        print(f"\n[{idx}/{len(services)}] Benchmarking: {service}")
        result = benchmark_service(
            base_url, service, args.run_sizes,
            args.window, args.stride,
            args.lead_points, args.late_points,
        )
        results.append(result)
        all_per_run.extend(result["per_run"])

        m = result["metrics"]
        print(f"  --- {service} Summary ---")
        print(f"  TP={m['tp']}  FP={m['fp']}  FN={m['fn']}")
        print(f"  Precision={m['precision']:.4f}  Recall={m['recall']:.4f}  F1={m['f1']:.4f}")

    duration = time.perf_counter() - start

    total_fp = sum(m["fp"] for m in all_metrics)
    total_fn = sum(m["fn"] for m in all_metrics)
    total_calls = sum(m["detect_calls"] for m in all_metrics)
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
        "benchmark": "benchmark_fixture_fullscan",
        "api_url": base_url,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(duration, 2),
        "scan": {"window": args.window, "stride": args.stride},
        "tolerance": {"lead_points": args.lead_points, "late_points": args.late_points,
                      "note": "per-run seconds = points * median dt; see per_run.*_tolerance_seconds"},
        "total_detect_calls": total_calls,
        "total_errors": total_errors,
        "aggregate": {
            "micro": {
                "tp": total_tp, "fp": total_fp, "fn": total_fn,
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
    print("  FINAL SUMMARY (FULL SCAN)")
    print("=" * 72)
    print(f"  Detect calls: {total_calls}   Errors: {total_errors}   Duration: {duration:.2f}s")
    print(f"  MICRO:  TP={total_tp} FP={total_fp} FN={total_fn}  "
          f"P={micro_precision:.4f} R={micro_recall:.4f} F1={micro_f1:.4f}")
    print(f"  MACRO:  P={macro_precision:.4f} R={macro_recall:.4f} F1={macro_f1:.4f}")
    print()
    for m in all_metrics:
        print(f"  {m['service']:24s}  TP={m['tp']} FP={m['fp']} FN={m['fn']}  "
              f"P={m['precision']:.4f} R={m['recall']:.4f} F1={m['f1']:.4f}")
    print("=" * 72)
    print(f"  Report saved: {args.output}")


if __name__ == "__main__":
    main()