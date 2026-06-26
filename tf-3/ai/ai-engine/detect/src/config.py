import os

# Define package and dotenv paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AI_ENGINE_DIR = os.path.dirname(SCRIPT_DIR)
DOTENV_PATH = os.path.join(AI_ENGINE_DIR, ".env")

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

# File Paths
DATASET_DIR = os.getenv("DATASET_DIR", os.path.join(AI_ENGINE_DIR, "dataset"))
GROUND_TRUTH_PATH = os.getenv("GROUND_TRUTH_PATH", os.path.join(DATASET_DIR, "ground_truth.json"))
RUNBOOKS_PATH = os.getenv("RUNBOOKS_PATH", os.path.join(DATASET_DIR, "runbooks.json"))

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
USE_BARO_RCA = os.getenv("USE_BARO_RCA", "False").lower() == "true"
BARO_TOP_K = int(os.getenv("BARO_TOP_K", "3"))

# RRCF Anomaly Detection Configuration
USE_RRCF = os.getenv("USE_RRCF", "False").lower() == "true"
USE_BOCPD = os.getenv("USE_BOCPD", "False").lower() == "true"
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

