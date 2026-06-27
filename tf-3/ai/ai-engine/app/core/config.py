from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "Self-Heal AI Engine"
    API_V1_STR: str = "/v1"
    BEDROCK_REGION: str = "us-east-1"
    
    # Cost cap for Bedrock per tenant
    COST_CAP_PER_TENANT_USD: float = 50.0

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

settings = Settings()
