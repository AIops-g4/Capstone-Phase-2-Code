"""
POST /v1/detect — Anomaly Detection Endpoint.
Per AI API Contract §3.1: Receives telemetry, runs hybrid Rule+RRCF detection.
Includes tenant validation, audit logging, and contract-compliant response.
"""
from fastapi import APIRouter, Header, HTTPException
from app.schemas.detect import DetectRequest, DetectResponse
from app.detector.aggregator import DetectionAggregator
from app.safety.tenant_validator import TenantValidator
from app.audit.writer import AuditWriter
from app.audit.schema import AuditRecord
from datetime import datetime, timezone
import uuid
import time
import logging

router = APIRouter()
aggregator = DetectionAggregator()
tenant_validator = TenantValidator()
audit_writer = AuditWriter()
logger = logging.getLogger(__name__)


@router.post("/detect", response_model=DetectResponse)
async def detect_anomaly(
    request: DetectRequest,
    x_tenant_id: str = Header(..., description="Unique tenant identifier (UUID v4)"),
):
    """
    Anomaly Detection Endpoint: Receives real-time telemetry data,
    runs hybrid Rule-Based + RRCF anomaly detection, and returns severity assessment.
    Per AI API Contract §3.1.
    """
    start_time = time.time()

    # 1. Tenant isolation validation (403 on mismatch)
    tenant_validator.validate_detect(x_tenant_id, request.telemetry_window)

    # 2. Run hybrid detection pipeline (Rule Engine + RRCF)
    is_anomaly, severity, confidence, reasoning, anomaly_context = aggregator.analyze(
        request.telemetry_window
    )

    # 3. Build response
    correlation_id = request.correlation_id or uuid.uuid4()
    response = DetectResponse(
        anomaly_detected=is_anomaly,
        severity=severity,
        anomaly_context=anomaly_context,
        confidence=confidence,
        reasoning=reasoning,
        correlation_id=correlation_id,
    )

    # 4. Write audit record (synchronous, fail-closed)
    latency_ms = int((time.time() - start_time) * 1000)
    try:
        audit_writer.write(AuditRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            correlation_id=str(correlation_id),
            idempotency_key=str(request.idempotency_key),
            tenant_id=x_tenant_id,
            endpoint="/v1/detect",
            dry_run_mode=request.dry_run_mode,
            request_body=request.model_dump(mode="json"),
            response_body=response.model_dump(mode="json"),
            response_status_code=200,
            latency_ms=latency_ms,
            decision_path="hybrid_rule_rrcf",
            model_version="detect-v1.0.0",
            safety_checks_passed=["tenant_isolation", "schema_validation"],
        ))
    except Exception as e:
        logger.error(f"Audit write failed for /v1/detect: {e}")
        raise HTTPException(status_code=500, detail="Audit trail write failed.")

    return response
