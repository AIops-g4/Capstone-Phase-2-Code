from fastapi import APIRouter, Header
from app.schemas.detect import DetectRequest, DetectResponse
from app.schemas.common import AnomalyContext
from app.detector.analyzer import TelemetryAnalyzer
import uuid

router = APIRouter()
analyzer = TelemetryAnalyzer(z_threshold=2.5)

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
        # Extract context from the triggering metric
        trigger_point = request.telemetry_window[-1]
        context = AnomalyContext(
            target_service=trigger_point.service,
            suspected_fault_type="statistical_anomaly",
            system="E-COMMERCE",
            trigger_metric=trigger_point.signal_name,
            trigger_value=float(trigger_point.value) if isinstance(trigger_point.value, (int, float)) else None
        )
        
    return DetectResponse(
        anomaly_detected=is_anomaly,
        severity=severity,
        anomaly_context=context,
        confidence=confidence,
        reasoning=reasoning,
        correlation_id=request.correlation_id or uuid.uuid4()
    )
