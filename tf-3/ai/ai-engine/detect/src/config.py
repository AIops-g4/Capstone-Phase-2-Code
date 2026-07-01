import os


# Define package and dotenv paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DETECT_DIR = os.path.dirname(SCRIPT_DIR)
AI_ENGINE_DIR = os.path.dirname(DETECT_DIR)
DOTENV_PATH = os.path.join(DETECT_DIR, ".env")

def load_dotenv(dotenv_path):
    """
    Manually parses the .env file to load configuration variables into os.environ.
    This eliminates the need for third-party libraries like python-dotenv.
    """
    if os.path.exists(dotenv_path):
        with open(dotenv_path, "r") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    # Clean key and value
                    clean_key = key.strip()
                    clean_val = val.strip().strip('"').strip("'")
                    os.environ[clean_key] = clean_val
        print(f"Loaded environment variables from: {dotenv_path}")

# Load dotenv variables on import
load_dotenv(DOTENV_PATH)


# --- Configuration Constants ---


def _resolve_path(value: str, base_dir: str) -> str:
    if not os.path.isabs(value):
        return os.path.normpath(os.path.join(base_dir, value))
    return value


def _parse_float_map(value: str) -> dict:
    parsed = {}
    for item in value.split(","):
        if not item.strip() or ":" not in item:
            continue
        key, raw_val = item.split(":", 1)
        try:
            parsed[key.strip()] = float(raw_val.strip())
        except ValueError:
            continue
    return parsed


def _parse_pattern_map(value: str) -> dict:
    parsed = {}
    for group in value.split(";"):
        if not group.strip() or ":" not in group:
            continue
        key, raw_tokens = group.split(":", 1)
        tokens = [t.strip().lower() for t in raw_tokens.split("|") if t.strip()]
        if tokens:
            parsed[key.strip()] = tokens
    return parsed


# File Paths
DATASET_DIR = _resolve_path(os.getenv("DATASET_DIR", os.path.join(AI_ENGINE_DIR, "dataset")), DETECT_DIR)
GROUND_TRUTH_PATH = _resolve_path(
    os.getenv("GROUND_TRUTH_PATH", os.path.join(DATASET_DIR, "ground_truth.json")),
    DETECT_DIR,
)
RUNBOOKS_PATH = _resolve_path(
    os.getenv("RUNBOOKS_PATH", os.path.join(DATASET_DIR, "runbooks.json")),
    DETECT_DIR,
)
DEPENDENCY_GRAPH_PATH = _resolve_path(
    os.getenv("DEPENDENCY_GRAPH_PATH", os.path.join(DATASET_DIR, "dependency_graph.json")),
    DETECT_DIR,
)

# Server Configuration
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8050"))

# Detect-only benchmark uses BOCPD for anomaly detection.
BASELINE_LENGTH = int(os.getenv("BASELINE_LENGTH", "600"))
BOCPD_HAZARD = int(os.getenv("BOCPD_HAZARD", "50"))

# Correlation & Diagnostics Hyperparameters
CORRELATION_THRESHOLD = float(os.getenv("CORRELATION_THRESHOLD", "0.4"))
ANALYSIS_WINDOW_SIZE = int(os.getenv("ANALYSIS_WINDOW_SIZE", "120"))

# BARO RCA Configuration
USE_BARO_RCA = os.getenv("USE_BARO_RCA", "False").lower() == "true"
BARO_TOP_K = int(os.getenv("BARO_TOP_K", "3"))

# Drain3 Log Parser Configuration
DRAIN_SIM_TH = float(os.getenv("DRAIN_SIM_TH", "0.4"))
DRAIN_DEPTH = int(os.getenv("DRAIN_DEPTH", "4"))
LOG_ERROR_KEYWORDS = os.getenv("LOG_ERROR_KEYWORDS", r"(error|exception|fail|timeout|exhaust|limit|abort|invalid|refused|conn|crash|oom|kill)")

