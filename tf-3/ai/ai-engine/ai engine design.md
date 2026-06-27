# AI Engine Architecture Proposal
**Task Force 3 – Self-Heal Engine**

Based on:

- Capstone requirements
- AI/CDO contracts
- Modern AIOps architectures
- Self-healing platform best practices

---

# Overall Architecture

Instead of building a chatbot, the AI Engine should act as a **Decision Engine** in the self-healing pipeline.

```text
Telemetry
    │
    ▼
POST /v1/detect
    │
    ▼
POST /v1/decide
    │
    ▼
CDO executes Runbook
    │
    ▼
POST /v1/verify
    │
    ▼
Rollback / Escalate
```

This follows the standard AIOps workflow:

```
Observability
      ↓
Detection
      ↓
Decision
      ↓
Action
      ↓
Verification
      ↓
Learning
```

Modern AIOps platforms generally follow this pipeline, separating anomaly detection, decision-making, remediation, and feedback rather than relying on a single LLM. :contentReference[oaicite:0]{index=0}

---

# AI Engine Components

```text
                   AI ENGINE

        ┌─────────────────────────────┐
        │      API Gateway            │
        │         FastAPI             │
        └────────────┬────────────────┘
                     │

     ┌───────────────┼─────────────────┐
     │               │                 │

 /detect         /decide          /verify

     │               │                 │

     ▼               ▼                 ▼

Anomaly       Decision Engine      Verification
Detection      + Runbook AI          Engine

     │               │                 │

     └───────────────┼─────────────────┘

             Knowledge Base

      Pattern Library
      Runbooks
      Safety Rules
      Blast Radius
      Confidence Thresholds
```

---

# Component 1 — Detect

## Goal

Determine whether incoming telemetry indicates an incident.

Input comes from:

- Metrics
- Logs
- Traces

according to the Telemetry Contract.

Pipeline:

```text
Telemetry

↓

Feature Extraction

↓

Multi-signal Correlation

↓

Anomaly Scoring

↓

Fault Classification
```

Example:

**Input** (Aligned with API Contract)
*Includes mandatory headers: `X-Tenant-Id`, `Idempotency-Key`, `dry_run_mode`*
```json
{
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "dry_run_mode": false,
  "telemetry_window": [
    {
      "ts": "2026-06-25T10:00:00.123Z",
      "service": "order-service",
      "signal_name": "service_error_rate",
      "value": 0.15
    }
  ]
}
```

**Output**
```json
{
  "anomaly_detected": true,
  "severity": 0.85,
  "anomaly_context": {
    "target_service": "order-service",
    "suspected_fault_type": "database_connection_failure",
    "system": "E-COMMERCE"
  },
  "confidence": 0.92,
  "reasoning": "Error rate exceeds safe limits",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
}
```

---

## Suggested AI

Do **not** use an LLM here.

Recommended:

- Z-score
- Moving Average
- Isolation Forest
- One-Class SVM
- Hybrid Rule + Statistical Detection

Reason:

Metrics are numerical signals.

Statistical models are faster, cheaper, deterministic, and are widely used for anomaly detection in AIOps. :contentReference[oaicite:1]{index=1}

---

# Component 2 — Decision Engine

This is the intelligence of the system.

Input

```json
{
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "dry_run_mode": false,
  "anomaly_context": {
    "target_service": "order-service",
    "suspected_fault_type": "database_connection_failure",
    "system": "E-COMMERCE"
  }
}
```

Pipeline

```text
Retrieve Runbooks

↓

Rank Candidate Runbooks

↓

Safety Validation

↓

LLM Reasoning

↓

JSON Action Plan
```

Instead of asking:

> What should I do?

Prompt should look like

```text
Known Runbooks

1 Restart Deployment

2 Scale Worker

3 Increase Memory

4 Rollback

5 Rotate Secret

Current Incident

...

Choose ONE runbook.

Return JSON only.
```

Example Output:
```json
{
  "matched_runbook": "DatabaseConnectionRecoveryRunbook",
  "pattern_type": "urgent",
  "action_plan": [
    {
      "step": 1,
      "action": "PATCH_MEMORY_LIMIT",
      "target": "deployment/order-service",
      "params": {
        "namespace": "production"
      }
    }
  ],
  "blast_radius_config": {
    "max_pod_impact_pct": 25,
    "circuit_breaker_error_rate": 0.20,
    "allowed_namespaces": ["production"]
  },
  "verify_policy": {
    "window_seconds": 120
  },
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "dry_run_mode": false
}
```

This minimizes hallucination.

---

# Retrieval-Augmented Generation (RAG)

Runbooks should be stored in

- FAISS
- Pinecone
- Weaviate
- JSON knowledge base

Workflow

```text
Incident

↓

Retrieve Similar Runbooks

↓

LLM explains WHY

↓

Return JSON Action Plan
```

The LLM **must never invent actions**.

