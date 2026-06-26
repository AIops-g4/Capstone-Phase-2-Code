import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DECIDE_DIR = os.path.dirname(SCRIPT_DIR)
AI_ENGINE_DIR = os.path.dirname(DECIDE_DIR)
DOTENV_PATH = os.path.join(DECIDE_DIR, ".env")


def load_dotenv(dotenv_path: str) -> None:
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")


load_dotenv(DOTENV_PATH)


def _resolve_path(value: str, base_dir: str) -> str:
    if not os.path.isabs(value):
        return os.path.normpath(os.path.join(base_dir, value))
    return value


DATASET_DIR = _resolve_path(
    os.getenv("DATASET_DIR", os.path.join(AI_ENGINE_DIR, "dataset")),
    DECIDE_DIR,
)
GROUND_TRUTH_PATH = _resolve_path(
    os.getenv("GROUND_TRUTH_PATH", os.path.join(DATASET_DIR, "ground_truth.json")),
    DECIDE_DIR,
)
RUNBOOKS_PATH = _resolve_path(
    os.getenv("RUNBOOKS_PATH", os.path.join(DATASET_DIR, "runbooks.json")),
    DECIDE_DIR,
)

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8051"))

DECIDER_TYPE = os.getenv("DECIDER_TYPE", "rule-based").lower()
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)
BEDROCK_DAILY_CAP_USD = float(os.getenv("BEDROCK_DAILY_CAP_USD", "50.0"))

ALERT_HEALING_WINDOW_SECONDS = int(os.getenv("ALERT_HEALING_WINDOW_SECONDS", "120"))

FAULT_RUNBOOK_MAPPING = {
    # RE2 resource / network faults
    "cpu": "CPUSaturationRecoveryRunbook",
    "mem": "MemoryLeakRecoveryRunbook",
    "delay": "NetworkLatencyRecoveryRunbook",
    "loss": "PacketLossRecoveryRunbook",
    "disk": "DiskIORecoveryRunbook",
    "socket": "SocketExhaustionRecoveryRunbook",
    # RE3 code-level faults (RCAEval) — restart deployment to roll back buggy build
    "f1": "DefaultRecoveryRunbook",
    "f2": "DefaultRecoveryRunbook",
    "f3": "DefaultRecoveryRunbook",
    "f4": "DefaultRecoveryRunbook",
    "f5": "DefaultRecoveryRunbook",
}