# RCA Engine Scoring & Weighting Hyperparameters
RCA_ZSCORE_THRESHOLD = float(os.getenv("RCA_ZSCORE_THRESHOLD", "3.0"))
RCA_ZSCORE_MAX_CONTRIBUTION = float(os.getenv("RCA_ZSCORE_MAX_CONTRIBUTION", "500.0"))
RCA_LOG_METRIC_DEFAULT_WEIGHT = float(os.getenv("RCA_LOG_METRIC_DEFAULT_WEIGHT", "1.0"))
RCA_LOG_METRIC_COLOCATED_WEIGHT = float(os.getenv("RCA_LOG_METRIC_COLOCATED_WEIGHT", "3.0"))
RCA_LOG_METRIC_MULTIPLIER = float(os.getenv("RCA_LOG_METRIC_MULTIPLIER", "15.0"))
RCA_CONFIDENCE_MAX = float(os.getenv("RCA_CONFIDENCE_MAX", "0.95"))
RCA_CONFIDENCE_BASE = float(os.getenv("RCA_CONFIDENCE_BASE", "0.70"))
RCA_CONFIDENCE_DIVISOR = float(os.getenv("RCA_CONFIDENCE_DIVISOR", "200.0"))
RCA_SMOOTHING_WINDOW = int(os.getenv("RCA_SMOOTHING_WINDOW", "15"))
RCA_DEVIATION_WINDOW = int(os.getenv("RCA_DEVIATION_WINDOW", "30"))

# Alert Correlation & Verification Hyperparameters
ALERT_HEALING_WINDOW_SECONDS = int(os.getenv("ALERT_HEALING_WINDOW_SECONDS", "120"))

# BOCPD Evaluation Slicing Hyperparameters
EVAL_BOCPD_WINDOW_BEFORE = int(os.getenv("EVAL_BOCPD_WINDOW_BEFORE", "120"))
EVAL_BOCPD_WINDOW_AFTER = int(os.getenv("EVAL_BOCPD_WINDOW_AFTER", "30"))
EVAL_BOCPD_BASELINE_LENGTH = int(os.getenv("EVAL_BOCPD_BASELINE_LENGTH", "100"))
RCA_ANALYSIS_WINDOW_AFTER = int(os.getenv("RCA_ANALYSIS_WINDOW_AFTER", "10"))
BARO_RCA_CONFIDENCE = float(os.getenv("BARO_RCA_CONFIDENCE", "0.90"))
RCA_STD_REG_MULTIPLIER = float(os.getenv("RCA_STD_REG_MULTIPLIER", "0.05"))
RCA_STD_REG_ADDITIVE = float(os.getenv("RCA_STD_REG_ADDITIVE", "0.05"))

# Generic fault-type inference configuration. These defaults describe signal-name
# families, not dataset folders or team-specific services. Override via env for
# different CDO telemetry naming conventions without changing code.
FAULT_SIGNAL_PATTERNS = _parse_pattern_map(os.getenv(
    "FAULT_SIGNAL_PATTERNS",
    "cpu:cpu|processor|core;"
    "mem:mem|memory|oom|rss|heap;"
    "disk:disk|diskio|disk_io|io|iops|fs|filesystem;"
    "socket:socket|connection|conn|fd|file_descriptor|tcp;"
    "loss:loss|packet|error|error_rate|unavailable|reset|refused|deadline|no healthy|dropped;"
    "delay:latency|delay|p95|p90|p99|timeout|duration|slow"
))
FAULT_SIGNAL_WEIGHTS = _parse_float_map(os.getenv(
    "FAULT_SIGNAL_WEIGHTS",
    "cpu:1.2,mem:2.0,disk:3.5,socket:5.5,loss:3.2,delay:0.9"
))
FAULT_LOG_EVIDENCE_WEIGHT = float(os.getenv("FAULT_LOG_EVIDENCE_WEIGHT", "12.0"))
FAULT_BARO_RANK_WEIGHT = float(os.getenv("FAULT_BARO_RANK_WEIGHT", "1.5"))
FAULT_SCORE_MIN = float(os.getenv("FAULT_SCORE_MIN", "1.0"))
SERVICES_LIST = [s.strip() for s in os.getenv("SERVICES_LIST", "checkoutservice,currencyservice,emailservice,productcatalogservice,recommendationservice,adservice,cartservice,frontend,paymentservice,redis,shippingservice").split(",") if s.strip()]
METRIC_TYPES_LIST = [m.strip() for m in os.getenv("METRIC_TYPES_LIST", "cpu,mem,latency,error,socket,diskio").split(",") if m.strip()]

