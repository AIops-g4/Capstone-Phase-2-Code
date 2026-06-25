import os
import argparse
import json
import uuid
from pathlib import Path
from config.settings import Settings
from pipeline.ingestor import AIOpsIngestor
from pipeline.detector import AIOpsDetector
from pipeline.decide import RuleBasedDecider, LLMDecider
from pipeline.verify import AIOpsVerifier
from bench.benchmark import AIOpsBenchmark
from utils.logger import get_logger

logger = get_logger("AIOpsMain")

def run_single_case(case_key: str, split: str, settings: Settings):
    """
    Runs E2E AIOps pipeline on a single case and prints results.
    """
    logger.info(f"=== Running Single Case: {case_key} ({split} split) ===")
    
    case_path = settings.DATASET_PATH / split / case_key
    if not case_path.exists():
        logger.error(f"Case directory not found: {case_path}")
        return

    # Initialize components
    ingestor = AIOpsIngestor()
    detector = AIOpsDetector(settings)
    
    if settings.DECIDER_TYPE == "llm":
        decider = LLMDecider(settings)
    else:
        decider = RuleBasedDecider(settings)
        
    verifier = AIOpsVerifier(settings)

    correlation_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())

    # 1. Ingest
    logger.info("Stage 1: Ingesting raw telemetry...")
    telemetry_window = ingestor.ingest(str(case_path), "d3b07384-d113-495f-9f58-20d18d357d75", "OB")
    logger.info(f"Ingested {len(telemetry_window)} telemetry points.")

    # 2. Detect
    logger.info("Stage 2: Running anomaly detection...")
    detect_res = detector.detect(telemetry_window, correlation_id)
    logger.info(f"Detection Response:\n{json.dumps(detect_res, indent=2)}")

    if not detect_res.get("anomaly_detected", False):
        logger.info("No anomaly detected. Ending pipeline.")
        return

    # 3. Decide
    logger.info("Stage 3: Planning remediation...")
    decide_res = decider.decide(
        anomaly_context=detect_res["anomaly_context"],
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        dry_run_mode=False
    )
    logger.info(f"Decide Response:\n{json.dumps(decide_res, indent=2)}")

    # 4. Verify
    if decide_res and decide_res.get("action_plan"):
        logger.info("Stage 4: Executing mock action and verifying...")
        action_executed = {
            "action": decide_res["action_plan"][0]["action"],
            "target": decide_res["action_plan"][0]["target"],
            "status": "COMPLETED",
            "execution_time_seconds": 45
        }
        verify_res = verifier.verify(
            action_executed=action_executed,
            post_telemetry_window=telemetry_window,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            dry_run_mode=False
        )
        logger.info(f"Verify Response:\n{json.dumps(verify_res, indent=2)}")

def main():
    parser = argparse.ArgumentParser(description="AIOps Core Engine Command Line Interface")
    parser.add_argument("--case", type=str, help="Case key to run, e.g., 'RE2-OB/checkoutservice_cpu/1'")
    parser.add_argument("--split", type=str, default="val", help="Dataset split ('train', 'val', 'public_test', 'private_test')")
    parser.add_argument("--bench", action="store_true", help="Run benchmark on the split")
    args = parser.parse_args()

    settings = Settings()

    if args.bench:
        benchmark = AIOpsBenchmark(settings)
        benchmark.run_benchmark(split=args.split)
    elif args.case:
        run_single_case(args.case, args.split, settings)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
