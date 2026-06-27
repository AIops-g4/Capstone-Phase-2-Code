from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse
from app.api import detect, decide, verify
from app.core.config import settings
from app.core.exceptions import validation_exception_handler
from fastapi.exceptions import RequestValidationError
import datetime

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
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}

@app.get("/ready", tags=["System"])
def readiness_probe():
    """Readiness probe: K8s /ready check"""
    # TODO: Add FAISS / Bedrock / DynamoDB / S3 connection checks here in Phase 2
    return {
        "status": "ready",
        "dependencies": {
            "bedrock": "ok",
            "dynamodb_lock": "ok",
            "s3_audit_trail": "ok"
        }
    }

@app.get("/metrics", tags=["System"], response_class=PlainTextResponse)
def get_metrics():
    """Prometheus metrics"""
    return "ai_engine_requests_total 0\n"
