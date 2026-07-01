# =====================================================================
# Dockerfile for AIOps AI Engine Service (detect_decide_verify)
# =====================================================================

# Use an official Python 3.11 slim image as the base (matches K8s simulator deployments)
FROM python:3.11-slim

# Set environment variables
# Prevent Python from writing .pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
# Prevent Python from buffering stdout and stderr (crucial for container logs)
ENV PYTHONUNBUFFERED=1

# Bind API server to all interfaces (required for external/container access)
ENV API_HOST=0.0.0.0
# Set default API port
ENV API_PORT=8080

# Create and set the working directory
WORKDIR /app

# Install system utilities needed (e.g., curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first to optimize Docker image layer caching
COPY requirements.txt /app/

# Install python dependencies using pip cache mounting to speed up builds
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout=1000 --retries=10 --upgrade pip && \
    pip install --timeout=1000 --retries=10 -r requirements.txt

# Copy source code and configuration files
COPY src/ /app/src/
COPY adr/ /app/adr/

# Create a non-root system user and group for security compliance
RUN useradd -u 10001 -U -d /app -s /bin/bash appuser && \
    chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Expose the API server port
EXPOSE 8080

# Health check to monitor application status in Docker / Kubernetes
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:${API_PORT}/health || exit 1

# Start the FastAPI server using the module syntax to correctly resolve relative imports
CMD ["python", "-m", "src.server"]
