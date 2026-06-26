from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AnomalyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_service: str
    suspected_fault_type: str
    system: str = "E-COMMERCE"
    namespace: Optional[str] = "production"
    deployment: Optional[str] = None
    trigger_metric: Optional[str] = None
    trigger_value: Optional[float] = None


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    anomaly_context: AnomalyContext


class ActionPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int
    action: Literal[
        "RESTART_DEPLOYMENT",
        "PATCH_MEMORY_LIMIT",
        "SCALE_REPLICAS",
        "ROLLOUT_UNDO",
        "ROTATE_SECRET",
    ]
    target: str
    params: Dict[str, Any]


class BlastRadiusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_pod_impact_pct: int
    circuit_breaker_error_rate: float
    allowed_namespaces: List[str]


class VerifyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_seconds: int
    success_conditions: Optional[List[str]] = None


class DecideResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matched_runbook: str
    pattern_type: Literal["urgent", "deferred"]
    action_plan: List[ActionPlanStep]
    blast_radius_config: BlastRadiusConfig
    verify_policy: VerifyPolicy
    correlation_id: str
    idempotency_key: str
    dry_run_mode: bool
    cost_cap_exceeded: bool = False
