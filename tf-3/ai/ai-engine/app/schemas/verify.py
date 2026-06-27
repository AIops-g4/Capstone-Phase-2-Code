from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID
from enum import Enum
from .common import TelemetryPoint

class ActionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class ActionExecuted(BaseModel):
    action: str
    target: str
    status: ActionStatus
    execution_time_seconds: Optional[int] = None

class VerifyRequest(BaseModel):
    """Request schema per AI API Contract §3.3 VerifyRequest.
    idempotency_key and correlation_id use UUID v4 format to avoid duplication."""
    correlation_id: UUID = Field(description="UUID v4 truy vết chu trình tự chữa lành")
    idempotency_key: UUID = Field(description="Khóa chống trùng lặp - UUID v4")
    dry_run_mode: bool
    action_executed: ActionExecuted
    post_telemetry_window: List[TelemetryPoint]

    class Config:
        extra = "forbid"

class NextAction(str, Enum):
    DONE = "DONE"
    RETRY = "RETRY"
    ROLLBACK = "ROLLBACK"
    ESCALATE = "ESCALATE"

class EscalationBundle(BaseModel):
    reason: Optional[str] = None
    logs: Optional[List[str]] = None
    metrics: Optional[dict] = None

class VerifyResponse(BaseModel):
    """Response schema per AI API Contract §3.3 VerifyResponse."""
    success: bool
    regression_detected: bool
    next_action: NextAction
    escalation_bundle: Optional[EscalationBundle] = None

    class Config:
        extra = "forbid"
