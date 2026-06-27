from fastapi import APIRouter, Header
from app.schemas.detect import DetectRequest, DetectResponse
from app.schemas.common import AnomalyContext
import uuid

router = APIRouter()

@router.post("/detect", response_model=DetectResponse)
async def detect_anomaly(
    request: DetectRequest,
    x_tenant_id: str = Header(..., description="Định danh duy nhất của Tenant"),
):
    """
    Endpoint Phát hiện Bất thường: Nhận dữ liệu telemetry thời gian thực, 
    thực thi mô hình phát hiện bất thường và đánh giá mức độ nghiêm trọng.
    """
    # Mock response based on the API contract
    return DetectResponse(
        anomaly_detected=True,
        severity=0.85,
        anomaly_context=AnomalyContext(
            target_service="order-service",
            suspected_fault_type="database_connection_failure",
            system="E-COMMERCE"
        ),
        confidence=0.92,
        reasoning="Mocked: Error rate exceeds safe limits",
        correlation_id=request.correlation_id or uuid.uuid4()
    )
