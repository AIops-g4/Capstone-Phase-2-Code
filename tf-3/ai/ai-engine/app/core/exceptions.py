from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handles 400 Bad Request strictly as defined in the API contract
    when the incoming JSON doesn't match the schema.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Dữ liệu gửi lên không đúng định dạng schema.", "errors": exc.errors()},
    )
