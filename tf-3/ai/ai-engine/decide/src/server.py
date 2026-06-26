import uuid
from typing import Dict

from fastapi import FastAPI, HTTPException, status

from src.alert_correlator import AlertCorrelationEngine
from src.config import ALERT_HEALING_WINDOW_SECONDS, API_HOST, API_PORT
from src.decider import DeciderService
from src.models import (
    BlastRadiusConfig,
    DecideRequest,
    DecideResponse,
    VerifyPolicy,
)

app = FastAPI(
    title="AIOps Decide Service",
    description="POST /v1/decide — runbook matching & action planning (TF3 contract).",
    version="1.0.0",
)

decider_service = DeciderService()
alert_correlator = AlertCorrelationEngine(ALERT_HEALING_WINDOW_SECONDS)
_idempotency_cache: Dict[str, dict] = {}


def _suppression_response(request: DecideRequest) -> DecideResponse:
    return DecideResponse(
        matched_runbook="CorrelatedSymptomSuppression",
        pattern_type="urgent",
        action_plan=[],
        blast_radius_config=BlastRadiusConfig(
            max_pod_impact_pct=0,
            circuit_breaker_error_rate=0.0,
            allowed_namespaces=["production"],
        ),
        verify_policy=VerifyPolicy(window_seconds=10, success_conditions=[]),
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        dry_run_mode=request.dry_run_mode,
        cost_cap_exceeded=False,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "decide"}


@app.post("/v1/decide", response_model=DecideResponse, response_model_exclude_none=True)
async def decide_action_plan(request: DecideRequest):
    cache_key = request.idempotency_key
    if cache_key in _idempotency_cache:
        cached = _idempotency_cache[cache_key]
        if cached.get("correlation_id") != request.correlation_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key reused with different correlation_id",
            )
        return DecideResponse(**cached)

    ctx = request.anomaly_context
    alert_correlator.register_incident(
        request.correlation_id, ctx.target_service, ctx.suspected_fault_type
    )

    suppressed, reason = alert_correlator.should_suppress_decide(
        request.correlation_id, ctx.target_service
    )
    if suppressed:
        response = _suppression_response(request)
        _idempotency_cache[cache_key] = response.model_dump()
        return response

    decision = decider_service.decide(ctx.model_dump())
    response = DecideResponse(
        matched_runbook=decision["matched_runbook"],
        pattern_type=decision["pattern_type"],
        action_plan=decision["action_plan"],
        blast_radius_config=decision["blast_radius_config"],
        verify_policy=decision["verify_policy"],
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        dry_run_mode=request.dry_run_mode,
        cost_cap_exceeded=decision.get("cost_cap_exceeded", False),
    )
    _idempotency_cache[cache_key] = response.model_dump()
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=API_HOST, port=API_PORT)
