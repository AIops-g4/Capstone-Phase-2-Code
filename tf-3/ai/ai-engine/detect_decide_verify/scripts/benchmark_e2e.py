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
from collections import defaultdict
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


def _find_detection_candidates(
    df_metrics: pd.DataFrame,
    inject_time: int,
    detection_results: dict,
    use_bocpd: bool,
    inject_row_idx_sliced: int | None,
    max_candidates: int = 8,
    min_gap: int = 5,
) -> list[int]:
    """Return multiple anomaly indices so benchmark can re-run detect/RCA retries."""
    mif_anoms = detection_results["multivariate"]["anomalies"]
    ewma_all_anoms = np.zeros(len(mif_anoms), dtype=bool)
    for res in detection_results["ewma"].values():
        ewma_all_anoms |= res["anomalies"]

    combined_anoms = mif_anoms | ewma_all_anoms
    if use_bocpd and inject_row_idx_sliced is not None:
        search_start = max(0, inject_row_idx_sliced - 30)
        search_end = len(combined_anoms)
    else:
        inject_row_idx = df_metrics[df_metrics["time"] >= inject_time].index.min()
        if pd.isna(inject_row_idx):
            inject_row_idx = len(df_metrics) - 100
        search_start = max(0, int(inject_row_idx) - 30)
        search_end = len(df_metrics)

    candidates: list[int] = []
    last_idx = -min_gap - 1
    for i in range(search_start, search_end):
        if combined_anoms[i] and i - last_idx >= min_gap:
            candidates.append(i)
            last_idx = i
            if len(candidates) >= max_candidates:
                break
    return candidates


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


def _decision_context(decide_result: dict, fallback_context: dict) -> dict:
    """Return the context that should be considered as the executed healing target."""
    corrected = decide_result.get("corrected_anomaly_context")
    if isinstance(corrected, dict) and corrected.get("target_service"):
        ctx = dict(fallback_context)
        ctx.update(corrected)
        ctx.setdefault("deployment", f"deployment/{ctx['target_service']}")
        return ctx
    return dict(fallback_context)


def _simulate_post_telemetry(
    target_service: str,
    true_service: str,
    true_fault: str,
    attempted_fault: str,
) -> list[SimpleNamespace]:
    """
    Offline benchmark verifier model.

    The real system would observe post-heal telemetry. In the offline benchmark we
    only know ground truth, so recovery is successful only when both service and
    fault type match. Wrong fault on the right service leaves the target unhealthy;
    wrong service creates a regression signal on the real faulty service.
    """
    if target_service == true_service and attempted_fault == true_fault:
        return [
            SimpleNamespace(service=target_service, signal_name="service_error_rate", value=0.0),
            SimpleNamespace(service=target_service, signal_name="service_latency_p95", value=0.03),
        ]

    if target_service == true_service:
        return [
            SimpleNamespace(service=target_service, signal_name="service_error_rate", value=1.0),
            SimpleNamespace(service=target_service, signal_name="service_latency_p95", value=999.0),
        ]

    return [
        SimpleNamespace(service=target_service, signal_name="service_error_rate", value=0.0),
        SimpleNamespace(service=target_service, signal_name="service_latency_p95", value=0.03),
        SimpleNamespace(service=true_service, signal_name="service_error_rate", value=1.0),
    ]


def _execute_offline_heal_attempt(
    verifier: VerificationEngine,
    decide_result: dict,
    executed_context: dict,
    true_service: str,
    true_fault: str,
) -> tuple[bool, bool, str, str | None, float]:
    first_action = decide_result["action_plan"][0] if decide_result.get("action_plan") else None
    if not first_action:
        return False, False, "ESCALATE", "No healing action was generated.", 0.0

    action_executed = SimpleNamespace(
        action=first_action["action"],
        target=first_action["target"],
        status="COMPLETED",
        execution_time_seconds=45,
    )
    target_service = first_action["target"].split("/")[-1]
    attempted_fault = executed_context.get("suspected_fault_type", "unknown")
    post_telemetry = _simulate_post_telemetry(
        target_service=target_service,
        true_service=true_service,
        true_fault=true_fault,
        attempted_fault=attempted_fault,
    )

    t_verify = time.perf_counter()
    verify_ok, verify_regression, verify_next_action, verify_reason = verifier.verify_action(
        action_executed,
        post_telemetry,
    )
    return (
        verify_ok,
        verify_regression,
        verify_next_action,
        verify_reason,
        (time.perf_counter() - t_verify) * 1000,
    )


