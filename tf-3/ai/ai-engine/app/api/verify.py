"""
POST /v1/verify — Post-Action Verification Endpoint.
Per AI API Contract §3.3: Evaluates remediation success against pattern-specific conditions.
Includes tenant validation, escalation bundle generation, and audit logging.
"""
from fastapi import APIRouter, Header, HTTPException
from app.schemas.verify import VerifyRequest, VerifyResponse, NextAction, EscalationBundle
from app.verifier.evaluator import VerificationEvaluator
from app.verifier.escalation import EscalationBundleGenerator
from app.safety.tenant_validator import TenantValidator
from app.audit.writer import AuditWriter
from app.audit.schema import AuditRecord
from datetime import datetime, timezone
import time
import logging

router = APIRouter()
evaluator = VerificationEvaluator()
escalation_gen = EscalationBundleGenerator()
tenant_validator = TenantValidator()
audit_writer = AuditWriter()
logger = logging.getLogger(__name__)


from typing import Optional

@router.post("/verify", response_model=VerifyResponse)
async def verify_action(
    request: VerifyRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id", description="Unique tenant identifier (UUID v4)"),
    x_correlation_id: str = Header(..., alias="X-Correlation-Id", description="Correlation ID (UUID v4)"),
    idempotency_key: str = Header(..., alias="Idempotency-Key", description="Idempotency key (UUID v4)"),
    x_dry_run_mode: str = Header(..., alias="X-Dry-Run-Mode", description="Dry run mode ('true' or 'false')"),
):
    """
    Post-Action Verification Endpoint: Evaluates remediation effectiveness
    based on post-action telemetry data.
    Per AI API Contract §3.3.
    """
    start_time = time.time()

    # Validate headers strictly per contract
    try:
        from uuid import UUID
        # Validate UUID format for X-Tenant-Id
        UUID(x_tenant_id)
        # Validate UUID format and match for X-Correlation-Id
        header_corr = UUID(x_correlation_id)
        if header_corr != request.correlation_id:
            raise HTTPException(status_code=400, detail="X-Correlation-Id header does not match body correlation_id.")
        # Validate UUID format and match for Idempotency-Key
        header_idem = UUID(idempotency_key)
        if header_idem != request.idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header does not match body idempotency_key.")
        # Validate X-Dry-Run-Mode format and match
        if x_dry_run_mode.lower() not in ("true", "false"):
            raise HTTPException(status_code=400, detail="X-Dry-Run-Mode header must be 'true' or 'false'.")
        header_dry = x_dry_run_mode.lower() == "true"
        if header_dry != request.dry_run_mode:
            raise HTTPException(status_code=400, detail="X-Dry-Run-Mode header does not match body dry_run_mode.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Header validation failed: {str(e)}")

    # 1. Tenant isolation validation (403 on mismatch)
    tenant_validator.validate_verify(x_tenant_id, request.post_telemetry_window)


    # 2. If the action itself failed at CDO side, escalate immediately
    if request.action_executed.status == "FAILED":
        bundle = escalation_gen.generate(
            fault_type="unknown",
            target_service=request.action_executed.target,
            action_executed={
                "action": request.action_executed.action,
                "target": request.action_executed.target,
                "status": "FAILED",
            },
            error_logs=[f"Action {request.action_executed.action} failed at CDO executor."],
            metrics_summary={"action_status": "FAILED"},
        )
        response = VerifyResponse(
            success=False,
            regression_detected=False,
            next_action=NextAction.ESCALATE,
            escalation_bundle=EscalationBundle(**bundle),
        )
        _write_audit(request, response, x_tenant_id, start_time)
        return response

    # 3. Evaluate post-action telemetry using pattern-specific conditions
    # We need the fault_type — extract from action context or use a default
    fault_type = _infer_fault_type(request.action_executed.action)
    success, has_regression, error_logs, metrics_summary = evaluator.evaluate(
        fault_type=fault_type,
        post_telemetry=request.post_telemetry_window,
    )

    # 4. Decision logic: DONE / RETRY / ROLLBACK / ESCALATE
    if success:
        response = VerifyResponse(
            success=True,
            regression_detected=False,
            next_action=NextAction.DONE,
        )
    elif has_regression:
        bundle = escalation_gen.generate(
            fault_type=fault_type,
            target_service=request.action_executed.target,
            action_executed={
                "action": request.action_executed.action,
                "target": request.action_executed.target,
                "status": request.action_executed.status,
            },
            error_logs=error_logs,
            metrics_summary=metrics_summary,
        )
        response = VerifyResponse(
            success=False,
            regression_detected=True,
            next_action=NextAction.ROLLBACK,
            escalation_bundle=EscalationBundle(**bundle),
        )
    else:
        # Not recovered but no regression — escalate
        bundle = escalation_gen.generate(
            fault_type=fault_type,
            target_service=request.action_executed.target,
            action_executed={
                "action": request.action_executed.action,
                "target": request.action_executed.target,
                "status": request.action_executed.status,
            },
            error_logs=error_logs,
            metrics_summary=metrics_summary,
        )
        response = VerifyResponse(
            success=False,
            regression_detected=False,
            next_action=NextAction.ESCALATE,
            escalation_bundle=EscalationBundle(**bundle),
        )

    # 5. Write audit record
    _write_audit(request, response, x_tenant_id, start_time)
    return response


def _infer_fault_type(action: str) -> str:
    """Infers suspected_fault_type from the action that was executed."""
    action_to_fault = {
        "PATCH_MEMORY_LIMIT": "pod_oom_event",
        "RESTART_DEPLOYMENT": "service_unhealthy",
        "SCALE_REPLICAS": "queue_backlog",
        "ROLLOUT_UNDO": "crash_loop",
        "ROTATE_SECRET": "secret_expiry_warning",
    }
    return action_to_fault.get(action, "unknown")


def _write_audit(request: VerifyRequest, response: VerifyResponse, tenant_id: str, start_time: float):
    """Writes audit record for /v1/verify call."""
    latency_ms = int((time.time() - start_time) * 1000)
    try:
        audit_writer.write(AuditRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            correlation_id=str(request.correlation_id),
            idempotency_key=str(request.idempotency_key),
            tenant_id=tenant_id,
            endpoint="/v1/verify",
            dry_run_mode=request.dry_run_mode,
            request_body=request.model_dump(mode="json"),
            response_body=response.model_dump(mode="json"),
            response_status_code=200,
            latency_ms=latency_ms,
            decision_path="rule_based_evaluation",
            model_version="verify-v1.0.0",
            safety_checks_passed=["tenant_isolation", "schema_validation"],
        ))
    except Exception as e:
        logger.error(f"Audit write failed for /v1/verify: {e}")
        raise HTTPException(status_code=500, detail="Audit trail write failed.")
