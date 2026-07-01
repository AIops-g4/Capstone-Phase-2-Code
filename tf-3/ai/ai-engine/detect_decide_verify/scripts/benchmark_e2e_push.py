"""
CDO push benchmark client for detect_decide_verify.

This script simulates the CDO side of the API contract:

1. Build contract-compliant telemetry_window from benchmark fixture data.
2. POST /v1/detect.
3. If anomaly_detected=true, POST /v1/decide with anomaly_context.
4. Simulate CDO action execution.
5. POST /v1/verify with post_telemetry_window.
6. Save an end-to-end report.

The script is a client simulator. It does not configure server request schema.
Server validation remains in src/server.py.
"""
from __future__ import annotations

import json
import math
import os
import sys
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DETECT_DECIDE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, DETECT_DECIDE_DIR)

from src.config import (  # noqa: E402
    API_HOST,
    API_PORT,
    EVAL_BOCPD_WINDOW_AFTER,
    EVAL_BOCPD_WINDOW_BEFORE,
    GROUND_TRUTH_PATH,
    PLATFORM_PROFILE,
)

# Mapping from benchmark fault-type suffix to contract-compliant signal_name.
CONTRACT_SIGNAL_MAP: dict[str, str] = {
    "cpu": "container_resource_usage",
    "mem": "container_resource_usage",
    "delay": "service_latency_p95",
    "loss": "service_error_rate",
    "socket": "db_connection_pool_saturation",
    "disk": "container_resource_usage",
}

DEFAULT_RUN_KEYS = ["checkoutservice_cpu_1", "checkoutservice_mem_1"]
DEFAULT_OUTPUT = os.path.normpath(
    os.path.join(
        os.path.dirname(DETECT_DECIDE_DIR),
        "dataset",
        "benchmark_reports",
        "benchmark_e2e_push.json",
    )
)


def _iso(ts: int | float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _csv_env(name: str, default_values: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default_values
    return [item.strip() for item in raw.split(",") if item.strip()]


def _api_url() -> str:
    return os.getenv("CDO_PUSH_API_URL", f"http://{API_HOST}:{API_PORT}").rstrip("/")


def _tenant_id() -> str:
    return os.getenv("CDO_PUSH_TENANT_ID", "d3b07384-d113-495f-9f58-20d18d357d75")


def _authorization() -> str:
    return os.getenv("CDO_PUSH_AUTHORIZATION", "Bearer benchmark-token")


def _dry_run_mode() -> bool:
    return os.getenv("CDO_PUSH_DRY_RUN_MODE", "true").strip().lower() == "true"


def _output_path() -> str:
    env_val = os.getenv("CDO_PUSH_OUTPUT_PATH", "").strip()
    if not env_val:
        return DEFAULT_OUTPUT
    if os.path.isabs(env_val):
        return env_val
    return os.path.normpath(os.path.join(DETECT_DECIDE_DIR, env_val))


def _request_ids() -> tuple[str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4())


def _request_headers(
    tenant_id: str,
    correlation_id: str,
    idempotency_key: str,
    dry_run: bool,
    authorization: str,
) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Tenant-Id": tenant_id,
        "Authorization": authorization,
        "X-Correlation-Id": correlation_id,
        "Idempotency-Key": idempotency_key,
        "X-Dry-Run-Mode": str(dry_run).lower(),
    }


def _post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float = 120.0,
) -> dict[str, Any]:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {path} HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot reach API server at {base_url}. "
            f"Start it with: cd ai-engine/detect_decide_verify && "
            f"python -m uvicorn src.server:app --host {API_HOST} --port {API_PORT}"
        ) from exc


def _load_ground_truth() -> dict[str, Any]:
    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _fault_suffix(service_fault: str) -> str:
    return str(service_fault).rsplit("_", 1)[-1]


def _contract_signal_name(service_fault: str) -> str:
    return CONTRACT_SIGNAL_MAP.get(_fault_suffix(service_fault), "service_error_rate")


def _contract_metric_value(service_fault: str, raw: float) -> float:
    suffix = _fault_suffix(service_fault)
    if suffix in {"cpu", "mem", "disk"}:
        return max(float(raw), 1.0) * 1024 * 1024
    if suffix == "delay":
        return max(float(raw), 1.0) * 1000.0
    if suffix in {"loss", "socket"}:
        return max(0.0, min(float(raw), 1.0))
    return float(raw)


def _run_dir(run_info: dict[str, Any]) -> str:
    return os.path.join(
        os.path.dirname(GROUND_TRUTH_PATH),
        str(run_info["service_fault"]),
        str(run_info["run_id"]),
    )