It should only choose from approved runbooks retrieved through RAG. This is a common pattern in operational AI systems because runbooks remain deterministic while the LLM provides contextual reasoning. :contentReference[oaicite:2]{index=2}

---

# Component 3 — Safety Engine

This component enforces operational safety.

It should NOT rely on an LLM.

Pipeline

```text
Decision

↓

Safety Validator

↓

Allowed?

↓

Return blast_radius_config

↓

CDO Controller Enforces Limits & Executes
```

Validation

The AI Engine evaluates the Runbook and generates the `blast_radius_config` and `verify_policy` which are returned in the JSON response of `/v1/decide`. 
The CDO Platform's Executor is ultimately responsible for evaluating the `blast_radius_config` against real-time cluster state before executing.

Safety rules include:

- **Idempotency Lock**: Managed via DynamoDB on the `/v1/decide` endpoint to prevent duplicate executions.
- **Dry Run**: Flag passed through all API calls to mock execution.
- **Blast Radius**: Returned by the AI Engine, enforced by the CDO Controller.
- **Tenant Isolation**: RBAC enforced via K8s Network Policies.
- **Circuit Breaker**: Trips if `cost_cap_exceeded` is true or if CDO determines error rates are too high.

Operational safety comes from policy enforcement around the model rather than from the model itself.

### Handling Alert Storms (Cascading Failures)

*Note: Cross-service root cause analysis is explicitly Out of Scope for this Capstone. Therefore, if a shared dependency fails and causes dozens of services to trigger anomalies simultaneously, the system relies entirely on the **Circuit Breaker** and **Blast Radius** policies.*

*When the volume of concurrent remediation requests exceeds safety thresholds (e.g., > 3 actions per minute, or > 5% of cluster affected), the engine will halt automated execution and immediately **escalate to the on-call engineer** with a bundled context report containing all related anomalies.* :contentReference[oaicite:3]{index=3}

---

# Component 4 — Verification Engine

Purpose

Determine whether remediation succeeded.

Workflow

```text
Before Metrics

↓

Action Executed

↓

New Telemetry

↓

Compare

↓

Healthy?

↓

Success / Regression / Rollback / Escalate
```

Example Output

```json
{
  "success": true,
  "regression_detected": false,
  "next_action": "DONE"
}
```

or (If failure occurs)

```json
{
  "success": false,
  "regression_detected": true,
  "next_action": "ESCALATE",
  "escalation_bundle": {
    "reason": "OOMKilled continues after restart",
    "logs": ["..."]
  }
}
```

---

# Component 5 — Learning

Optional but valuable.

Store

- Incident
- Telemetry
- Selected Runbook
- Success
- Rollback
- Human Feedback

Pipeline

```text
Incident

↓

Incident Database

↓

Analytics

↓

Improve Confidence

↓

Improve Future Decisions
```

This is **incident memory**, not online model retraining.

---

# AI Engine Folder Structure

```text
ai-engine/
│
├── app/
│   ├── api/
│   │   ├── detect.py
│   │   ├── decide.py
│   │   └── verify.py
│   │
│   ├── core/              
│   │   ├── config.py
│   │   ├── logger.py
│   │   └── exceptions.py
│   │
│   ├── detector/
│   ├── decision/
│   │   └── prompts/       
│   │
│   ├── safety/
│   ├── verifier/
│   ├── rag/
│   ├── models/            
│   ├── schemas/           
│   └── main.py
│
├── tests/
├── runbooks/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

# Recommended Tech Stack

| Layer | Technology |
|--------|------------|
| API | FastAPI |
| Validation | Pydantic |
| AI Model | Claude (Amazon Bedrock) / GPT |
| Vector Database | FAISS |
| Statistical Detection | Scikit-learn |
| Logging | OpenTelemetry |
| Idempotency Lock | DynamoDB (Per Deployment Contract) |
| Audit Trail | Amazon S3 Object Lock (Per Deployment Contract) |
| Testing | pytest |

---

# Three Implemented Patterns

| Pattern | Detection | Decision |
|----------|-----------|----------|
| Pod OOMKilled | OOM Event + High Memory | Restart Deployment |
| Queue Backlog | Queue Length Rising | Scale Worker |
| Service Unhealthy | Error Rate + Health Check | Restart Deployment |

---

# Two Designed Patterns

- Certificate Expiry → Rotate Secret
- Database Connection Pool Saturation → Restart Service / Scale Service

---

# Why This Architecture?

This hybrid architecture separates responsibilities:

- Statistical models perform anomaly detection.
- LLM performs contextual reasoning.
- RAG retrieves approved operational knowledge.
- Safety engine enforces deterministic guardrails.
- Verification engine validates remediation.
- Learning engine stores operational knowledge.

This architecture aligns with modern AIOps reference designs where AI assists operators but deterministic guardrails, safety policies, and workflow orchestration remain outside the LLM. :contentReference[oaicite:4]{index=4}