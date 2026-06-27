from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID
from enum import Enum
from .common import AnomalyContext

class PatternType(str, Enum):
    urgent = "urgent"
    deferred = "deferred"

class ActionType(str, Enum):
    RESTART_DEPLOYMENT = "RESTART_DEPLOYMENT"
    PATCH_MEMORY_LIMIT = "PATCH_MEMORY_LIMIT"
    SCALE_REPLICAS = "SCALE_REPLICAS"
    ROLLOUT_UNDO = "ROLLOUT_UNDO"
    ROTATE_SECRET = "ROTATE_SECRET"

class ActionParams(BaseModel):
    """Parameters for each action step per AI API Contract §3.2."""
    namespace: str
    container: Optional[str] = None
    memory_request_mb: Optional[int] = None
    memory_limit_mb: Optional[int] = None
    replicas: Optional[int] = None
    secret_name: Optional[str] = None
    grace_period_seconds: Optional[int] = None

class ActionPlanStep(BaseModel):
    step: int
    action: ActionType
    target: str
    params: ActionParams

class BlastRadiusConfig(BaseModel):
    max_pod_impact_pct: int
    circuit_breaker_error_rate: float
    allowed_namespaces: List[str]

class VerifyPolicy(BaseModel):
    window_seconds: int
    success_conditions: Optional[List[str]] = None

class DecideRequest(BaseModel):
    """Request schema per AI API Contract §3.2 DecideRequest.
    idempotency_key and correlation_id use UUID v4 format to avoid duplication."""
    correlation_id: UUID = Field(description="UUID v4 liên kết từ /v1/detect")
    idempotency_key: UUID = Field(description="Khóa chống trùng lặp - UUID v4")
    dry_run_mode: bool
    anomaly_context: AnomalyContext

    class Config:
        extra = "forbid"

class DecideResponse(BaseModel):
    """Response schema per AI API Contract §3.2 DecideResponse."""
    matched_runbook: str
    pattern_type: PatternType
    action_plan: List[ActionPlanStep]
    blast_radius_config: BlastRadiusConfig
    verify_policy: VerifyPolicy
    correlation_id: UUID
    idempotency_key: UUID
    dry_run_mode: bool
    cost_cap_exceeded: Optional[bool] = None

    class Config:
        extra = "forbid"
