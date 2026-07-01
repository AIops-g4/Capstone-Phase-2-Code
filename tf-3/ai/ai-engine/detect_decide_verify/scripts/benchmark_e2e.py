"""
CDO-style E2E benchmark client for detect_decide_verify.

This script acts like an external CDO/orchestrator:
  1. Loads benchmark telemetry input from dataset.
  2. Calls AI Engine API /v1/detect.
  3. Calls /v1/decide for action plans.
  4. Simulates executor + post-heal telemetry outside the AI Engine.
  5. Calls /v1/verify.
  6. If verification fails, calls /v1/fault-rank and /v1/decide again to retry
     fault types on the same service, then moves through service_top_k.

The benchmark no longer calls an in-process benchmark endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DETECT_DECIDE_DIR = os.path.dirname(SCRIPT_DIR)
AI_ENGINE_ROOT = os.path.dirname(DETECT_DECIDE_DIR)
sys.path.insert(0, DETECT_DECIDE_DIR)

from src.config import API_HOST, API_PORT, DATASET_DIR, FAULT_TYPE_CATALOG, GROUND_TRUTH_PATH

FAULT_TYPE_CANDIDATES = list(FAULT_TYPE_CATALOG)
TENANT_ID = "benchmark-cdo"


def _sample_run_keys(ground_truth: dict, sample_size: int | None) -> list[str]:
    keys = sorted(ground_truth)
    if not sample_size or sample_size >= len(keys):
        return keys
    np.random.seed(42)
    return sorted(np.random.choice(keys, sample_size, replace=False).tolist())


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


def _post_json(base_url: str, path: str, payload: dict, correlation_id: str, idempotency_key: str, timeout: float = 900.0) -> dict:
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
        raise RuntimeError(f"Cannot connect to API server at {base_url}. Start it with: python -m src.server") from exc


def _load_telemetry_window(run_dir: str) -> list[dict[str, Any]]:
    metrics_path = os.path.join(run_dir, "simple_metrics.csv")
    logs_path = os.path.join(run_dir, "logs.csv")
    df_metrics = pd.read_csv(metrics_path).sort_values("time").reset_index(drop=True)
    telemetry: list[dict[str, Any]] = []

    for _, row in df_metrics.iterrows():
        ts = _iso(row["time"])
        for col, value in row.items():
            if col == "time" or pd.isna(value):
                continue
            service = str(col).split("_", 1)[0]
            telemetry.append(
                {
                    "ts": ts,
                    "tenant_id": TENANT_ID,
                    "service": service,
                    "signal_name": str(col),
                    "value": float(value),
                    "labels": {},
                }
            )

    if os.path.exists(logs_path):
        df_logs = pd.read_csv(logs_path)
        for _, row in df_logs.iterrows():
            raw_ts = row.get("timestamp", df_metrics["time"].iloc[0] * 1_000_000_000)
            ts_sec = int(raw_ts // 1_000_000_000) if raw_ts > 10_000_000_000 else int(raw_ts)
            telemetry.append(
                {
                    "ts": _iso(ts_sec),
                    "tenant_id": TENANT_ID,
                    "service": str(row.get("container_name", "unknown")),
                    "signal_name": "application_log_event",
                    "value": str(row.get("message", "")),
                    "labels": {"level": str(row.get("level", "info"))},
                }
            )
    return telemetry


def _simulate_post_telemetry(target_service: str, true_service: str, true_fault: str, attempted_fault: str) -> list[dict[str, Any]]:
    now = _iso(time.time())
    def point(service: str, signal: str, value: float) -> dict[str, Any]:
        return {"ts": now, "tenant_id": TENANT_ID, "service": service, "signal_name": signal, "value": value, "labels": {}}

    if target_service == true_service and attempted_fault == true_fault:
        return [point(target_service, "service_error_rate", 0.0), point(target_service, "service_latency_p95", 0.03)]
    if target_service == true_service:
        return [point(target_service, "service_error_rate", 1.0), point(target_service, "service_latency_p95", 999.0)]
    return [
        point(target_service, "service_error_rate", 0.0),
        point(target_service, "service_latency_p95", 0.03),
        point(true_service, "service_error_rate", 1.0),
    ]


def _executed_context(decision: dict, fallback_context: dict) -> dict:
    corrected = decision.get("corrected_anomaly_context")
    if isinstance(corrected, dict) and corrected.get("target_service"):
        ctx = dict(fallback_context)
        ctx.update(corrected)
        ctx.setdefault("deployment", f"deployment/{ctx['target_service']}")
        return ctx
    return dict(fallback_context)


def _call_decide(base_url: str, correlation_id: str, context: dict, evidence: dict) -> tuple[dict, dict]:
    idem = str(uuid.uuid4())
    payload = {"correlation_id": correlation_id, "idempotency_key": idem, "dry_run_mode": True, "anomaly_context": context, "detect_evidence": evidence}
    decision = _post_json(base_url, "/v1/decide", payload, correlation_id, idem)
    return decision, _executed_context(decision, context)


def _fault_order_from_decision(decision: dict, first_fault: str) -> list[str]:
    ranked = [
        item.get("suspected_fault_type")
        for item in decision.get("fault_type_ranking", [])
        if item.get("suspected_fault_type")
    ]
    order = [first_fault]
    order += [fault for fault in ranked if fault not in order]
    order += [fault for fault in FAULT_TYPE_CANDIDATES if fault not in order]
    return order


def _call_verify(base_url: str, correlation_id: str, decision: dict, context: dict, true_service: str, true_fault: str) -> dict:
    first = (decision.get("action_plan") or [None])[0]
    if not first:
        return {"success": False, "regression_detected": False, "next_action": "ESCALATE", "escalation_bundle": {"reason": "no action"}}
    target_service = first["target"].split("/")[-1]
    attempted_fault = context.get("suspected_fault_type", "unknown")
    idem = str(uuid.uuid4())
    payload = {
        "correlation_id": correlation_id,
        "idempotency_key": idem,
        "dry_run_mode": True,
        "action_executed": {"action": first["action"], "target": first["target"], "status": "COMPLETED", "execution_time_seconds": 45},
        "post_telemetry_window": _simulate_post_telemetry(target_service, true_service, true_fault, attempted_fault),
    }
    return _post_json(base_url, "/v1/verify", payload, correlation_id, idem)


def _call_fault_rank(base_url: str, correlation_id: str, context: dict, evidence: dict) -> list[str]:
    idem = str(uuid.uuid4())
    payload = {"correlation_id": correlation_id, "idempotency_key": idem, "dry_run_mode": True, "anomaly_context": context, "detect_evidence": evidence}
    result = _post_json(base_url, "/v1/fault-rank", payload, correlation_id, idem)
    ranked = [item.get("suspected_fault_type") for item in result.get("fault_type_ranking", []) if item.get("suspected_fault_type")]
    return ranked + [fault for fault in FAULT_TYPE_CANDIDATES if fault not in ranked]


def _run_one_cdo_flow(base_url: str, run_key: str, gt: dict, top_k: int) -> dict:
    true_service = gt["target_service"]
    true_fault = gt["suspected_fault_type"]
    expected_runbook = gt["matched_runbook"]
    run_dir = os.path.join(DATASET_DIR, gt["service_fault"], gt["run_id"])
    correlation_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())

    detect_payload = {
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "dry_run_mode": True,
        "telemetry_source": {
            "kind": "benchmark_fixture",
            "service_fault": gt["service_fault"],
            "run_id": gt["run_id"],
            "inject_time": gt.get("inject_time"),
            "tenant_id": TENANT_ID,
        },
    }
    detect = _post_json(base_url, "/v1/detect", detect_payload, correlation_id, idempotency_key)

    row = {"run_key": run_key, "true_service": true_service, "true_fault": true_fault, "expected_runbook": expected_runbook, "detected": detect.get("anomaly_detected", False)}
    if not row["detected"]:
        row.update({"selected_prediction_correct": False, "selected_failure_reason": "not_detected"})
        return row

    base_context = detect["anomaly_context"]
    pred_service = base_context.get("target_service")
    pred_fault = base_context.get("suspected_fault_type")
    services = (detect.get("service_top_k") or [pred_service])[:top_k]
    if pred_service not in services:
        services.insert(0, pred_service)

    attempts = []
    final_decision = {}
    final_context = dict(base_context)
    final_verify = {"success": False, "next_action": "ESCALATE", "regression_detected": False}

    detect_evidence = {
        "detect_reasoning": detect.get("reasoning"),
        "detect_confidence": detect.get("confidence"),
        "service_top_k": services,
        **(detect.get("llm_fault_rank_evidence") or {}),
    }
    first_fault = pred_fault

    for service in services:
        service_context = dict(base_context)
        service_context["target_service"] = service
        service_context["deployment"] = f"deployment/{service}"
        initial_ctx = dict(service_context)
        initial_ctx["suspected_fault_type"] = first_fault
        initial_decision, initial_exec_ctx = _call_decide(
            base_url,
            correlation_id,
            initial_ctx,
            {
                **detect_evidence,
                "failed_self_heal_attempts": attempts,
                "rank_fault_catalog_for_topk_service": True,
                "topk_service_rank": services.index(service) + 1,
                "instruction": "Rank fault catalog only for this selected Top-K service candidate.",
            },
        )
        fault_order = _fault_order_from_decision(initial_decision, first_fault)

        for fault in fault_order:
            ctx = dict(service_context)
            ctx["suspected_fault_type"] = fault
            if fault == first_fault:
                decision, exec_ctx = initial_decision, initial_exec_ctx
            else:
                decision, exec_ctx = _call_decide(
                    base_url,
                    correlation_id,
                    ctx,
                    {
                        **detect_evidence,
                        "failed_self_heal_attempts": attempts,
                        "rank_fault_catalog_for_topk_service": False,
                    },
                )
            verify = _call_verify(base_url, correlation_id, decision, exec_ctx, true_service, true_fault)
            attempt = {
                "phase": "cdo_api_self_heal",
                "target_service": exec_ctx.get("target_service"),
                "suspected_fault_type": exec_ctx.get("suspected_fault_type"),
                "matched_runbook": decision.get("matched_runbook"),
                "next_action": verify.get("next_action"),
                "success": verify.get("success") and verify.get("next_action") == "DONE",
            }
            attempts.append(attempt)
            final_decision, final_context, final_verify = decision, exec_ctx, verify
            if attempt["success"]:
                break
        if final_verify.get("success") and final_verify.get("next_action") == "DONE":
            break

    final_service = final_context.get("target_service")
    final_fault = final_context.get("suspected_fault_type")
    pred_runbook = final_decision.get("matched_runbook")
    final_service_ok = final_service == true_service
    final_fault_ok = final_service_ok and final_fault == true_fault
    runbook_ok = final_service_ok and pred_runbook == expected_runbook
    selected_ok = final_service_ok and final_fault_ok and runbook_ok and final_verify.get("success")
    reasons = []
    if not final_service_ok:
        reasons.append("service_wrong")
    if final_service_ok and not final_fault_ok:
        reasons.append("fault_type_wrong")
    if final_service_ok and final_fault_ok and not runbook_ok:
        reasons.append("runbook_wrong")
    if not final_verify.get("success"):
        reasons.append("verify_failed")

    row.update({
        "pred_service": pred_service,
        "pred_fault": pred_fault,
        "top_k_candidates": services,
        "service_correct": pred_service == true_service,
        "service_in_top_k": true_service in services,
        "fault_correct": pred_service == true_service and pred_fault == true_fault,
        "final_service": final_service,
        "final_fault": final_fault,
        "selected_predicted_service": final_service,
        "selected_predicted_fault": final_fault,
        "selected_predicted_runbook": pred_runbook,
        "final_service_correct": final_service_ok,
        "final_fault_correct": final_fault_ok,
        "runbook_correct_e2e": runbook_ok,
        "selected_prediction_correct": bool(selected_ok),
        "selected_failure_reason": "+".join(reasons) if reasons else "none",
        "verify_success": bool(final_verify.get("success") and final_verify.get("next_action") == "DONE"),
        "verify_next_action": final_verify.get("next_action"),
        "verify_regression_detected": final_verify.get("regression_detected", False),
        "fallback_used": len(attempts) > 1,
        "fallback_reason": "cdo_api_retry_loop" if len(attempts) > 1 else "initial_attempt",
        "self_heal_attempts": attempts,
        "detect_assessment": final_decision.get("detect_assessment"),
        "corrected_anomaly_context": final_decision.get("corrected_anomaly_context"),
        "confidence": detect.get("confidence", 0.0),
    })
    return row


def _print_per_run(row: dict, idx: int, total: int) -> None:
    print(f"[{idx}/{total}] Run: {row.get('run_key')}")
    print(f"  [TRUE]      Service/Fault:     {row.get('true_service')} ({row.get('true_fault')})")
    print(f"  [TRUE]      Expected Runbook:  {row.get('expected_runbook')}")
    if not row.get("detected"):
        print("  [RESULT]    Anomaly Detection: FALSE NEGATIVE")
        print("  [CONCLUSION] Final selected result is WRONG\n")
        return
    print(f"  [DIAGNOSIS] Predicted Service: {row.get('pred_service')} [{'OK' if row.get('service_correct') else 'WRONG'}]")
    print(f"  [DIAGNOSIS] Predicted Fault:   {row.get('pred_fault')} [{'OK' if row.get('fault_correct') else 'WRONG'}]")
    print(f"  [DIAGNOSIS] Top-K Candidates:  {', '.join(row.get('top_k_candidates', []))} [{'OK' if row.get('service_in_top_k') else 'WRONG'}]")
    print(f"  [FINAL]     Healing Target:    {row.get('final_service')} ({row.get('final_fault')}) [svc={'OK' if row.get('final_service_correct') else 'WRONG'}, fault={'OK' if row.get('final_fault_correct') else 'WRONG'}]")
    if row.get("fallback_used"):
        print(f"  [FALLBACK]  Reason:            {row.get('fallback_reason')}")
    for attempt in row.get("self_heal_attempts", []):
        print(f"              - {attempt.get('phase')}: {attempt.get('target_service')} ({attempt.get('suspected_fault_type')}) runbook={attempt.get('matched_runbook')} => {attempt.get('next_action')}")
    print(
        "  [SELECTED]  Final Prediction:  "
        f"service={row.get('selected_predicted_service')}, fault={row.get('selected_predicted_fault')}, "
        f"runbook={row.get('selected_predicted_runbook')} "
        f"[svc={'OK' if row.get('final_service_correct') else 'WRONG'}, fault={'OK' if row.get('final_fault_correct') else 'WRONG'}, "
        f"runbook={'OK' if row.get('runbook_correct_e2e') else 'WRONG'}, reason={row.get('selected_failure_reason')}, "
        f"joint={'TRUE' if row.get('selected_prediction_correct') else 'WRONG'}]"
    )
    print(f"  [VERIFY]    Result:            {'OK' if row.get('verify_success') else row.get('verify_next_action')}")
    print(f"  [CONCLUSION] Final selected result is {'TRUE' if row.get('selected_prediction_correct') else 'WRONG'}\n")


def _summary(rows: list[dict], duration: float, top_k: int) -> dict:
    total = len(rows)
    detected = sum(bool(r.get("detected")) for r in rows)
    svc = sum(bool(r.get("final_service_correct")) for r in rows)
    runbook = sum(bool(r.get("runbook_correct_e2e")) for r in rows)
    verify = sum(bool(r.get("verify_success")) for r in rows)
    pipeline = sum(bool(r.get("selected_prediction_correct")) for r in rows)
    return {
        "benchmark": "detect_decide_verify_cdo_api_e2e",
        "total_runs": total,
        "detect": {
            "detected_runs": detected,
            "detection_rate": round(detected / total, 4) if total else 0,
            "service_top1_accuracy": round(svc / total, 4) if total else 0,
            f"service_top{top_k}_accuracy": round(sum(bool(r.get("service_in_top_k")) for r in rows) / total, 4) if total else 0,
            "fault_type_accuracy_on_detected": round(sum(bool(r.get("final_fault_correct")) for r in rows) / detected, 4) if detected else 0,
            "macro_precision": 0,
            "macro_recall": 0,
            "macro_f1": 0,
            "fault_accuracy_by_type": {},
        },
        "decide": {"runbook_accuracy_e2e": round(runbook / detected, 4) if detected else 0, "correct_runbook_e2e": runbook, "pipeline_success_rate": round(pipeline / total, 4) if total else 0, "pipeline_success_count": pipeline, "runbook_accuracy_oracle_fault": 0},
        "verify": {"success_rate_on_detected": round(verify / detected, 4) if detected else 0, "success_count": verify},
        "latency_ms": {"decide_mean": 0, "decide_p99": 0, "verify_mean": 0},
        "config": {"top_k": top_k},
        "duration_seconds": round(duration, 2),
        "per_run": rows,
    }


def _print_summary(report: dict) -> None:
    d, c, v = report["detect"], report["decide"], report["verify"]
    top_k = report["config"]["top_k"]
    print("\n=======================================================")
    print("          CDO API E2E SUMMARY REPORT                   ")
    print("=======================================================")
    print(f"Total Runs Evaluated:          {report['total_runs']}")
    print(f"Anomaly Detection Rate:        {d['detection_rate'] * 100:.1f}% ({d['detected_runs']}/{report['total_runs']})")
    print(f"Service Final Accuracy:        {d['service_top1_accuracy'] * 100:.1f}%")
    print(f"Service Top-{top_k} Coverage:       {d[f'service_top{top_k}_accuracy'] * 100:.1f}%")
    print(f"Fault Type Accuracy:           {d['fault_type_accuracy_on_detected'] * 100:.1f}% (on detected, service-aware)")
    print(f"Runbook Accuracy (E2E):        {c['runbook_accuracy_e2e'] * 100:.1f}% ({c['correct_runbook_e2e']}/{d['detected_runs']} detected)")
    print(f"Verify Success Rate:           {v['success_rate_on_detected'] * 100:.1f}% ({v['success_count']}/{d['detected_runs']} detected)")
    print(f"Full Pipeline Success:         {c['pipeline_success_rate'] * 100:.1f}%")
    print("=======================================================\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="CDO-style E2E benchmark using real AI Engine APIs")
    parser.add_argument("--sample-size", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--api-url", default=f"http://{API_HOST}:{API_PORT}")
    parser.add_argument("--output", default=os.path.join(AI_ENGINE_ROOT, "dataset", "benchmark_reports", "benchmark_e2e.json"))
    args = parser.parse_args()

    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        gt = json.load(f)
    keys = _sample_run_keys(gt, args.sample_size)
    rows = []
    start = time.perf_counter()
    print(f"Calling AI Engine APIs at: {args.api_url}")
    for idx, key in enumerate(keys, 1):
        row = _run_one_cdo_flow(args.api_url, key, gt[key], args.top_k)
        rows.append(row)
        _print_per_run(row, idx, len(keys))
    report = _summary(rows, time.perf_counter() - start, args.top_k)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    _print_summary(report)
    print(f"Report saved: {args.output}")


if __name__ == "__main__":
    main()
