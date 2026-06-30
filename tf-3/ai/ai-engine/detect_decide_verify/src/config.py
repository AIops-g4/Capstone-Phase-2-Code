import json
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
                    if clean_key not in os.environ:
                        os.environ[clean_key] = clean_val
        print(f"Loaded environment variables from: {dotenv_path}")

# Load dotenv variables on import
load_dotenv(DOTENV_PATH)


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


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_json_file(path: str, default: dict | None = None) -> dict:
    if not path or not os.path.exists(path):
        return default or {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"Warning: Failed to load JSON config from {path}: {exc}")
        return default or {}


# --- Configuration Constants ---

# File Paths
DATASET_DIR = _resolve_path(
    os.getenv("DATASET_DIR", os.path.join(AI_ENGINE_DIR, "dataset")),
    DETECT_DIR,
)
GROUND_TRUTH_PATH = _resolve_path(
    os.getenv("GROUND_TRUTH_PATH", os.path.join(DATASET_DIR, "ground_truth.json")),
    DETECT_DIR,
)
RUNBOOKS_PATH = _resolve_path(
    os.getenv("RUNBOOKS_PATH", os.path.join(DATASET_DIR, "runbooks.json")),
    DETECT_DIR,
)
PLATFORM_PROFILE_PATH = _resolve_path(
    os.getenv(
        "PLATFORM_PROFILE_PATH",
        os.path.join(DATASET_DIR, "platform_profile_online_boutique.json"),
    ),
    DETECT_DIR,
)
PLATFORM_PROFILE = _load_json_file(PLATFORM_PROFILE_PATH)
PLATFORM_PROFILE_SCHEMA_PATH = _resolve_path(
    os.getenv(
        "PLATFORM_PROFILE_SCHEMA_PATH",
        os.path.join(DATASET_DIR, "platform_profile.schema.json"),
    ),
    DETECT_DIR,
)

# Server Configuration
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8050"))

# Anomaly Detection Hyperparameters
IFOREST_MULTIVARIATE_THRESHOLD_MULTIPLIER = float(os.getenv("IFOREST_MULTIVARIATE_THRESHOLD_MULTIPLIER", "6.0"))
IFOREST_UNIVARIATE_THRESHOLD_MULTIPLIER = float(os.getenv("IFOREST_UNIVARIATE_THRESHOLD_MULTIPLIER", "5.0"))
EWMA_ALPHA = float(os.getenv("EWMA_ALPHA", "0.1"))
EWMA_THRESHOLD = float(os.getenv("EWMA_THRESHOLD", "5.0"))
BASELINE_LENGTH = int(os.getenv("BASELINE_LENGTH", "600"))

# Correlation & Diagnostics Hyperparameters
CORRELATION_THRESHOLD = float(os.getenv("CORRELATION_THRESHOLD", "0.4"))
ANALYSIS_WINDOW_SIZE = int(os.getenv("ANALYSIS_WINDOW_SIZE", "120"))

# BARO RCA Configuration
# detect_decide_verify uses the required benchmark stack: BOCPD + BARO.
USE_BARO_RCA = os.getenv("USE_BARO_RCA", "True").lower() == "true"
BARO_TOP_K = int(os.getenv("BARO_TOP_K", "3"))

# RRCF Anomaly Detection Configuration
# Keep BOCPD as the default/active detector for this stage. Do not fall back to
# Isolation Forest when env vars are absent.
USE_RRCF = os.getenv("USE_RRCF", "False").lower() == "true"
USE_BOCPD = os.getenv("USE_BOCPD", "True").lower() == "true"
RRCF_NUM_TREES = int(os.getenv("RRCF_NUM_TREES", "100"))
RRCF_TREE_SIZE = int(os.getenv("RRCF_TREE_SIZE", "256"))
RRCF_MULTIVARIATE_THRESHOLD_MULTIPLIER = float(os.getenv("RRCF_MULTIVARIATE_THRESHOLD_MULTIPLIER", "6.0"))
RRCF_UNIVARIATE_THRESHOLD_MULTIPLIER = float(os.getenv("RRCF_UNIVARIATE_THRESHOLD_MULTIPLIER", "5.0"))

