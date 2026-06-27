from fastapi import APIRouter, Header
from app.schemas.decide import DecideRequest, DecideResponse, ActionPlanStep, ActionType, ActionParams, BlastRadiusConfig, VerifyPolicy, PatternType

router = APIRouter()

@router.post("/decide", response_model=DecideResponse)
async def decide_action(
    request: DecideRequest,
    x_tenant_id: str = Header(..., description="Định danh duy nhất của Tenant"),
):
    """
    Endpoint Lập Kế hoạch: Đối chiếu ngữ cảnh lỗi với thư viện Runbook 
    để đưa ra kịch bản khắc phục tuần tự (Action Plan) cùng các giới hạn an toàn.
    """
    return DecideResponse(
        matched_runbook="DatabaseConnectionRecoveryRunbook",
        pattern_type=PatternType.urgent,
        action_plan=[
            ActionPlanStep(
                step=1,
                action=ActionType.PATCH_MEMORY_LIMIT,
                target="deployment/order-service",
                params=ActionParams(namespace="production", memory_request_mb=512, memory_limit_mb=768)
            )
        ],
        blast_radius_config=BlastRadiusConfig(
            max_pod_impact_pct=25,
            circuit_breaker_error_rate=0.20,
            allowed_namespaces=["production"]
        ),
        verify_policy=VerifyPolicy(
            window_seconds=120
        ),
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        dry_run_mode=request.dry_run_mode,
        cost_cap_exceeded=False
    )
