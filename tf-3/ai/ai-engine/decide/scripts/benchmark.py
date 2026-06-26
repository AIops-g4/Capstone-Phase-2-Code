"""
Benchmark decide engine on RE3 ground truth (runbook matching accuracy).
"""
import argparse
import json
import os
import sys
import time
import uuid

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DECIDE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, DECIDE_DIR)

from src.config import DECIDER_TYPE, GROUND_TRUTH_PATH
from src.decider import DeciderService


def run_benchmark(sample_size: int | None = None) -> dict:
    if not os.path.exists(GROUND_TRUTH_PATH):
        print(f"Error: {GROUND_TRUTH_PATH} not found. Run: python scripts/setup_dataset.py")
        sys.exit(1)

    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    keys = sorted(ground_truth.keys())
    if sample_size and sample_size < len(keys):
        keys = keys[:sample_size]

    decider = DeciderService()
    correct = 0
    total = 0
    latencies_ms = []
    per_run = []

    for key in keys:
        gt = ground_truth[key]
        ctx = {
            "target_service": gt["target_service"],
            "suspected_fault_type": gt["suspected_fault_type"],
            "system": "E-COMMERCE",
            "namespace": "production",
            "deployment": f"deployment/{gt['target_service']}",
        }
        expected = gt["matched_runbook"]
        t0 = time.perf_counter()
        result = decider.decide(ctx)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies_ms.append(elapsed_ms)

        pred = result["matched_runbook"]
        ok = pred == expected
        if ok:
            correct += 1
        total += 1
        per_run.append(
            {
                "run_key": key,
                "fault": gt["suspected_fault_type"],
                "expected_runbook": expected,
                "predicted_runbook": pred,
                "correct": ok,
                "latency_ms": round(elapsed_ms, 2),
                "action": result["action_plan"][0]["action"] if result["action_plan"] else None,
            }
        )

    accuracy = correct / total if total else 0.0
    p99 = sorted(latencies_ms)[int(0.99 * len(latencies_ms)) - 1] if latencies_ms else 0

    report = {
        "dataset": "re3",
        "decider_type": DECIDER_TYPE,
        "total_runs": total,
        "correct_runbook": correct,
        "runbook_accuracy": round(accuracy, 4),
        "latency_ms": {
            "mean": round(sum(latencies_ms) / len(latencies_ms), 2) if latencies_ms else 0,
            "p99": round(p99, 2),
            "sla_rule_based_ms": 500,
            "sla_pass": p99 < 500,
        },
        "per_run": per_run,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="Benchmark decide on RE3 ground truth")
    parser.add_argument("--sample-size", type=int, default=None, help="Limit number of runs")
    parser.add_argument(
        "--output",
        default=os.path.join(DECIDE_DIR, "benchmark_report_re2.json"),
        help="Output JSON path",
    )
    args = parser.parse_args()

    report = run_benchmark(sample_size=args.sample_size)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("=" * 60)
    print("  DECIDE BENCHMARK (RE2)")
    print("=" * 60)
    print(f"Decider:          {report['decider_type']}")
    print(f"Runs evaluated:   {report['total_runs']}")
    print(f"Runbook accuracy: {report['runbook_accuracy'] * 100:.1f}%")
    print(f"Mean latency:     {report['latency_ms']['mean']} ms")
    print(f"p99 latency:    {report['latency_ms']['p99']} ms (SLA <500ms: {report['latency_ms']['sla_pass']})")
    print(f"Report saved:     {args.output}")


if __name__ == "__main__":
    main()