# Global Random Seed
RANDOM_STATE = int(os.getenv("RANDOM_STATE", "42"))

# Drain3 Log Parser Configuration
DRAIN_SIM_TH = float(os.getenv("DRAIN_SIM_TH", "0.4"))
DRAIN_DEPTH = int(os.getenv("DRAIN_DEPTH", "4"))
LOG_ERROR_KEYWORDS = os.getenv("LOG_ERROR_KEYWORDS", r"(error|exception|fail|timeout|exhaust|limit|abort|invalid|refused|conn|crash|oom|kill)")

# Isolation Forest Core Hyperparameters
IFOREST_CONTAMINATION = float(os.getenv("IFOREST_CONTAMINATION", "0.01"))
IFOREST_N_ESTIMATORS = int(os.getenv("IFOREST_N_ESTIMATORS", "100"))

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
VERIFY_ERROR_THRESHOLD = float(os.getenv("VERIFY_ERROR_THRESHOLD", "0.05"))
VERIFY_LATENCY_THRESHOLD = float(os.getenv("VERIFY_LATENCY_THRESHOLD", "0.5"))
VERIFY_REGRESSION_ERROR_THRESHOLD = float(os.getenv("VERIFY_REGRESSION_ERROR_THRESHOLD", "0.10"))

# BOCPD Evaluation Slicing Hyperparameters
EVAL_BOCPD_WINDOW_BEFORE = int(os.getenv("EVAL_BOCPD_WINDOW_BEFORE", "120"))
EVAL_BOCPD_WINDOW_AFTER = int(os.getenv("EVAL_BOCPD_WINDOW_AFTER", "30"))
EVAL_BOCPD_BASELINE_LENGTH = int(os.getenv("EVAL_BOCPD_BASELINE_LENGTH", "100"))

# OOP Modular & Configurable Hyperparameters
DEPENDENCY_GRAPH_PATH = _resolve_path(
    os.getenv("DEPENDENCY_GRAPH_PATH", os.path.join(DATASET_DIR, "dependency_graph.json")),
    DETECT_DIR,
)
BOCPD_HAZARD = int(os.getenv("BOCPD_HAZARD", "50"))
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

# Parse service and fault-type catalogs from PLATFORM_PROFILE_PATH JSON.
# PLATFORM_PROFILE_SCHEMA_PATH documents/validates that JSON shape; the actual
# values are read from profile fields such as services and metric_types.
# Do not use env vars for these catalogs: swap PLATFORM_PROFILE_PATH for each CDO team.
SERVICES_LIST = [s.strip() for s in PLATFORM_PROFILE.get("services", []) if str(s).strip()]
METRIC_TYPES_LIST = [m.strip() for m in PLATFORM_PROFILE.get("metric_types", []) if str(m).strip()]
SYSTEM_NAME = os.getenv("SYSTEM_NAME", PLATFORM_PROFILE.get("system", "E-COMMERCE"))
DEFAULT_NAMESPACE = os.getenv("DEFAULT_NAMESPACE", PLATFORM_PROFILE.get("default_namespace", "production"))
DEFAULT_DEPLOYMENT_TEMPLATE = os.getenv(
    "DEFAULT_DEPLOYMENT_TEMPLATE",
    PLATFORM_PROFILE.get("default_deployment_template", "deployment/{{target_service}}"),
)
DEFAULT_SERVICE = os.getenv("DEFAULT_SERVICE", PLATFORM_PROFILE.get("default_service", SERVICES_LIST[0] if SERVICES_LIST else "service"))
ALLOWED_NAMESPACES = [
    ns.strip()
    for ns in os.getenv(
        "ALLOWED_NAMESPACES",
        ",".join(PLATFORM_PROFILE.get("allowed_namespaces", [DEFAULT_NAMESPACE, "default"])),
    ).split(",")
    if ns.strip()
]