def _load_metric_slice(run_info: dict[str, Any], post_window: bool = False) -> pd.DataFrame:
    metrics_path = os.path.join(_run_dir(run_info), "simple_metrics.csv")
    df = pd.read_csv(metrics_path).sort_values("time").reset_index(drop=True)

    inject_time = int(run_info["inject_time"])
    if post_window:
        start_ts = inject_time + 1
        end_ts = inject_time + EVAL_BOCPD_WINDOW_AFTER
    else:
        start_ts = inject_time - EVAL_BOCPD_WINDOW_BEFORE
        end_ts = inject_time + EVAL_BOCPD_WINDOW_AFTER

    df = df[(df["time"] >= start_ts) & (df["time"] <= end_ts)].reset_index(drop=True)
    if df.empty:
        raise RuntimeError(
            f"No metric rows for {run_info['service_fault']}/{run_info['run_id']} "
            f"in window {start_ts}..{end_ts}"
        )
    return df


def _build_telemetry_window(
    run_info: dict[str, Any],
    tenant_id: str,
    post_window: bool = False,
) -> list[dict[str, Any]]:
    service_fault = str(run_info["service_fault"])
    target_service = str(run_info["target_service"])
    system_name = str(PLATFORM_PROFILE.get("system", "E-COMMERCE"))
    namespace = str(PLATFORM_PROFILE.get("default_namespace", "production"))
    deployment = target_service

    df = _load_metric_slice(run_info, post_window=post_window)
    signal_name = _contract_signal_name(service_fault)
    service_cols = [
        c for c in df.columns
        if c != "time" and str(c).startswith(f"{target_service}_")
    ]

    telemetry: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        raw = float(row[service_cols[0]]) if service_cols else 0.0
        value = _contract_metric_value(service_fault, raw)
        if math.isnan(value) or math.isinf(value):
            continue
        telemetry.append(
            {
                "ts": _iso(int(row["time"])),
                "tenant_id": tenant_id,
                "service": target_service,
                "signal_name": signal_name,
                "value": value,
                "labels": {
                    "system": system_name,
                    "namespace": namespace,
                    "deployment": deployment,
                },
            }
        )

    if not post_window:
        logs_path = os.path.join(_run_dir(run_info), "logs.csv")
        if os.path.exists(logs_path):
            inject_time = int(run_info["inject_time"])
            start_ts = inject_time - EVAL_BOCPD_WINDOW_BEFORE
            end_ts = inject_time + EVAL_BOCPD_WINDOW_AFTER
            df_logs = pd.read_csv(logs_path)
            if "timestamp" in df_logs.columns:
                df_logs["_ts_sec"] = (df_logs["timestamp"] // 1_000_000_000).astype(int)
                df_logs = df_logs[
                    (df_logs["_ts_sec"] >= start_ts) & (df_logs["_ts_sec"] <= end_ts)
                ]
            for _, lrow in df_logs.head(10).iterrows():
                ts_sec = int(lrow.get("_ts_sec", start_ts))
                msg = str(lrow.get("message", ""))[:500]
                level = str(lrow.get("level", "ERROR")).upper()
                telemetry.append(
                    {
                        "ts": _iso(ts_sec),
                        "tenant_id": tenant_id,
                        "service": target_service,
                        "signal_name": "application_log_event",
                        "value": msg,
                        "labels": {
                            "system": system_name,
                            "namespace": namespace,
                            "deployment": deployment,
                            "level": level,
                        },
                    }
                )

    return telemetry


def _simulate_action_executed(decide_response: dict[str, Any]) -> dict[str, Any]:
    action_plan = decide_response.get("action_plan") or []
    if not action_plan:
        return {
            "action": "RESTART_DEPLOYMENT",
            "target": "deployment/unknown",
            "status": "COMPLETED",
            "execution_time_seconds": 1,
        }

    first_step = action_plan[0]
    return {
        "action": str(first_step.get("action", "RESTART_DEPLOYMENT")),
        "target": str(first_step.get("target", "deployment/unknown")),
        "status": "COMPLETED",
        "execution_time_seconds": 1,
    }


def _call_detect(
    base_url: str,
    run_info: dict[str, Any],
    tenant_id: str,
    authorization: str,
    dry_run: bool,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    correlation_id, idempotency_key = _request_ids()
    telemetry_window = _build_telemetry_window(run_info, tenant_id, post_window=False)
    headers = _request_headers(tenant_id, correlation_id, idempotency_key, dry_run, authorization)
    payload = {
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "dry_run_mode": dry_run,
        "telemetry_window": telemetry_window,
    }
    return _post_json(base_url, "/v1/detect", payload, headers), payload, telemetry_window


def _call_decide(
    base_url: str,
    detect_response: dict[str, Any],
    detect_payload: dict[str, Any],
    tenant_id: str,
    authorization: str,
    dry_run: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    correlation_id = str(detect_response.get("correlation_id") or detect_payload["correlation_id"])
    idempotency_key = str(uuid.uuid4())
    headers = _request_headers(tenant_id, correlation_id, idempotency_key, dry_run, authorization)
    payload = {
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "dry_run_mode": dry_run,
        "anomaly_context": detect_response["anomaly_context"],
    }
    return _post_json(base_url, "/v1/decide", payload, headers), payload


def _call_verify(
    base_url: str,
    run_info: dict[str, Any],
    decide_response: dict[str, Any],
    detect_response: dict[str, Any],
    tenant_id: str,
    authorization: str,
    dry_run: bool,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    correlation_id = str(detect_response["correlation_id"])
    idempotency_key = str(uuid.uuid4())
    post_telemetry_window = _build_telemetry_window(run_info, tenant_id, post_window=True)
    action_executed = _simulate_action_executed(decide_response)
    headers = _request_headers(tenant_id, correlation_id, idempotency_key, dry_run, authorization)
    payload = {
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "dry_run_mode": dry_run,
        "action_executed": action_executed,
        "post_telemetry_window": post_telemetry_window,
    }
    return _post_json(base_url, "/v1/verify", payload, headers), payload, post_telemetry_window


def _run_one(
    base_url: str,
    run_key: str,
    run_info: dict[str, Any],
    tenant_id: str,
    authorization: str,
    dry_run: bool,
) -> dict[str, Any]:
    detect_resp, detect_payload, telemetry_window = _call_detect(
        base_url,
        run_info,
        tenant_id,
        authorization,
        dry_run,
    )

    row: dict[str, Any] = {
        "run_key": run_key,
        "service_fault": run_info["service_fault"],
        "run_id": run_info["run_id"],
        "target_service": run_info.get("target_service"),
        "detect_request_summary": {
            "correlation_id": detect_payload["correlation_id"],
            "idempotency_key": detect_payload["idempotency_key"],
            "dry_run_mode": detect_payload["dry_run_mode"],
            "telemetry_window_size": len(telemetry_window),
            "signal_names": sorted({p["signal_name"] for p in telemetry_window}),
        },
        "detect_response": detect_resp,
    }

    if not detect_resp.get("anomaly_detected") or not detect_resp.get("anomaly_context"):
        row["loop_completed"] = False
        row["skip_reason"] = "detect_response did not contain anomaly_context"
        return row

    decide_resp, decide_payload = _call_decide(
        base_url,
        detect_resp,
        detect_payload,
        tenant_id,
        authorization,
        dry_run,
    )
    row["decide_request_summary"] = {
        "correlation_id": decide_payload["correlation_id"],
        "idempotency_key": decide_payload["idempotency_key"],
        "dry_run_mode": decide_payload["dry_run_mode"],
        "anomaly_context": decide_payload["anomaly_context"],
    }
    row["decide_response"] = decide_resp

    verify_resp, verify_payload, post_telemetry_window = _call_verify(
        base_url,
        run_info,
        decide_resp,
        detect_resp,
        tenant_id,
        authorization,
        dry_run,
    )
    row["verify_request_summary"] = {
        "correlation_id": verify_payload["correlation_id"],
        "idempotency_key": verify_payload["idempotency_key"],
        "dry_run_mode": verify_payload["dry_run_mode"],
        "action_executed": verify_payload["action_executed"],
        "post_telemetry_window_size": len(post_telemetry_window),
        "signal_names": sorted({p["signal_name"] for p in post_telemetry_window}),
    }
    row["verify_response"] = verify_resp
    row["loop_completed"] = True
    return row


def main() -> None:
    run_keys = _csv_env("CDO_PUSH_SAMPLE_RUN_KEYS", DEFAULT_RUN_KEYS)
    gt = _load_ground_truth()
    base_url = _api_url()
    tenant_id = _tenant_id()
    authorization = _authorization()
    dry_run = _dry_run_mode()
    output = _output_path()

    print(f"[CDO_PUSH] API: {base_url}  tenant: {tenant_id}  dry_run: {dry_run}")
    print(f"[CDO_PUSH] Run keys: {run_keys}")

    rows: list[dict[str, Any]] = []
    for run_key in run_keys:
        if run_key not in gt:
            raise RuntimeError(f"run_key not found in ground_truth.json: {run_key}")

        row = _run_one(base_url, run_key, gt[run_key], tenant_id, authorization, dry_run)
        rows.append(row)

        detect = row.get("detect_response", {})
        decide = row.get("decide_response", {})
        verify = row.get("verify_response", {})
        print(
            f"[CDO_PUSH] {run_key}: "
            f"detect={detect.get('anomaly_detected')} "
            f"service={detect.get('anomaly_context', {}).get('target_service')} "
            f"runbook={decide.get('matched_runbook')} "
            f"verify_next={verify.get('next_action')} "
            f"completed={row.get('loop_completed')}"
        )

    report = {
        "benchmark": "detect_decide_verify_cdo_push_full_api_loop",
        "api_url": base_url,
        "samples_requested": len(run_keys),
        "samples_completed": len(rows),
        "per_run": rows,
    }

    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"[CDO_PUSH] Report saved: {output}")


if __name__ == "__main__":
    main()