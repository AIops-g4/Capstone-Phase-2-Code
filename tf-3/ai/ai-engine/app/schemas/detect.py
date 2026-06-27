from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID
from .common import TelemetryPoint, AnomalyContext

class DetectRequest(BaseModel):
    correlation_id: Optional[UUID] = Field(default=None, description="UUID v4 liên kết chuỗi vết lỗi")
    idempotency_key: UUID = Field(description="Khóa chống trùng lặp")
    dry_run_mode: bool = Field(description="Chế độ chạy thử nghiệm")
    telemetry_window: List[TelemetryPoint]
    
    class Config:
        extra = "forbid"

class DetectResponse(BaseModel):
    anomaly_detected: bool
    severity: float = Field(ge=0.0, le=1.0)
    anomaly_context: Optional[AnomalyContext] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=300)
    correlation_id: UUID

    class Config:
        extra = "forbid"
