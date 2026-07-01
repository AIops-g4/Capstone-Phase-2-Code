FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# Install system dependencies and build utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements.txt and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and default configuration profile assets
COPY src/ ./src/
COPY adr/ ./adr/

# Expose the API port
EXPOSE 8050

# Set default environment variables
# Note: API_HOST must be 0.0.0.0 to receive traffic outside the container.
ENV API_HOST=0.0.0.0
ENV API_PORT=8050
ENV PYTHONUNBUFFERED=1

# Command to run the FastAPI server
CMD ["python", "-m", "src.server"]
