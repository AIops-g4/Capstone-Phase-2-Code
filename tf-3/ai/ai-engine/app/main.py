from fastapi import FastAPI
from app.api import detect, decide, verify
from app.core.config import settings
from app.core.exceptions import validation_exception_handler
from fastapi.exceptions import RequestValidationError

app = FastAPI(title=settings.PROJECT_NAME)

# Override default validation error handler to match contract 400 Bad Request strictly
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# Include Routers
app.include_router(detect.router, prefix=settings.API_V1_STR, tags=["Detect"])
app.include_router(decide.router, prefix=settings.API_V1_STR, tags=["Decide"])
app.include_router(verify.router, prefix=settings.API_V1_STR, tags=["Verify"])

@app.get("/health", tags=["System"])
def health_check():
    """Liveness probe: K8s /health check"""
    return {"status": "ok"}

@app.get("/ready", tags=["System"])
def readiness_probe():
    """Readiness probe: K8s /ready check"""
    # TODO: Add FAISS / Bedrock / DynamoDB / S3 connection checks here in Phase 2
    return {"status": "ready"}
