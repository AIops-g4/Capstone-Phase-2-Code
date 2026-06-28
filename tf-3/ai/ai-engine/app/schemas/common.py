from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Union
from uuid import UUID
from enum import Enum
import re

# ISO 8601 / RFC 3339 datetime pattern with optional milliseconds/microseconds and strict UTC 'Z' or offset
RFC3339_REGEX = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$')

class SignalName(str, Enum):
    service_error_rate = "service_error_rate"
    service_latency_p95 = "service_latency_p95"
    container_resource_usage = "container_resource_usage"
    application_log_event = "application_log_event"
    distributed_trace_error_event = "distributed_trace_error_event"
    pod_oom_event = "pod_oom_event"
    service_unhealthy = "service_unhealthy"
    queue_backlog = "queue_backlog"
    service_throughput_rps = "service_throughput_rps"
    container_restart_count = "container_restart_count"
    secret_expiry_warning = "secret_expiry_warning"
    db_connection_pool_saturation = "db_connection_pool_saturation"

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
    tenant_id: UUID = Field(description="UUID v4 tenant identifier")
    service: str = Field(description="Microservice name")
    signal_name: SignalName = Field(description="Signal name from telemetry contract enum")
    value: Union[float, int, str] = Field(description="Metric value or log text")
    labels: Optional[TelemetryLabels] = Field(default=None, description="Topology labels")

    @field_validator('ts')
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        if not RFC3339_REGEX.match(v):
            raise ValueError("Timestamp must comply with RFC3339 format (e.g. 2026-06-25T10:30:00.123Z)")
        return v

    class Config:
        extra = "forbid"

