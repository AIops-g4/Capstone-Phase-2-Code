import os
from pathlib import Path
from dotenv import load_dotenv

# Load env variables
load_dotenv()

class Settings:
    # Directories
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATASET_PATH = Path(os.getenv("DATASET_PATH", str(BASE_DIR / "dataset")))
    TOPOLOGY_GRAPH_PATH = Path(os.getenv("TOPOLOGY_GRAPH_PATH", str(BASE_DIR / "topology_graph.json")))

    # Benchmark settings
    BENCHMARK_USE_HINTS = os.getenv("BENCHMARK_USE_HINTS", "False").lower() in ("true", "1", "t")

    # Decider settings
    DECIDER_TYPE = os.getenv("DECIDER_TYPE", "rule-based").lower()

    # AWS/Bedrock settings
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

    # Detection parameters
    ANOMALY_THRESHOLD_CPU = float(os.getenv("ANOMALY_THRESHOLD_CPU", "0.80"))
    ANOMALY_THRESHOLD_MEM_MB = float(os.getenv("ANOMALY_THRESHOLD_MEM_MB", "150.0"))
    ANOMALY_THRESHOLD_ERROR_RATE = float(os.getenv("ANOMALY_THRESHOLD_ERROR_RATE", "0.05"))
    ANOMALY_THRESHOLD_LATENCY_P95_MS = float(os.getenv("ANOMALY_THRESHOLD_LATENCY_P95_MS", "1000.0"))

    # EWMA parameters
    EWMA_SPAN = int(os.getenv("EWMA_SPAN", "5"))

    # Isolation Forest parameters
    IF_CONTAMINATION = float(os.getenv("IF_CONTAMINATION", "0.05"))
    IF_N_ESTIMATORS = int(os.getenv("IF_N_ESTIMATORS", "100"))

    # Drain3 Log Parser parameters
    DRAIN3_SIM_TH = float(os.getenv("DRAIN3_SIM_TH", "0.4"))
    DRAIN3_DEPTH = int(os.getenv("DRAIN3_DEPTH", "4"))
    DRAIN3_MAX_CHILDREN = int(os.getenv("DRAIN3_MAX_CHILDREN", "100"))
    DRAIN3_MAX_CLUSTERS = int(os.getenv("DRAIN3_MAX_CLUSTERS", "1000"))

    # Specific Detector Thresholds
    DETECTOR_Z_THRESHOLD = float(os.getenv("DETECTOR_Z_THRESHOLD", "3.0"))
    DETECTOR_IF_SCALE = float(os.getenv("DETECTOR_IF_SCALE", "15.0"))
    DETECTOR_COMBINED_THRESHOLD = float(os.getenv("DETECTOR_COMBINED_THRESHOLD", "3.0"))
    DETECTOR_BEST_SCORE_THRESHOLD = float(os.getenv("DETECTOR_BEST_SCORE_THRESHOLD", "5.0"))

    # Alert Correlation Parameters
    CORRELATION_TIME_WINDOW_SEC = int(os.getenv("CORRELATION_TIME_WINDOW_SEC", "60"))
    CORRELATION_SEMANTIC_WEIGHT = float(os.getenv("CORRELATION_SEMANTIC_WEIGHT", "0.7"))

    # Cost settings
    COST_LIMIT_DAILY = float(os.getenv("COST_LIMIT_DAILY", "50.0"))

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
