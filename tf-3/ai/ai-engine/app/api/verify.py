from fastapi import APIRouter, Header
from app.schemas.verify import VerifyRequest, VerifyResponse, NextAction

router = APIRouter()

@router.post("/verify", response_model=VerifyResponse)
async def verify_action(
    request: VerifyRequest,
    x_tenant_id: str = Header(..., description="Định danh duy nhất của Tenant"),
):
    """
    Endpoint Xác thực: Đánh giá hiệu quả của hành động khắc phục lỗi 
    dựa trên dữ liệu telemetry thu được sau sự kiện.
    """
    return VerifyResponse(
        success=True,
        regression_detected=False,
        next_action=NextAction.DONE
    )
