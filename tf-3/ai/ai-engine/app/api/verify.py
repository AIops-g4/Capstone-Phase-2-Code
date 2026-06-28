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


@router.post("/verify", response_model=VerifyResponse)
async def verify_action(
    request: VerifyRequest,
    x_tenant_id: str = Header(..., description="Unique tenant identifier (UUID v4)"),
):
    """
    Post-Action Verification Endpoint: Evaluates remediation effectiveness
    based on post-action telemetry data.
    Per AI API Contract §3.3.
    """
    start_time = time.time()

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
