from pydantic import BaseModel, Field
from typing import Optional, Dict, Union

class TelemetryLabels(BaseModel):
    """Labels object per Telemetry Contract §3 - supports all defined label fields."""
    system: str = Field(description="Tên hệ thống nghiệp vụ (bắt buộc)")
    namespace: Optional[str] = Field(default=None, description="Kubernetes namespace")
    deployment: Optional[str] = Field(default=None, description="Tên Kubernetes Deployment")
    pod_name: Optional[str] = Field(default=None, description="Tên pod cụ thể")
    container: Optional[str] = Field(default=None, description="Tên container cụ thể")
    endpoint: Optional[str] = Field(default=None, description="API endpoint hoặc gRPC method")
    trace_id: Optional[str] = Field(default=None, description="Trace ID liên kết vết lỗi")
    span_id: Optional[str] = Field(default=None, description="Span ID giao dịch cụ thể")
    operation: Optional[str] = Field(default=None, description="Tên giao dịch trace span")
    level: Optional[str] = Field(default=None, description="Log level: ERROR, WARNING, INFO")
    secret_name: Optional[str] = Field(default=None, description="Secret name for cert/key rotation")

    class Config:
        extra = "allow"  # Telemetry contract §3 allows additionalProperties: true

class AnomalyContext(BaseModel):
    """Anomaly context per AI API Contract §3.1 DetectResponse / §3.2 DecideRequest."""
    target_service: str
    suspected_fault_type: str
    system: str
    namespace: Optional[str] = None
    deployment: Optional[str] = None
    trigger_metric: Optional[str] = None
    trigger_value: Optional[float] = None

class TelemetryPoint(BaseModel):
    """Single telemetry data point per Telemetry Contract §3."""
    ts: str = Field(description="RFC3339 UTC timestamp")
    tenant_id: str = Field(description="UUID v4 tenant identifier")
    service: str = Field(description="Microservice name")
    signal_name: str = Field(description="Signal name from telemetry contract enum")
    value: Union[float, int, str] = Field(description="Metric value or log text")
    labels: Optional[TelemetryLabels] = Field(default=None, description="Topology labels")

    class Config:
        extra = "forbid"
