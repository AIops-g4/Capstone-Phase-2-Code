from fastapi import APIRouter, Header
from app.schemas.detect import DetectRequest, DetectResponse
from app.schemas.common import AnomalyContext
from app.detector.analyzer import TelemetryAnalyzer
import uuid

router = APIRouter()
analyzer = TelemetryAnalyzer(z_threshold=2.5)

# Signal-to-fault-type mapping per Telemetry Contract §4
SIGNAL_FAULT_MAP = {
    "service_error_rate": "service_error_spike",
    "service_latency_p95": "latency_degradation",
    "container_resource_usage": "memory_pressure",
    "application_log_event": "application_exception",
    "distributed_trace_error_event": "distributed_trace_failure",
    "pod_oom_event": "pod_oom_killed",
    "service_unhealthy": "service_health_check_failure",
    "queue_backlog": "queue_congestion",
    "service_throughput_rps": "throughput_anomaly",
    "container_restart_count": "crash_loop_backoff",
    "secret_expiry_warning": "certificate_expiring",
    "db_connection_pool_saturation": "database_connection_failure",
}

@router.post("/detect", response_model=DetectResponse)
async def detect_anomaly(
    request: DetectRequest,
    x_tenant_id: str = Header(..., description="Định danh duy nhất của Tenant"),
):
    """
    Endpoint Phát hiện Bất thường: Nhận dữ liệu telemetry thời gian thực, 
    thực thi mô hình phát hiện bất thường và đánh giá mức độ nghiêm trọng.
    """
    is_anomaly, severity, confidence, reasoning = analyzer.analyze(request.telemetry_window)
    
    context = None
    if is_anomaly and request.telemetry_window:
        # Extract context from telemetry - find the most relevant trigger point
        trigger_point = request.telemetry_window[-1]
        
        # Extract system, namespace, deployment from telemetry labels (matching demo output)
        system = "UNKNOWN"
        namespace = None
        deployment = None
        container = None
        
        if trigger_point.labels:
            system = trigger_point.labels.system or "UNKNOWN"
            namespace = trigger_point.labels.namespace
            deployment = trigger_point.labels.deployment
            container = trigger_point.labels.container
        
        # Map signal_name to suspected_fault_type using telemetry contract signals
        suspected_fault_type = SIGNAL_FAULT_MAP.get(
            trigger_point.signal_name, 
            "statistical_anomaly"
        )
        
        # Get trigger value (numeric only)
        trigger_value = None
        try:
            trigger_value = float(trigger_point.value) if isinstance(trigger_point.value, (int, float)) else None
        except (ValueError, TypeError):
            pass
        
        context = AnomalyContext(
            target_service=trigger_point.service,
            suspected_fault_type=suspected_fault_type,
            system=system,
            namespace=namespace,
            deployment=deployment,
            trigger_metric=trigger_point.signal_name,
            trigger_value=trigger_value
        )
        
    return DetectResponse(
        anomaly_detected=is_anomaly,
        severity=severity,
        anomaly_context=context,
        confidence=confidence,
        reasoning=reasoning,
        correlation_id=request.correlation_id or uuid.uuid4()
    )
