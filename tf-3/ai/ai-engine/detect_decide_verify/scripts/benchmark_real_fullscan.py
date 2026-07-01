"""
Full-scan detect benchmark on the REAL dataset (ai-engine/dataset/real_dataset).

Same scoring philosophy as benchmark_fixture_fullscan.py:
  - Slide a window across the whole run, treat EVERY detection as its own event.
  - Merge duplicate detections from overlapping windows into single events.
  - Auto-scale scan geometry (window/stride/merge) and tolerance to run size.

Classification per detection event (relative to ground-truth inject points):
  TP : detection inside [inject - lead, inject + late] of an inject event.
  FP : detection fired BEFORE inject - lead (too early / spam).
  FN : detection fired AFTER  inject + late (too late), plus any inject event
       that received no in-band detection at all.

Metrics:
  Precision = TP / (TP + FP)
  Recall    = TP / (TP + FN)
  F1        = 2 * P * R / (P + R)

--------------------------------------------------------------------------------
Why this is a separate script
--------------------------------------------------------------------------------
real_dataset differs from benchmark_fixtures:
  - Flat layout: one data-export/ folder, not <service>/<run_size>/ subdirs.
  - No is_anomaly column. Ground truth lives in ground_truth.json as discrete
    inject_time points (one moment per injected fault), not True/False rows.
  - Services are cdo-sample-api / notification-service; metric columns are
    <service>_{mem,cpu,delay,loss}. dt is ~30s (not 1s).

So we load metrics from data-export/simple_metrics.csv, read inject points from
data-export/ground_truth.json, treat each inject as a point-region
[inject_time, inject_time], and reuse the fixture scoring logic unchanged.

Usage:
  python benchmark_real_fullscan.py
  python benchmark_real_fullscan.py --data-dir /path/to/real_dataset/data-export
  python benchmark_real_fullscan.py --fixed-tolerance --lead-points 4 --late-points 8
  python benchmark_real_fullscan.py --fixed-window --window 60 --stride 30 --merge-gap 10
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

DEFAULT_DATA_DIR = os.path.join(DATASET_DIR, "real_dataset", "data-export")

# CSV metric-type -> contract signal_name (same mapping as the fixture bench).
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

# Fixed tolerance in POINTS (used only with --fixed-tolerance).
DEFAULT_LEAD_POINTS = 4
DEFAULT_LATE_POINTS = 8

# Auto-scaling tolerance (fraction of run point count), clamped.
AUTO_LEAD_RATIO = 0.05
AUTO_LATE_RATIO = 0.10
AUTO_LEAD_MIN = 2
AUTO_LATE_MIN = 3
AUTO_LEAD_MAX = 200
AUTO_LATE_MAX = 400

# Fixed scan geometry (used only with --fixed-window).
DEFAULT_WINDOW = 60
DEFAULT_STRIDE = 30
DEFAULT_MERGE_GAP = 10

# Auto scan geometry (fraction of run point count / window), clamped.
AUTO_WINDOW_RATIO = 0.20
AUTO_WINDOW_MIN = 40
AUTO_WINDOW_MAX = 300
AUTO_STRIDE_RATIO = 0.50
AUTO_STRIDE_MIN = 10
AUTO_MERGE_RATIO = 0.10
AUTO_MERGE_MIN = 5


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _iso(ts: float) -> str:
    return (
        datetime.fromtimestamp(float(ts), tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


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
# Geometry / tolerance helpers
# ---------------------------------------------------------------------------


def _auto_tolerance_points(n: int) -> tuple[int, int]:
    import math

    lead = int(math.ceil(n * AUTO_LEAD_RATIO))
    late = int(math.ceil(n * AUTO_LATE_RATIO))
    lead = max(AUTO_LEAD_MIN, min(AUTO_LEAD_MAX, lead))
    late = max(AUTO_LATE_MIN, min(AUTO_LATE_MAX, late))
    return lead, late


def _auto_scan_geometry(n: int) -> tuple[int, int, int]:
    window = int(round(n * AUTO_WINDOW_RATIO))
    window = max(AUTO_WINDOW_MIN, min(AUTO_WINDOW_MAX, window))
    window = min(window, n)
    stride = max(AUTO_STRIDE_MIN, int(round(window * AUTO_STRIDE_RATIO)))
    merge_gap = max(AUTO_MERGE_MIN, int(round(window * AUTO_MERGE_RATIO)))
    return window, stride, merge_gap


def _fmt_duration(seconds: float) -> str:
    total = int(round(seconds))
    m, s = divmod(total, 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _median_dt(rows: list[dict[str, Any]]) -> float:
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


def _merge_detections(
    detection_times: list[float], merge_gap_seconds: float
) -> list[float]:
    if merge_gap_seconds <= 0 or not detection_times:
        return sorted(set(detection_times))
    ordered = sorted(set(detection_times))
    clusters: list[float] = [ordered[0]]
    last = ordered[0]
    for t in ordered[1:]:
        if t - last <= merge_gap_seconds:
            last = t
        else:
            clusters.append(t)
            last = t
    return clusters


# ---------------------------------------------------------------------------
# Dataset loading (real_dataset)
# ---------------------------------------------------------------------------


def _split_column(col: str) -> tuple[str, str]:
    if "_" not in col:
        return col, col
    service, suffix = col.rsplit("_", 1)
    return service, suffix


def _map_signal(metric_suffix: str) -> str:
    return METRIC_SUFFIX_TO_SIGNAL.get(metric_suffix.strip().lower(), DEFAULT_SIGNAL)


def _load_metrics(data_dir: str) -> list[dict[str, Any]]:
    """Load simple_metrics.csv rows (sorted by time). Empty cells -> skipped."""
    path = os.path.join(data_dir, "simple_metrics.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"simple_metrics.csv not found in {data_dir}")
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cleaned: dict[str, Any] = {}
            for k, v in row.items():
                k = (k or "").strip()
                v = v.strip() if v else ""
                if k == "time":
                    cleaned[k] = v
                elif v == "":
                    # Missing sample: leave the column out for this row.
                    continue
                else:
                    try:
                        cleaned[k] = float(v)
                    except ValueError:
                        continue
            if cleaned.get("time"):
                rows.append(cleaned)
    rows.sort(key=lambda r: float(r.get("time", 0)))
    return rows


def _load_ground_truth(data_dir: str) -> list[dict[str, Any]]:
    """Load inject events from ground_truth.json.

    Each value carries inject_time, target_service, suspected_fault_type.
    Returns a list of {inject_time, target_service, fault_type, key}.
    """
    for name in ("ground_truth.json", "ground_truth_ob.json"):
        path = os.path.join(data_dir, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            events = []
            for key, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("inject_time")
                if ts is None:
                    continue
                events.append(
                    {
                        "key": key,
                        "inject_time": float(ts),
                        "target_service": entry.get("target_service"),
                        "fault_type": entry.get("suspected_fault_type"),
                    }
                )
            events.sort(key=lambda e: e["inject_time"])
            return events
    raise FileNotFoundError(f"No ground_truth.json in {data_dir}")


def _rows_to_telemetry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    telemetry: list[dict[str, Any]] = []
    for row in rows:
        ts = _iso(float(row["time"]))
        for col, value in row.items():
            if col == "time":
                continue
            if not isinstance(value, (int, float)):
                continue
            service, metric_suffix = _split_column(str(col).strip())
            signal_name = _map_signal(metric_suffix)
            telemetry.append(
                {
                    "ts": ts,
                    "tenant_id": TENANT_ID,
                    "service": service,
                    "signal_name": signal_name,
                    "value": float(value),
                    "labels": {"system": "E-COMMERCE"},
                }
            )
    return telemetry


def _count_log_lines(data_dir: str) -> int:
    for name in ("online_boutique_logs.csv", "logs.csv", "simple_logs.csv"):
        path = os.path.join(data_dir, name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    count = sum(1 for _ in f)
                return max(0, count - 1)
            except Exception:
                return 0
    return 0


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
# Full scan + classification (identical logic to the fixture benchmark)
# ---------------------------------------------------------------------------


def _scan_detections(
    base_url: str, rows: list[dict[str, Any]], window: int, stride: int
) -> tuple[list[float], int]:
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


def _classify_detections(
    detection_times: list[float],
    regions: list[tuple[float, float]],
    lead_tolerance: float,
    late_tolerance: float,
) -> tuple[int, int, int, list[dict[str, Any]]]:
    """
    TP : detection inside [start - lead, end + late] of a region.
    FP : detection before start - lead (too early / spam).
    FN : detection after end + late (too late), plus regions with no TP hit.
    """
    region_hit = [False] * len(regions)
    tp = fp = fn = 0
    events: list[dict[str, Any]] = []

    for dt in detection_times:
        classification = None
        matched_region = None
        for idx, (start, end) in enumerate(regions):
            lower = start - lead_tolerance
            upper = end + late_tolerance
            if lower <= dt <= upper:
                classification = "TP"
                matched_region = idx
                break
            if dt < lower:
                classification = "FP"
                matched_region = idx
                break
        if classification is None:
            classification = "FN"

        if classification == "TP":
            tp += 1
            region_hit[matched_region] = True
        elif classification == "FP":
            fp += 1
        else:
            fn += 1
        events.append(
            {
                "detected_time": dt,
                "classification": classification,
                "region": matched_region,
            }
        )

    fn += sum(1 for hit in region_hit if not hit)
    return tp, fp, fn, events


# ---------------------------------------------------------------------------
# Benchmark one data-export directory
# ---------------------------------------------------------------------------


def benchmark_dataset(
    base_url: str,
    data_dir: str,
    lead_points: float,
    late_points: float,
    auto_tolerance: bool,
    window: int,
    stride: int,
    merge_gap_points: float,
    auto_window: bool,
) -> dict:
    rows = _load_metrics(data_dir)
    events = _load_ground_truth(data_dir)

    if not rows:
        raise RuntimeError(f"No metric rows loaded from {data_dir}")

    dt = _median_dt(rows)
    n = len(rows)

    # Each inject_time is a point-region [inject, inject].
    regions = [(e["inject_time"], e["inject_time"]) for e in events]

    # Resolve tolerance.
    if auto_tolerance:
        run_lead_points, run_late_points = _auto_tolerance_points(n)
    else:
        run_lead_points, run_late_points = lead_points, late_points
    lead_tolerance = run_lead_points * dt
    late_tolerance = run_late_points * dt

    # Resolve scan geometry.
    if auto_window:
        run_window, run_stride, run_merge_gap = _auto_scan_geometry(n)
    else:
        run_window, run_stride, run_merge_gap = window, stride, merge_gap_points

    times = [float(r["time"]) for r in rows]
    t_min, t_max = min(times), max(times)
    time_window = t_max - t_min
    num_log_lines = _count_log_lines(data_dir)

    raw_detection_times, calls = _scan_detections(
        base_url, rows, run_window, run_stride
    )
    merge_gap_seconds = run_merge_gap * dt
    detection_times = _merge_detections(raw_detection_times, merge_gap_seconds)

    tp, fp, fn, det_events = _classify_detections(
        detection_times, regions, lead_tolerance, late_tolerance
    )

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    print(
        f"  span={_fmt_duration(time_window)} dt={_fmt_duration(dt)} "
        f"metrics={n} logs={num_log_lines} "
        f"win/str/mrg={run_window}/{run_stride}/{run_merge_gap}pt "
        f"tol=[-{run_lead_points}pt,+{run_late_points}pt]"
        f"=[-{_fmt_duration(lead_tolerance)},+{_fmt_duration(late_tolerance)}] "
        f"injects={len(regions)} detections={len(detection_times)}(raw={len(raw_detection_times)}) "
        f"TP={tp} FP={fp} FN={fn} (calls={calls})"
    )

    return {
        "data_dir": data_dir,
        "dt_seconds": round(dt, 3),
        "time_window_seconds": round(time_window, 3),
        "time_window_human": _fmt_duration(time_window),
        "num_metric_points": n,
        "num_log_lines": num_log_lines,
        "window_points": run_window,
        "stride_points": run_stride,
        "merge_gap_points": run_merge_gap,
        "lead_points": run_lead_points,
        "late_points": run_late_points,
        "lead_tolerance_seconds": round(lead_tolerance, 3),
        "late_tolerance_seconds": round(late_tolerance, 3),
        "inject_events": events,
        "num_detections_raw": len(raw_detection_times),
        "num_detections": len(detection_times),
        "detect_calls": calls,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "events": det_events,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full-scan detect benchmark on the real dataset"
    )
    parser.add_argument("--api-url", default=f"http://{API_HOST}:{API_PORT}")
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Path to the data-export dir (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--lead-points",
        type=float,
        default=DEFAULT_LEAD_POINTS,
        help="Fixed POINTS before inject counted as TP (with --fixed-tolerance)",
    )
    parser.add_argument(
        "--late-points",
        type=float,
        default=DEFAULT_LATE_POINTS,
        help="Fixed POINTS after inject counted as TP (with --fixed-tolerance)",
    )
    parser.add_argument(
        "--fixed-tolerance",
        action="store_true",
        help="Use fixed lead/late points instead of auto-scaling to run size",
    )
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--stride", type=int, default=DEFAULT_STRIDE)
    parser.add_argument(
        "--merge-gap",
        type=float,
        default=DEFAULT_MERGE_GAP,
        help="Merge detections within this many POINTS (0 disables)",
    )
    parser.add_argument(
        "--fixed-window",
        action="store_true",
        help="Use fixed window/stride/merge instead of auto-scaling to run size",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(
            AI_ENGINE_ROOT,
            "dataset",
            "benchmark_reports",
            "benchmark_real_fullscan.json",
        ),
    )
    args = parser.parse_args()

    base_url = args.api_url
    auto_tolerance = not args.fixed_tolerance
    auto_window = not args.fixed_window

    print("=" * 72)
    print("  BENCHMARK REAL DATASET — FULL SCAN (every wrong detect = 1 FP)")
    print("=" * 72)
    print(f"  API URL:   {base_url}")
    print(f"  Data dir:  {args.data_dir}")
    if auto_window:
        print(
            f"  Scan geo:  AUTO — window={AUTO_WINDOW_RATIO:.0%} of run "
            f"(clamp {AUTO_WINDOW_MIN}-{AUTO_WINDOW_MAX}), "
            f"stride={AUTO_STRIDE_RATIO:.0%} of window, merge={AUTO_MERGE_RATIO:.0%} of window"
        )
    else:
        print(
            f"  Scan geo:  FIXED — window={args.window} stride={args.stride} merge={args.merge_gap}"
        )
    if auto_tolerance:
        print(
            f"  Tolerance: AUTO — lead={AUTO_LEAD_RATIO:.0%} late={AUTO_LATE_RATIO:.0%} of run "
            f"(clamp lead[{AUTO_LEAD_MIN}-{AUTO_LEAD_MAX}] late[{AUTO_LATE_MIN}-{AUTO_LATE_MAX}])"
        )
    else:
        print(
            f"  Tolerance: FIXED — lead={args.lead_points} late={args.late_points} pts"
        )
    print("=" * 72)

    start = time.perf_counter()
    result = benchmark_dataset(
        base_url,
        args.data_dir,
        args.lead_points,
        args.late_points,
        auto_tolerance,
        args.window,
        args.stride,
        args.merge_gap,
        auto_window,
    )
    duration = time.perf_counter() - start

    report = {
        "benchmark": "benchmark_real_fullscan",
        "api_url": base_url,
        "data_dir": args.data_dir,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(duration, 2),
        "result": result,
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    print("\n" + "=" * 72)
    print("  RESULT (REAL DATASET)")
    print("=" * 72)
    print(f"  TP={result['tp']}  FP={result['fp']}  FN={result['fn']}")
    print(
        f"  Precision={result['precision']:.4f}  Recall={result['recall']:.4f}  F1={result['f1']:.4f}"
    )
    print(f"  Detect calls: {result['detect_calls']}   Duration: {duration:.2f}s")
    print("=" * 72)
    print(f"  Report saved: {args.output}")


if __name__ == "__main__":
    main()