# Server-side telemetry source configuration.
# - bench keeps deterministic benchmark_fixture datasets.
# - production reads telemetry from Kubernetes pods and Prometheus/Loki-compatible backends.
TELEMETRY_RUNTIME_MODE = os.getenv("TELEMETRY_RUNTIME_MODE", "bench").strip().lower()
BENCH_TELEMETRY_SOURCE_KIND = os.getenv("BENCH_TELEMETRY_SOURCE_KIND", "benchmark_fixture").strip()
PRODUCTION_TELEMETRY_SOURCE_KIND = os.getenv("PRODUCTION_TELEMETRY_SOURCE_KIND", "k8s").strip()
_runtime_default_telemetry_source_kind = (
    PRODUCTION_TELEMETRY_SOURCE_KIND if TELEMETRY_RUNTIME_MODE == "production" else BENCH_TELEMETRY_SOURCE_KIND
)
DEFAULT_TELEMETRY_SOURCE_KIND = (
    os.getenv("DEFAULT_TELEMETRY_SOURCE_KIND", "").strip() or _runtime_default_telemetry_source_kind
)

# Kubernetes telemetry reader settings. AI Engine only reads telemetry; it must not
# mutate Kubernetes resources. Request telemetry_source fields can override these.
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE", DEFAULT_NAMESPACE)
K8S_CONTEXT = os.getenv("K8S_CONTEXT", "").strip()
K8S_IN_CLUSTER = os.getenv("K8S_IN_CLUSTER", "False").lower() == "true"
K8S_LABEL_SELECTOR = os.getenv("K8S_LABEL_SELECTOR", "").strip()
K8S_SERVICE_LABEL_KEYS = _parse_csv(os.getenv("K8S_SERVICE_LABEL_KEYS", "app.kubernetes.io/name,app,service,k8s-app"))
K8S_CONTAINER_NAMES = _parse_csv(os.getenv("K8S_CONTAINER_NAMES", ""))
K8S_LOG_SINCE_SECONDS = int(os.getenv("K8S_LOG_SINCE_SECONDS", "300"))
K8S_LOG_TAIL_LINES = int(os.getenv("K8S_LOG_TAIL_LINES", "500"))
K8S_METRICS_PROVIDER = os.getenv("K8S_METRICS_PROVIDER", "prometheus").strip().lower()
K8S_METRIC_WINDOW_SECONDS = int(os.getenv("K8S_METRIC_WINDOW_SECONDS", "300"))

# Prometheus/Loki endpoints for production telemetry. Prometheus is used for
# metric time series; Kubernetes API is used for pod logs in k8s mode.
PROMETHEUS_BASE_URL = os.getenv("PROMETHEUS_BASE_URL", "").rstrip("/")
PROMETHEUS_QUERY_STEP_SECONDS = int(os.getenv("PROMETHEUS_QUERY_STEP_SECONDS", "15"))
PROMETHEUS_REQUEST_TIMEOUT_SECONDS = int(os.getenv("PROMETHEUS_REQUEST_TIMEOUT_SECONDS", "10"))
LOKI_BASE_URL = os.getenv("LOKI_BASE_URL", "").rstrip("/")

# Decide — fault type → runbook mapping. Fault candidates come from
# platform_profile.metric_types so each CDO team can define its own catalog.
FAULT_RUNBOOK_MAPPING = PLATFORM_PROFILE.get("fault_runbook_mapping") or {}
FAULT_TYPE_CATALOG = [fault for fault in METRIC_TYPES_LIST if fault in FAULT_RUNBOOK_MAPPING]
DEPENDENCY_GRAPH = PLATFORM_PROFILE.get("dependency_graph", {})

# LLM Configurable Parameters
USE_LLM_DECISION = os.getenv("USE_LLM_DECISION", "False").lower() == "true"
USE_LLM_FAULT_TYPE = os.getenv("USE_LLM_FAULT_TYPE", "False").lower() == "true"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")

# OpenAI Configurations
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")

# Anthropic Configurations
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")

# AWS Bedrock Configurations
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "")

