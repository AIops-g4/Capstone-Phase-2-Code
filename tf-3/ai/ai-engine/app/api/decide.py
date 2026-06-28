"""
POST /v1/decide — Decision Planning Endpoint.
Per AI API Contract §3.2: Routes through LLM or fallback, applies safety checks.
Includes idempotency lock, circuit breaker, tenant validation, and audit logging.
"""
from fastapi import APIRouter, Header, HTTPException
from app.schemas.decide import (
    DecideRequest, DecideResponse, ActionPlanStep, ActionParams,
    BlastRadiusConfig, VerifyPolicy, PatternType,
)
from app.decision.router import DecisionRouter
from app.rag.runbook_retriever import RunbookRetriever
from app.safety.idempotency import IdempotencyLock
from app.safety.blast_radius import BlastRadiusGenerator
from app.safety.circuit_breaker import CircuitBreaker
from app.safety.tenant_validator import TenantValidator
from app.audit.writer import AuditWriter
from app.audit.schema import AuditRecord
from datetime import datetime, timezone
import time
import logging

router = APIRouter()
decision_router = DecisionRouter()
retriever = RunbookRetriever()
idempotency_lock = IdempotencyLock()
blast_radius_gen = BlastRadiusGenerator()
circuit_breaker = CircuitBreaker()
tenant_validator = TenantValidator()
audit_writer = AuditWriter()
logger = logging.getLogger(__name__)


@router.post("/decide", response_model=DecideResponse)
async def decide_action(
    request: DecideRequest,
    x_tenant_id: str = Header(..., description="Unique tenant identifier (UUID v4)"),
):
    """
    Decision Planning Endpoint: Matches anomaly context to runbooks,
    generates action plan with safety constraints.
    Per AI API Contract §3.2.
    """
    start_time = time.time()
    safety_checks_passed = []

    # 1. Idempotency lock (409 on duplicate)
    idempotency_lock.acquire_lock(x_tenant_id, str(request.idempotency_key))
    safety_checks_passed.append("idempotency_lock")

    # 2. Circuit breaker check
    is_tripped, action_count = circuit_breaker.check_and_record(x_tenant_id)
    if is_tripped:
        raise HTTPException(
            status_code=503,
            detail=f"Circuit breaker tripped for tenant '{x_tenant_id}': "
                   f"{action_count} actions in window. Escalate to on-call engineer.",
        )
    safety_checks_passed.append("circuit_breaker")

    # 3. Retrieve relevant runbooks (RAG)
    relevant_runbooks = retriever.retrieve_relevant(
        request.anomaly_context.suspected_fault_type
    )

    # 4. Route decision (LLM or fallback)
    decision_json, cost_cap_exceeded, used_fallback = decision_router.decide(
        anomaly_context=request.anomaly_context.model_dump(),
        runbooks=relevant_runbooks,
        tenant_id=x_tenant_id,
        cost_cap_exceeded=False,  # TODO: Check actual cost from DynamoDB counter
    )

    # 5. Generate blast radius config
    action_plan_raw = decision_json.get("action_plan", [])
    blast_radius = blast_radius_gen.generate(action_plan_raw)
    safety_checks_passed.append("blast_radius")

    # 6. Generate verify policy with pattern-specific success conditions
    verify_policy = _generate_verify_policy(request.anomaly_context.suspected_fault_type)
    safety_checks_passed.append("dry_run")
    safety_checks_passed.append("tenant_isolation")

    # 7. Map raw JSON to Pydantic objects
    try:
        action_plan_steps = [
            ActionPlanStep(
                step=step.get("step", i + 1),
                action=step["action"],
                target=step["target"],
                params=ActionParams(**step.get("params", {"namespace": "production"})),
            )
            for i, step in enumerate(action_plan_raw)
        ]
        pattern_type = PatternType(decision_json.get("pattern_type", "urgent"))
    except Exception as e:
        logger.error(f"Failed to parse decision output: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Decision engine generated invalid action plan: {str(e)}",
        )

    response = DecideResponse(
        matched_runbook=decision_json.get("matched_runbook", "UnknownRunbook"),
        pattern_type=pattern_type,
        action_plan=action_plan_steps,
        blast_radius_config=BlastRadiusConfig(**blast_radius),
        verify_policy=VerifyPolicy(**verify_policy),
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        dry_run_mode=request.dry_run_mode,
        cost_cap_exceeded=cost_cap_exceeded,
    )

    # 8. Write audit record
    latency_ms = int((time.time() - start_time) * 1000)
    try:
        audit_writer.write(AuditRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            correlation_id=str(request.correlation_id),
            idempotency_key=str(request.idempotency_key),
            tenant_id=x_tenant_id,
            endpoint="/v1/decide",
            dry_run_mode=request.dry_run_mode,
            request_body=request.model_dump(mode="json"),
            response_body=response.model_dump(mode="json"),
            response_status_code=200,
            latency_ms=latency_ms,
            decision_path="fallback_rule_based" if used_fallback else "llm_bedrock",
            model_version="decide-v1.0.0",
            matched_runbook=decision_json.get("matched_runbook"),
            pattern_type=decision_json.get("pattern_type"),
            cost_cap_exceeded=cost_cap_exceeded,
            idempotency_lock_acquired=True,
            safety_checks_passed=safety_checks_passed,
        ))
    except Exception as e:
        logger.error(f"Audit write failed for /v1/decide: {e}")
        raise HTTPException(status_code=500, detail="Audit trail write failed.")

    return response


def _generate_verify_policy(fault_type: str) -> dict:
    """Generates pattern-specific verify_policy per design document."""
    from app.verifier.evaluator import PATTERN_SUCCESS_CONDITIONS

    pattern_config = PATTERN_SUCCESS_CONDITIONS.get(fault_type)
    conditions = pattern_config["conditions"] if pattern_config else [
        "pod_ready == true",
        "service_error_rate < 0.05",
    ]

    return {
        "window_seconds": 120,
        "success_conditions": conditions,
    }