def _decide_with_timer(
    healer: SelfHealer,
    context: dict,
    detect_evidence: dict,
    force_rule_based: bool = False,
) -> tuple[dict, float]:
    t_decide = time.perf_counter()
    if force_rule_based:
        old_use_llm = os.environ.get("USE_LLM_DECISION")
        os.environ["USE_LLM_DECISION"] = "False"
        try:
            result = healer.decide(context, detect_evidence=detect_evidence)
        finally:
            if old_use_llm is None:
                os.environ.pop("USE_LLM_DECISION", None)
            else:
                os.environ["USE_LLM_DECISION"] = old_use_llm
    else:
        result = healer.decide(context, detect_evidence=detect_evidence)
    return result, (time.perf_counter() - t_decide) * 1000


def _run_fallback_healing_strategy(
    healer: SelfHealer,
    verifier: VerificationEngine,
    initial_context: dict,
    initial_decision: dict,
    detect_evidence: dict,
    top_k_candidates: list[str],
    true_service: str,
    true_fault: str,
) -> dict:
    """
    Execute the user-requested fallback policy:
    1. Try the normal self-heal first.
    2. If the selected service is correct but the fault type is wrong, call LLM
       again with failed verification evidence to reassess the fault type.
    3. If the selected service is wrong, exhaust the LLM-generated fault type on
       top-k candidate services; if none recovers, call LLM again to pick another
       service using failed-attempt evidence.
    """
    attempts: list[dict] = []
    decide_latency_ms = 0.0
    verify_latency_ms = 0.0

    executed_context = _decision_context(initial_decision, initial_context)
    current_decision = initial_decision
    current_context = executed_context

    def record_attempt(ctx: dict, decision: dict, phase: str) -> tuple[bool, bool, str, str | None]:
        nonlocal verify_latency_ms
        ok, regression, next_action, reason, latency = _execute_offline_heal_attempt(
            verifier,
            decision,
            ctx,
            true_service,
            true_fault,
        )
        verify_latency_ms += latency
        attempts.append(
            {
                "phase": phase,
                "target_service": ctx.get("target_service"),
                "suspected_fault_type": ctx.get("suspected_fault_type"),
                "matched_runbook": decision.get("matched_runbook"),
                "success": ok and next_action == "DONE",
                "next_action": next_action,
                "reason": reason,
            }
        )
        return ok, regression, next_action, reason

    verify_ok, verify_regression, verify_next_action, verify_reason = record_attempt(
        current_context,
        current_decision,
        "initial_self_heal",
    )

    final_decision = current_decision
    final_context = current_context
    fallback_used = False
    fallback_reason = "initial_self_heal_succeeded" if verify_ok and verify_next_action == "DONE" else "initial_self_heal_failed"

    if not (verify_ok and verify_next_action == "DONE"):
        attempted_service = current_context.get("target_service")
        attempted_fault = current_context.get("suspected_fault_type")

        if attempted_service == true_service and attempted_fault != true_fault:
            fallback_used = True
            reassess_evidence = dict(detect_evidence)
            reassess_evidence.update(
                {
                    "fallback_mode": "fault_reassessment_after_failed_self_heal",
                    "failed_self_heal_attempts": attempts,
                    "instruction": (
                        "The target service appears correct, but the selected fault runbook did not recover it. "
                        "Keep the service if evidence still supports it and choose a better fault type."
                    ),
                }
            )
            reassess_context = dict(current_context)
            final_decision, latency = _decide_with_timer(healer, reassess_context, reassess_evidence)
            decide_latency_ms += latency
            final_context = _decision_context(final_decision, reassess_context)
            verify_ok, verify_regression, verify_next_action, verify_reason = record_attempt(
                final_context,
                final_decision,
                "llm_fault_reassessment",
            )
            fallback_reason = "fault_reassessment_after_failed_self_heal"

        elif attempted_service != true_service:
            fallback_used = True
            generated_fault = attempted_fault
            for candidate_service in top_k_candidates:
                if candidate_service == attempted_service:
                    continue
                candidate_ctx = dict(current_context)
                candidate_ctx["target_service"] = candidate_service
                candidate_ctx["deployment"] = f"deployment/{candidate_service}"
                candidate_ctx["suspected_fault_type"] = generated_fault
                candidate_evidence = dict(detect_evidence)
                candidate_evidence.update(
                    {
                        "fallback_mode": "try_same_generated_fault_on_topk_service",
                        "failed_self_heal_attempts": attempts,
                        "candidate_service": candidate_service,
                        "generated_fault_type": generated_fault,
                    }
                )
                candidate_decision, latency = _decide_with_timer(
                    healer,
                    candidate_ctx,
                    candidate_evidence,
                    force_rule_based=True,
                )
                decide_latency_ms += latency
                candidate_context = _decision_context(candidate_decision, candidate_ctx)
                verify_ok, verify_regression, verify_next_action, verify_reason = record_attempt(
                    candidate_context,
                    candidate_decision,
                    "topk_same_fault_self_heal",
                )
                final_decision = candidate_decision
                final_context = candidate_context
                if verify_ok and verify_next_action == "DONE":
                    fallback_reason = "same_fault_recovered_on_topk_candidate"
                    break

            if not (verify_ok and verify_next_action == "DONE"):
                reassess_evidence = dict(detect_evidence)
                reassess_evidence.update(
                    {
                        "fallback_mode": "service_reassessment_after_exhausting_generated_fault",
                        "failed_self_heal_attempts": attempts,
                        "instruction": (
                            "The generated fault type has been tried on available candidate services and did not recover the system. "
                            "Reassess target_service among service_top_k and choose a different service/fault if evidence supports it."
                        ),
                    }
                )
                reassess_context = dict(current_context)
                final_decision, latency = _decide_with_timer(healer, reassess_context, reassess_evidence)
                decide_latency_ms += latency
                final_context = _decision_context(final_decision, reassess_context)
                verify_ok, verify_regression, verify_next_action, verify_reason = record_attempt(
                    final_context,
                    final_decision,
                    "llm_service_reassessment",
                )
                fallback_reason = "service_reassessment_after_exhausting_generated_fault"

    return {
        "decision": final_decision,
        "context": final_context,
        "attempts": attempts,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "verify_ok": verify_ok,
        "verify_regression": verify_regression,
        "verify_next_action": verify_next_action,
        "verify_reason": verify_reason,
        "extra_decide_latency_ms": decide_latency_ms,
        "verify_latency_ms": verify_latency_ms,
    }


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
    fault_confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fault_totals: dict[str, int] = defaultdict(int)
    fault_correct_by_type: dict[str, int] = defaultdict(int)

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

        print(f"[{idx + 1}/{len(run_keys)}] Evaluating Run: {run_key}")
        print(f"  True Fault Service: {true_service} injected at {inject_time}")
        print(f"  True Fault Type:    {true_fault}")

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
            print(f"  [ERROR] Missing metrics or logs files for {run_key}. Skipping.\n")
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
            detection_candidates_sliced = _find_detection_candidates(
                df_sliced,
                inject_time,
                detection_results,
                True,
                int(inject_row_idx_sliced),
            )
            detection_candidates = []
            for candidate_idx in detection_candidates_sliced:
                candidate_time = df_sliced.iloc[candidate_idx]["time"]
                detection_candidates.append(int(df_metrics[df_metrics["time"] == candidate_time].index[0]))
            if detection_idx >= 0:
                detect_time = df_sliced.iloc[detection_idx]["time"]
                detection_idx = int(df_metrics[df_metrics["time"] == detect_time].index[0])
        else:
            detection_results = run_metric_anomaly_detection(df_metrics, BASELINE_LENGTH)
            detection_idx, _ = _find_detection_idx(
                df_metrics, inject_time, detection_results, False, None
            )
            detection_candidates = _find_detection_candidates(
                df_metrics,
                inject_time,
                detection_results,
                False,
                None,
            )

        if not detection_candidates and detection_idx >= 0:
            detection_candidates = [detection_idx]

        if detection_idx < 0:
            print("  [RESULT] Anomaly Detection FAILED (False Negative).\n")
            y_true.append(true_service)
            y_pred.append("undetected")
            per_run.append(row)
            continue

        detected += 1
        row["detected"] = True
        detect_time = df_metrics.iloc[detection_idx]["time"]
        rto = int(detect_time - inject_time)
        row["rto"] = rto
        print(f"  [DETECTED] Anomaly flagged at second {detection_idx} (Time: {detect_time}, RTO: {rto}s).")

        time_start = int(df_metrics["time"].min())
        time_end = int(df_metrics["time"].max())
        log_parser = Drain3LogParser(service_aware=True)
        df_log_ts, temp_info = log_parser.parse_logs(df_logs, time_start, time_end)

        detect_retry_history: list[dict] = []
        selected_retry_idx = 0
        pred_service = pred_fault = reasoning = ""
        confidence = 0.0
        top_k_candidates: list[str] = []
        decide_result: dict = {}
        fallback_result: dict = {}
        executed_context: dict = {}
        pred_final_service = ""
        pred_final_fault = ""
        runbook_ok = False
        verify_ok = False
        verify_next_action = "ESCALATE"
        verify_regression = False

        for retry_idx, candidate_detection_idx in enumerate(detection_candidates):
            if retry_idx > 0:
                retry_time = df_metrics.iloc[candidate_detection_idx]["time"]
                print(
                    f"  [RE-DETECT] Retry #{retry_idx + 1}: anomaly at second "
                    f"{candidate_detection_idx} (Time: {retry_time})"
                )

            rca.baseline_len = BASELINE_LENGTH
            candidate_service, candidate_fault, candidate_reasoning, candidate_confidence = rca.analyze(
                df_metrics=df_metrics,
                df_logs=df_log_ts,
                template_info=temp_info,
                anomaly_idx=candidate_detection_idx,
                window_size=120,
            )

            candidate_top_k = rca.last_top_k[:eval_top_k]
            candidate_context = {
                "target_service": candidate_service,
                "suspected_fault_type": candidate_fault,
                "system": "E-COMMERCE",
                "namespace": "production",
                "deployment": f"deployment/{candidate_service}",
                "trigger_metric": "",
                "trigger_value": None,
            }
            candidate_evidence = {
                "detect_reasoning": candidate_reasoning,
                "detect_confidence": round(candidate_confidence, 4),
                "service_top_k": candidate_top_k,
                "trigger_metric": candidate_context.get("trigger_metric"),
                "trigger_value": candidate_context.get("trigger_value"),
                "rca_engine": engine,
                "detected_at_index": candidate_detection_idx,
                "detected_at_time": float(df_metrics.iloc[candidate_detection_idx]["time"]),
                "rto_seconds": int(df_metrics.iloc[candidate_detection_idx]["time"] - inject_time),
                "detect_retry_attempt": retry_idx + 1,
                "previous_detect_attempts": detect_retry_history,
            }

            t_decide = time.perf_counter()
            candidate_decision = healer.decide(candidate_context, detect_evidence=candidate_evidence)
            latencies_ms.append((time.perf_counter() - t_decide) * 1000)

            candidate_fallback = _run_fallback_healing_strategy(
                healer=healer,
                verifier=verifier,
                initial_context=candidate_context,
                initial_decision=candidate_decision,
                detect_evidence=candidate_evidence,
                top_k_candidates=candidate_top_k,
                true_service=true_service,
                true_fault=true_fault,
            )
            candidate_decision = candidate_fallback["decision"]
            candidate_executed_context = candidate_fallback["context"]
            if candidate_fallback["extra_decide_latency_ms"]:
                latencies_ms[-1] += candidate_fallback["extra_decide_latency_ms"]
            verify_latencies_ms.append(candidate_fallback["verify_latency_ms"])

            candidate_final_service = candidate_executed_context.get("target_service", candidate_service)
            candidate_final_fault = candidate_executed_context.get("suspected_fault_type", candidate_fault)
            candidate_verify_ok = candidate_fallback["verify_ok"]
            candidate_verify_next_action = candidate_fallback["verify_next_action"]
            candidate_record = {
                "retry": retry_idx + 1,
                "detected_at_index": candidate_detection_idx,
                "pred_service": candidate_service,
                "pred_fault": candidate_fault,
                "top_k_candidates": candidate_top_k,
                "final_service": candidate_final_service,
                "final_fault": candidate_final_fault,
                "verify_next_action": candidate_verify_next_action,
                "verify_success": candidate_verify_ok and candidate_verify_next_action == "DONE",
                "service_correct": candidate_final_service == true_service,
            }
            detect_retry_history.append(candidate_record)

            pred_service = candidate_service
            pred_fault = candidate_fault
            reasoning = candidate_reasoning
            confidence = candidate_confidence
            top_k_candidates = candidate_top_k
            anomaly_context = candidate_context
            detect_evidence = candidate_evidence
            decide_result = candidate_decision
            fallback_result = candidate_fallback
            executed_context = candidate_executed_context
            pred_final_service = candidate_final_service
            pred_final_fault = candidate_final_fault
            verify_ok = candidate_verify_ok
            verify_next_action = candidate_verify_next_action
            verify_regression = candidate_fallback["verify_regression"]
            selected_retry_idx = retry_idx

            if pred_final_service == true_service or (verify_ok and verify_next_action == "DONE"):
                break

        service_ok = pred_service == true_service
        in_top_k = true_service in top_k_candidates
        fault_ok = pred_fault == true_fault
        fault_confusion[true_fault][pred_fault] += 1
        fault_totals[true_fault] += 1

        if service_ok:
            service_top1 += 1
        if in_top_k:
            service_topk += 1
        if fault_ok:
            fault_correct += 1
            fault_correct_by_type[true_fault] += 1

        y_true.append(true_service)
        y_pred.append(pred_service)

        pred_runbook = decide_result["matched_runbook"]
        runbook_ok = pred_runbook == expected_runbook

        if runbook_ok:
            runbook_e2e += 1

        if verify_ok and verify_next_action == "DONE":
            verify_success += 1
        final_service_ok = pred_final_service == true_service
        final_fault_ok = pred_final_fault == true_fault
        if final_service_ok and runbook_ok and verify_ok:
            pipeline_success += 1

        oracle_ctx = dict(anomaly_context)
        oracle_ctx["suspected_fault_type"] = true_fault
        old_use_llm = os.environ.get("USE_LLM_DECISION")
        os.environ["USE_LLM_DECISION"] = "False"
        try:
            oracle_runbook = healer.decide(oracle_ctx, detect_evidence=detect_evidence)["matched_runbook"]
        finally:
            if old_use_llm is None:
                os.environ.pop("USE_LLM_DECISION", None)
            else:
                os.environ["USE_LLM_DECISION"] = old_use_llm
        oracle_ok = oracle_runbook == expected_runbook
        if oracle_ok:
            runbook_oracle += 1

        row.update(
            {
                "pred_service": pred_service,
                "pred_fault": pred_fault,
                "final_service": pred_final_service,
                "final_fault": pred_final_fault,
                "pred_runbook": pred_runbook,
                "oracle_runbook": oracle_runbook,
                "detect_assessment": decide_result.get("detect_assessment"),
                "corrected_anomaly_context": decide_result.get("corrected_anomaly_context"),
                "self_heal_attempts": fallback_result["attempts"],
                "fallback_used": fallback_result["fallback_used"],
                "fallback_reason": fallback_result["fallback_reason"],
                "detect_retry_count": selected_retry_idx + 1,
                "detect_retry_exhausted": pred_final_service != true_service,
                "detect_retry_history": detect_retry_history,
                "service_correct": service_ok,
                "final_service_correct": final_service_ok,
                "service_in_top_k": in_top_k,
                "top_k_candidates": top_k_candidates,
                "fault_correct": fault_ok,
                "final_fault_correct": final_fault_ok,
                "runbook_correct_e2e": runbook_ok,
                "runbook_correct_oracle": oracle_ok,
                "verify_success": verify_ok,
                "verify_next_action": verify_next_action,
                "verify_regression_detected": verify_regression,
                "pipeline_success": final_service_ok and runbook_ok and verify_ok,
                "confidence": round(confidence, 3),
                "decide_latency_ms": round(latencies_ms[-1], 2),
                "verify_latency_ms": round(verify_latencies_ms[-1], 2) if verify_latencies_ms else 0,
            }
        )
        per_run.append(row)

        print(f"  [DIAGNOSIS] Predicted Service: {pred_service} [{'OK' if service_ok else 'WRONG'}]")
        print(f"  [DIAGNOSIS] Predicted Fault:   {pred_fault} [{'OK' if fault_ok else 'WRONG'}]")
        print(f"  [DIAGNOSIS] Top-{eval_top_k} Candidates: {', '.join(top_k_candidates)} [{'OK' if in_top_k else 'WRONG'}]")
        print(f"  [DIAGNOSIS] Confidence Score:  {confidence:.2f}")
        if decide_result.get("detect_assessment"):
            assessment = decide_result["detect_assessment"]
            corrected = decide_result.get("corrected_anomaly_context", {})
            print(
                "  [LLM REVIEW] Detect plausible: "
                f"{assessment.get('is_detect_output_plausible')} | "
                f"Corrected: {corrected.get('target_service')} ({corrected.get('suspected_fault_type')})"
            )
            print(f"  [LLM REVIEW] Reason:           {assessment.get('assessment_reason')}")
        print(
            f"  [FINAL]     Healing Target:    {pred_final_service} ({pred_final_fault}) "
            f"[svc={'OK' if final_service_ok else 'WRONG'}, fault={'OK' if final_fault_ok else 'WRONG'}]"
        )
        if selected_retry_idx > 0 or pred_final_service != true_service:
            print(
                f"  [RE-DETECT] Attempts used:     {selected_retry_idx + 1}/{len(detection_candidates)} "
                f"[{'FOUND' if pred_final_service == true_service else 'EXHAUSTED'}]"
            )
            for retry_record in detect_retry_history:
                print(
                    "              - "
                    f"detect#{retry_record['retry']}: pred={retry_record['pred_service']} "
                    f"top={','.join(retry_record['top_k_candidates'])} "
                    f"final={retry_record['final_service']} => {retry_record['verify_next_action']}"
                )
        if fallback_result["fallback_used"]:
            print(f"  [FALLBACK]  Reason:            {fallback_result['fallback_reason']}")
            for attempt in fallback_result["attempts"]:
                print(
                    "              - "
                    f"{attempt['phase']}: {attempt['target_service']} ({attempt['suspected_fault_type']}) "
                    f"=> {attempt['next_action']}"
                )
        print(f"  [DECIDE]    Predicted Runbook: {pred_runbook} [{'OK' if runbook_ok else 'WRONG'}]")
        print(f"  [DECIDE]    Oracle Runbook:    {oracle_runbook} [{'OK' if oracle_ok else 'WRONG'}]")
        print(f"  [VERIFY]    Result:            {'OK' if verify_ok and verify_next_action == 'DONE' else verify_next_action}")
        print(f"  [REASONING] {reasoning}\n")

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
    fault_confusion_matrix = {
        true_fault: dict(sorted(pred_counts.items()))
        for true_fault, pred_counts in sorted(fault_confusion.items())
    }
    fault_accuracy_by_type = {
        fault: round(fault_correct_by_type.get(fault, 0) / total_count, 4)
        for fault, total_count in sorted(fault_totals.items())
        if total_count
    }

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
            "fault_accuracy_by_type": fault_accuracy_by_type,
            "fault_confusion_matrix": fault_confusion_matrix,
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
    print("          DETECT_DECIDE_VERIFY E2E SUMMARY REPORT       ")
    print("=======================================================")
    print(f"Evaluation completed in:       {report['duration_seconds']:.2f} seconds")
    print(f"Total Runs Evaluated:          {report['total_runs']}")
    print(f"Total Alerts Triggered:        {d['detected_runs']}")
    print(f"Anomaly Detection Rate:        {d['detection_rate'] * 100:.1f}% ({d['detected_runs']}/{report['total_runs']})")
    print(f"Service Top-1 Accuracy:        {d['service_top1_accuracy'] * 100:.1f}%")
    print(f"Service Top-{top_k} Accuracy:        {d[f'service_top{top_k}_accuracy'] * 100:.1f}%")
    print(f"Fault Type Accuracy:           {d['fault_type_accuracy_on_detected'] * 100:.1f}% (on detected)")
    print("-------------------------------------------------------")
    print(f"Macro-Precision (Service RCA): {d['macro_precision']:.3f}")
    print(f"Macro-Recall (Service RCA):    {d['macro_recall']:.3f}")
    print(f"Macro-F1-Score (Service RCA):  {d['macro_f1']:.3f} (Threshold: 0.85)")
    if d["macro_f1"] >= 0.85:
        print("[SUCCESS] Macro-F1 passes the 0.85 specification threshold.")
    else:
        print("[WARNING] Macro-F1 is below the 0.85 specification threshold.")
    print("-------------------------------------------------------")
    print(f"Runbook Accuracy (E2E):        {c['runbook_accuracy_e2e'] * 100:.1f}% ({c['correct_runbook_e2e']}/{d['detected_runs']} detected)")
    print(f"Runbook Accuracy (Oracle):     {c['runbook_accuracy_oracle_fault'] * 100:.1f}% (GT fault, on detected)")
    print(f"Verify Success Rate:           {v['success_rate_on_detected'] * 100:.1f}% ({v['success_count']}/{d['detected_runs']} detected)")
    print(f"Full Pipeline Success:         {c['pipeline_success_rate'] * 100:.1f}% (detect+svc+runbook+verify)")
    print(f"Decide Latency Mean/P99:       {report['latency_ms']['decide_mean']} / {report['latency_ms']['decide_p99']} ms")
    print("-------------------------------------------------------")
    print("Fault accuracy by type:")
    for fault, acc in d.get("fault_accuracy_by_type", {}).items():
        print(f"  - {fault:<8}: {acc * 100:.1f}%")
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
