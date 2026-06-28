"""
Component 5 — Audit Record Schema.
Per Deployment Contract §4: Structured JSON audit records for S3 Object Lock.
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime


class AuditRecord(BaseModel):
    """Structured audit record per design document Component 5."""
    audit_version: str = "1.0"
    timestamp: str = Field(description="RFC3339 UTC timestamp of the API call")
    correlation_id: str
    idempotency_key: str
    tenant_id: str
    endpoint: str = Field(description="/v1/detect, /v1/decide, or /v1/verify")
    dry_run_mode: bool
    request_body: Dict[str, Any] = Field(description="Full request payload")
    response_body: Dict[str, Any] = Field(description="Full response payload")
    response_status_code: int
    latency_ms: int
    decision_path: Optional[str] = Field(
        default=None,
        description="statistical_model | llm_bedrock | fallback_rule_based"
    )
    model_version: Optional[str] = None
    matched_runbook: Optional[str] = None
    pattern_type: Optional[str] = None
    cost_cap_exceeded: Optional[bool] = None
    idempotency_lock_acquired: Optional[bool] = None
    safety_checks_passed: Optional[List[str]] = None
