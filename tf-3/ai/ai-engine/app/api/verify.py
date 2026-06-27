from fastapi import APIRouter, Header
from app.schemas.verify import VerifyRequest, VerifyResponse, NextAction, EscalationBundle
from app.schemas.common import TelemetryPoint
from typing import List
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Thresholds for post-action verification
ERROR_RATE_THRESHOLD = 0.05      # 5% error rate considered unhealthy
LATENCY_THRESHOLD_MS = 500.0      # P95 latency threshold
QUEUE_BACKLOG_THRESHOLD = 5000    # Queue backlog threshold for congestion

@router.post("/verify", response_model=VerifyResponse)
async def verify_action(
    request: VerifyRequest,
    x_tenant_id: str = Header(..., description="Định danh duy nhất của Tenant"),
):
    """
    Endpoint Xác thực: Đánh giá hiệu quả của hành động khắc phục lỗi 
    dựa trên dữ liệu telemetry thu được sau sự kiện.
    Per AI API Contract §3.3.
    """
    # If the action itself failed at CDO side, escalate immediately
    if request.action_executed.status == "FAILED":
        return VerifyResponse(
            success=False,
            regression_detected=False,
            next_action=NextAction.ESCALATE,
            escalation_bundle=EscalationBundle(
                reason=f"Action {request.action_executed.action} on {request.action_executed.target} failed at CDO executor.",
                logs=[f"Action status: FAILED for {request.action_executed.target}"],
                metrics={"action": request.action_executed.action, "status": "FAILED"}
            )
        )
    
    # Analyze post-action telemetry to determine success
    has_regression = False
    still_unhealthy = False
    error_logs = []
    metrics_summary = {}
    
    for point in request.post_telemetry_window:
        signal = point.signal_name
        
        # Check for persistent error signals
        if signal == "service_error_rate":
            try:
                val = float(point.value)
                metrics_summary["post_error_rate"] = val
                if val > ERROR_RATE_THRESHOLD:
                    still_unhealthy = True
                    error_logs.append(f"Error rate still elevated: {val:.2%} (threshold: {ERROR_RATE_THRESHOLD:.2%})")
            except (ValueError, TypeError):
                pass
                
        elif signal == "service_latency_p95":
            try:
                val = float(point.value)
                metrics_summary["post_latency_p95_ms"] = val
                if val > LATENCY_THRESHOLD_MS:
                    has_regression = True
                    error_logs.append(f"P95 latency regression: {val}ms (threshold: {LATENCY_THRESHOLD_MS}ms)")
            except (ValueError, TypeError):
                pass
                
        elif signal == "pod_oom_event":
            # New OOM event after fix = regression
            has_regression = True
            error_logs.append(f"New OOMKilled event detected after remediation: {point.value}")
            
        elif signal == "service_unhealthy":
            still_unhealthy = True
            error_logs.append(f"Service still unhealthy after action: {point.value}")
            
        elif signal == "container_restart_count":
            try:
                val = int(float(point.value))
                metrics_summary["post_restart_count"] = val
                if val > 3:
                    has_regression = True
                    error_logs.append(f"High restart count detected: {val}")
            except (ValueError, TypeError):
                pass
                
        elif signal == "queue_backlog":
            try:
                val = int(float(point.value))
                metrics_summary["post_queue_backlog"] = val
                if val > QUEUE_BACKLOG_THRESHOLD:
                    still_unhealthy = True
                    error_logs.append(f"Queue backlog still high: {val} (threshold: {QUEUE_BACKLOG_THRESHOLD})")
            except (ValueError, TypeError):
                pass

    # Decision logic per contract: DONE / RETRY / ROLLBACK / ESCALATE
    if has_regression:
        return VerifyResponse(
            success=False,
            regression_detected=True,
            next_action=NextAction.ROLLBACK,
            escalation_bundle=EscalationBundle(
                reason="Regression detected after remediation action. Rolling back.",
                logs=error_logs,
                metrics=metrics_summary
            )
        )
    elif still_unhealthy:
        return VerifyResponse(
            success=False,
            regression_detected=False,
            next_action=NextAction.ESCALATE,
            escalation_bundle=EscalationBundle(
                reason="Service did not recover after remediation. Escalating to on-call engineer.",
                logs=error_logs,
                metrics=metrics_summary
            )
        )
    else:
        # All post-action telemetry looks healthy
        return VerifyResponse(
            success=True,
            regression_detected=False,
            next_action=NextAction.DONE
        )
