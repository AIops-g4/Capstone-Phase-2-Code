from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Self-Heal AI Engine"
    API_V1_STR: str = "/v1"
    BEDROCK_REGION: str = "us-east-1"

    # Cost cap for Bedrock per tenant (per Deployment Contract §4C)
    COST_CAP_PER_TENANT_USD: float = 50.0
    COST_ALERT_THRESHOLD_PCT: float = 0.80  # Alert at 80% ($40)

    # RRCF Configuration
    RRCF_NUM_TREES: int = 40
    RRCF_TREE_SIZE: int = 256
    RRCF_SHINGLE_SIZE: int = 4

    # Circuit Breaker Configuration (per design: >3 actions/5min per tenant)
    CIRCUIT_BREAKER_MAX_ACTIONS: int = 3
    CIRCUIT_BREAKER_WINDOW_SECONDS: int = 300  # 5 minutes

    # Audit Trail Configuration
    AUDIT_S3_BUCKET: str = "tf-3-aiops-audit-trail"
    AUDIT_RETENTION_DAYS: int = 90

    # Bedrock timeout budget (ms)
    BEDROCK_TIMEOUT_MS: int = 2500

    # DynamoDB table for idempotency locks
    DYNAMODB_LOCK_TABLE: str = "tf-3-idempotency-locks"

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")


settings = Settings()
