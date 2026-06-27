from fastapi import APIRouter, Header, HTTPException
from app.schemas.decide import DecideRequest, DecideResponse, ActionPlanStep, BlastRadiusConfig, VerifyPolicy, PatternType
from app.decision.bedrock_client import BedrockDecisionEngine
from app.rag.runbook_retriever import RunbookRetriever
from app.safety.guardrails import SafetyGuardrails

router = APIRouter()
decision_engine = BedrockDecisionEngine()
retriever = RunbookRetriever()
guardrails = SafetyGuardrails()

@router.post("/decide", response_model=DecideResponse)
async def decide_action(
    request: DecideRequest,
    x_tenant_id: str = Header(..., description="Định danh duy nhất của Tenant"),
):
    """
    Endpoint Lập Kế hoạch: Đối chiếu ngữ cảnh lỗi với thư viện Runbook 
    để đưa ra kịch bản khắc phục tuần tự (Action Plan) cùng các giới hạn an toàn.
    """
    # 1. Retrieve relevant runbooks (RAG)
    relevant_runbooks = retriever.retrieve_relevant(request.anomaly_context.suspected_fault_type)
    
    # 2. Invoke Bedrock for Decision
    decision_json = decision_engine.decide(request.anomaly_context.model_dump(), relevant_runbooks)
    
    # 3. Apply Safety Guardrails
    blast_radius = guardrails.generate_blast_radius(decision_json, x_tenant_id)
    verify_policy = guardrails.generate_verify_policy()
    
    # Map raw JSON to Pydantic objects
    try:
        action_plan_steps = [ActionPlanStep(**step) for step in decision_json.get("action_plan", [])]
        pattern_type = PatternType(decision_json.get("pattern_type", "urgent"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generated invalid action plan: {str(e)}")

    return DecideResponse(
        matched_runbook=decision_json.get("matched_runbook", "UnknownRunbook"),
        pattern_type=pattern_type,
        action_plan=action_plan_steps,
        blast_radius_config=BlastRadiusConfig(**blast_radius),
        verify_policy=VerifyPolicy(**verify_policy),
        correlation_id=request.correlation_id,
        idempotency_key=request.idempotency_key,
        dry_run_mode=request.dry_run_mode,
        cost_cap_exceeded=False
    )
