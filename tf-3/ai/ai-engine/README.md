# Self-Heal AI Engine (Task Force 3)

This repository contains the AI Engine for the Self-Healing Platform, implementing the API Contracts for `/v1/detect`, `/v1/decide`, and `/v1/verify`.

## Tech Stack
- **Framework**: FastAPI
- **Validation**: Pydantic v2
- **AI Models**: Amazon Bedrock (Claude) via `boto3`
- **Anomaly Detection**: Scikit-learn
- **RAG / Vector DB**: FAISS

## Local Development

### 1. Install Dependencies
Ensure you have Python 3.11+ installed.
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run the Application
```bash
uvicorn app.main:app --reload
```
The application will be available at `http://localhost:8000`.

### 3. API Documentation (Swagger)
Once running, navigate to `http://localhost:8000/docs` to view the interactive API documentation and test the endpoints.

## Docker Deployment
To build and run the Docker container locally (as it will be run by the CDO team):

```bash
docker build -t ai-engine:latest .
docker run -p 8080:8080 ai-engine:latest
```
The application will be available at `http://localhost:8080/docs`.
