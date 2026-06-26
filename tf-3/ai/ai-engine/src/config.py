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
