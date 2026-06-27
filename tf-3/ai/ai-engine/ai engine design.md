# AI Engine Architecture Proposal
**Task Force 3 – Self-Heal Engine**

Based on:

- Capstone requirements (TF3_SELFHEAL_LEARNER.md)
- AI API Contract, Telemetry Contract, Deployment Contract
- Modern AIOps architectures
- Self-healing platform best practices

---

# Overall Architecture

Instead of building a chatbot, the AI Engine acts as a **Decision Engine** in the self-healing pipeline.

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

Modern AIOps platforms generally follow this pipeline, separating anomaly detection, decision-making, remediation, and feedback rather than relying on a single LLM.

---

# AI Engine Components

```text
                   AI ENGINE

        ┌─────────────────────────────┐
        │      API Gateway            │
        │         FastAPI             │
        │  (X-Tenant-Id extraction)   │
        └────────────┬────────────────┘
                     │
                Tenant Context
                     │
     ┌───────────────┼─────────────────┐
     │               │                 │

 /detect         /decide          /verify

     │               │                 │

     ▼               ▼                 ▼

Anomaly       Decision Engine      Verification
Detection     (LLM + Fallback      Engine
              Rule-Based)

     │               │                 │

     └───────────────┼─────────────────┘
                     │
          ┌──────────┴──────────┐
          │                     │
    Knowledge Base        Audit Logger
                          (S3 Object Lock)
    Pattern Library
    Runbooks
    Safety Rules
    Blast Radius
    Confidence Thresholds
```

---

# Multi-Tenant Routing & Isolation

The AI Engine supports **≥2 tenants** with full data isolation. Every request must include the `X-Tenant-Id` header.

## Tenant Context Extraction

On every incoming request, the API Gateway middleware:

1. Extracts `X-Tenant-Id` from the request header (required on all endpoints).
2. Creates a `TenantContext` object that is passed to all downstream components.
3. Logs the `tenant_id` in every audit record and OpenTelemetry span.

## Cross-Tenant Validation (403 Forbidden)

Per the AI API Contract §4, the engine enforces strict cross-tenant validation:

- On `/v1/detect`: Validates that every `tenant_id` field inside `telemetry_window[]` items matches the `X-Tenant-Id` header. If any mismatch is found, the request is immediately rejected with `403 Forbidden`.
- On `/v1/decide`: Validates that the `anomaly_context` originates from the same tenant.
- On `/v1/verify`: Validates that `post_telemetry_window[]` items match the tenant.

```python
# Pseudo-code for cross-tenant validation middleware
def validate_tenant_isolation(header_tenant_id: str, payload_tenant_ids: list[str]):
    for tid in payload_tenant_ids:
        if tid != header_tenant_id:
            raise HTTPException(
                status_code=403,
                detail=f"Tenant isolation violation: header={header_tenant_id}, payload={tid}"
            )
```

## Tenant-Scoped Resources

| Resource | Isolation Method |
|----------|-----------------|
| Runbook configurations | Loaded per-tenant from knowledge base |
| Cost cap tracking | Per-tenant counter in DynamoDB ($50/day limit). Alert at 80% ($40), circuit break at 100% ($50). Resets 00:00 UTC. |
| Idempotency locks | DynamoDB keys scoped by `tenant_id + idempotency_key` |
| Audit logs | S3 prefix: `audit/{tenant_id}/{date}/{correlation_id}.json` |
| Circuit breaker state | Per-tenant action counter (max 3 automated actions per 5 minutes per tenant) |

## Network-Level Isolation

Per the Deployment Contract §5, tenant isolation at the Kubernetes level is enforced by:

- K8s Network Policies restricting ingress to AI Engine pods.
- CDO Controller's RBAC restricting actions to allowed namespaces per tenant.
- AI Engine has **no** direct access to K8s API (Deployment Contract §3).

---

# Component 1 — Detect

## Goal

Determine whether incoming telemetry indicates an incident.

Input comes from:

- Metrics (service_error_rate, service_latency_p95, container_resource_usage, etc.)
- Logs (application_log_event)
- Events (pod_oom_event, service_unhealthy, secret_expiry_warning)
- Traces (distributed_trace_error_event)

according to the Telemetry Contract (12 defined signal types across 4 layers).

## Pipeline

```text
Telemetry (telemetry_window[])
       ↓
Tenant Validation (X-Tenant-Id == payload tenant_id)
       ↓
Feature Extraction (parse signal_name, value, labels)
       ↓
Signal Routing (binary event vs continuous metric)
       ↓
┌──────────────────────┬──────────────────────────┐
│ Binary Events        │ Continuous Metrics        │
│ (Rule-Based)         │ (RRCF Anomaly Scoring)    │
│                      │                           │
│ pod_oom_event        │ service_error_rate         │
│ service_unhealthy    │ service_latency_p95        │
│ secret_expiry_warning│ container_resource_usage   │
│ container_restart    │ queue_backlog              │
│   _count (threshold) │ db_connection_pool_sat.    │
│                      │ service_throughput_rps     │
└──────────┬───────────┴──────────┬────────────────┘
           │                      │
           └──────────┬───────────┘
                      ↓
              Score Aggregation
                      ↓
              Fault Classification (map to suspected_fault_type)
```

## Example

**Input** (Aligned with AI API Contract & Telemetry Contract schemas)

*Headers: `X-Tenant-Id: d3b07384-d113-495f-9f58-20d18d357d75`, `Idempotency-Key: <uuid>`, `X-Dry-Run-Mode: false`*

```json
{
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "dry_run_mode": false,
  "telemetry_window": [
    {
      "ts": "2026-06-25T10:00:00.123Z",
      "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
      "service": "order-service",
      "signal_name": "service_error_rate",
      "value": 0.15,
      "labels": {
        "system": "E-COMMERCE",
        "namespace": "production",
        "deployment": "order-service",
        "endpoint": "/v1/orders/checkout"
      }
    },
    {
      "ts": "2026-06-25T10:00:01.456Z",
      "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
      "service": "order-service",
      "signal_name": "application_log_event",
      "value": "NullPointerException: Conn timed out\n\tat com.ecommerce.OrderService.save(OrderService.java:45)",
      "labels": {
        "system": "E-COMMERCE",
        "pod_name": "order-service-5f8d9b7c-xyz12",
        "namespace": "production",
        "deployment": "order-service",
        "level": "ERROR"
      }
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
    "system": "E-COMMERCE",
    "namespace": "production",
    "deployment": "order-service",
    "trigger_metric": "service_error_rate",
    "trigger_value": 0.15
  },
  "confidence": 0.92,
  "reasoning": "Error rate of order-service (15%) exceeds 5% threshold, correlated with NullPointerException in DB connection stack trace.",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
}
```

> **Note**: Per AI API Contract §3.1, the `reasoning` field is capped at **300 characters**. The detection engine must truncate longer explanations.

---

## Suggested AI for Detection

Do **not** use an LLM here.

Recommended approach — **Hybrid Rule-Based + RRCF (Robust Random Cut Forest)**:

### Layer 1: Rule-Based (for binary events)

Binary event signals are inherently discrete — they either happened or they didn't. A rule engine handles these directly:

| Signal | Rule | Result |
|---|---|---|
| `pod_oom_event` | Event present → anomaly | `suspected_fault_type: "pod_oom_event"` |
| `service_unhealthy` | Event present → anomaly | `suspected_fault_type: "service_unhealthy"` |
| `secret_expiry_warning` | `value ≤ 7` days → anomaly | `suspected_fault_type: "secret_expiry_warning"` |
| `container_restart_count` | `value ≥ 5` in window → anomaly | `suspected_fault_type: "crash_loop"` |

### Layer 2: RRCF (for continuous metrics)

Continuous metric signals require anomaly scoring to detect gradual drifts and spikes. **RRCF (Robust Random Cut Forest)** is used as the primary anomaly detection model:

| Signal | RRCF Input | Anomaly Threshold |
|---|---|---|
| `service_error_rate` | Float 0.0–1.0 | CoDisp score > configured threshold |
| `service_latency_p95` | Float (ms) | CoDisp score > configured threshold |
| `container_resource_usage` | Integer (bytes) | CoDisp score > configured threshold |
| `queue_backlog` | Integer (msg count) | CoDisp score > configured threshold |
| `db_connection_pool_saturation` | Float 0.0–1.0 | CoDisp score > configured threshold |
| `service_throughput_rps` | Float (RPS) | CoDisp score > configured threshold |

**Why RRCF over Isolation Forest / Z-score:**

| Aspect | Z-score | Isolation Forest | RRCF ✅ |
|---|---|---|---|
| Streaming support | ✅ | ❌ Needs batch retrain | ✅ Native streaming (insert/forget) |
| Multivariate | ❌ Per-signal | ✅ | ✅ |
| Handles concept drift | ❌ Fixed baseline | ❌ Static model | ✅ Adapts via shingling window |
| Auto-retrain needed? | No | Yes (out of scope) | No (incremental updates) |
| Library | NumPy | scikit-learn | `rrcf` PyPI |
| Inference latency | ~1ms | ~5ms | ~5ms |

RRCF maintains a forest of random cut trees. Each incoming data point is inserted and scored using **Collusive Displacement (CoDisp)** — a high CoDisp score means the point is anomalous. Old points are forgotten using a sliding window (shingling), so the model adapts to baseline shifts without requiring batch retraining (avoiding the "Auto-retrain ML model" out-of-scope constraint).

### Score Aggregation

Results from both layers are combined:
- Rule layer hits → immediate anomaly with high confidence (0.95+)
- RRCF high CoDisp → anomaly with confidence proportional to score
- Both layers agree → highest confidence
- RRCF alone → moderate confidence (requires verify_policy window for confirmation)

All detection runs within **p99 < 300ms** per SLA. No LLM calls are made in this component.

## SLA & Rate Limits (per AI API Contract §4)

| Endpoint | p99 Latency Target | Rate Limit (per tenant) |
|---|---|---|
| `POST /v1/detect` | < 300 ms | 100 RPS |
| `POST /v1/decide` (LLM) | < 3000 ms | 10 RPS |
| `POST /v1/decide` (fallback) | < 500 ms | 10 RPS |
| `POST /v1/verify` | < 500 ms | 10 RPS |

- **Availability target**: 99.9%
- Exceeding rate limits returns `429 Too Many Requests` with `Retry-After` header.

## Error Handling (per AI API Contract §4)

| Code | Meaning | CDO Action |
|---|---|---|
| `400 Bad Request` | Schema validation failure | Log and fix code, do NOT retry |
| `401 Unauthorized` | Invalid Local Trust / mTLS config | Check auth config, do NOT retry |
| `403 Forbidden` | `X-Tenant-Id` mismatch with payload `tenant_id` | Check tenant mapping logic, do NOT retry |
| `409 Conflict` | Duplicate `Idempotency-Key` | Request already processed, skip |
| `429 Too Many Requests` | Rate limit exceeded | Retry after `Retry-After` header value (exponential backoff) |
| `500 Internal Server Error` | Unexpected bug / runtime error | Retry max 2× with backoff (1s, 3s), then escalate |
| `503 Service Unavailable` | Bedrock/DDB/S3 dependency down | CDO must have internal fallback (static runbook or escalate to SRE) |

---

# Component 2 — Decision Engine

This is the intelligence of the system. It operates in two modes: **LLM-powered** (primary) and **Rule-based fallback** (when LLM is unavailable).

## Input

```json
{
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "dry_run_mode": false,
  "anomaly_context": {
    "target_service": "order-service",
    "suspected_fault_type": "database_connection_failure",
    "system": "E-COMMERCE",
    "namespace": "production",
    "deployment": "order-service",
    "trigger_metric": "service_error_rate",
    "trigger_value": 0.15
  }
}
```

## Primary Path — LLM-Powered Decision (via Bedrock)

Pipeline:

```text
Anomaly Context
       ↓
Idempotency Check (DynamoDB conditional write)
       ↓
Retrieve Runbooks via RAG (FAISS similarity search)
       ↓
Rank Candidate Runbooks
       ↓
Safety Pre-Validation (blast radius check)
       ↓
LLM Reasoning (Bedrock Claude — constrained to retrieved runbooks)
       ↓
Assign pattern_type (urgent / deferred)
       ↓
JSON Action Plan + blast_radius_config + verify_policy
```

Instead of asking the LLM an open-ended question:

> What should I do?

The prompt constrains the LLM to choose from **approved runbooks only**:

```text
You are a Self-Heal Decision Engine. You may ONLY choose from the approved runbooks below.
Do NOT invent any action not listed. If no runbook matches, respond with ESCALATE.

## Approved Runbooks
1. RestartDeploymentRunbook — RESTART_DEPLOYMENT
2. PatchMemoryLimitRunbook — PATCH_MEMORY_LIMIT
3. ScaleReplicasRunbook — SCALE_REPLICAS
4. RolloutUndoRunbook — ROLLOUT_UNDO
5. RotateSecretRunbook — ROTATE_SECRET

## Current Incident
- Service: {target_service}
- Fault Type: {suspected_fault_type}
- Trigger Metric: {trigger_metric} = {trigger_value}
- Namespace: {namespace}

## Instructions
1. Select the BEST matching runbook from the list above.
2. Determine pattern_type: "urgent" (immediate K8s patch) or "deferred" (GitOps commit/PR).
3. Return ONLY valid JSON matching the DecideResponse schema.
```

## Fallback Path — Rule-Based Decision (No LLM)

The engine automatically switches to the rule-based fallback when any of these conditions occur (per AI API Contract §4):

1. **Cost cap exceeded**: Tenant's daily Bedrock cost ≥ $50 (tracked per-tenant in DynamoDB).
2. **Bedrock rate-limited**: HTTP 429 from Bedrock and retry would exceed 2000ms internal budget.
3. **Bedrock timeout/downtime**: Connection failure, response > 2500ms, or 5xx errors.
4. **LLM response parse failure**: Response is not valid JSON or fails `DecideResponse` schema validation.

The fallback uses a **static decision tree** that maps `suspected_fault_type` directly to an action:

```text
Fallback Decision Tree (deterministic, p99 < 500ms):

suspected_fault_type == "pod_oom_event"
    → action: PATCH_MEMORY_LIMIT
    → params: memory_limit_mb = current × 1.5 (capped at 4096 MB)
    → pattern_type: "urgent"

suspected_fault_type == "service_unhealthy"
    → action: RESTART_DEPLOYMENT
    → params: grace_period_seconds = 30
    → pattern_type: "urgent"

suspected_fault_type == "queue_backlog"
    → action: SCALE_REPLICAS
    → params: replicas = current × 2 (capped at 10)
    → pattern_type: "deferred"

suspected_fault_type == "secret_expiry_warning"
    → action: ROTATE_SECRET
    → params: secret_name from anomaly_context
    → pattern_type: "deferred"

suspected_fault_type == "db_connection_pool_saturation"
    → action: RESTART_DEPLOYMENT
    → params: grace_period_seconds = 30
    → pattern_type: "urgent"

suspected_fault_type == (any other)
    → next_action: ESCALATE
    → Generate escalation bundle and skip execution
```

When fallback is triggered, the response includes `"cost_cap_exceeded": true` (if triggered by cost cap) to inform CDO.

## Pattern Type — Urgent vs Deferred

The `pattern_type` field (required in `DecideResponse`) determines how CDO executes the action:

| pattern_type | CDO Execution Method | RTO Target | Use Cases |
|---|---|---|---|
| `"urgent"` | CDO patches K8s API directly (hotfix) | < 60 seconds | OOMKilled, Service Unhealthy, DB Pool Saturation |
| `"deferred"` | CDO creates Git commit/PR → GitOps syncs | 2-5 minutes | Queue Backlog scaling, Secret Rotation, Cert Expiry |

**Mapping per pattern:**

| Pattern | suspected_fault_type | pattern_type | Rationale |
|---|---|---|---|
| Pod OOMKilled | `pod_oom_event` | `urgent` | Immediate availability threat, pods crashing |
| Service Unhealthy | `service_unhealthy` | `urgent` | Liveness/readiness failure, active outage |
| Queue Backlog | `queue_backlog` | `deferred` | Capacity scaling, not a live outage |
| Certificate Expiry | `secret_expiry_warning` | `deferred` | Planned rotation, days until expiry |
| DB Connection Pool | `db_connection_pool_saturation` | `urgent` | Active connection exhaustion, service degrading |

**Rollback behavior differs by pattern_type** (per AI API Contract §3.2):

- **Urgent path**: CDO captures K8s resource state (memory_limit, replica_count, image_tag) *before* applying patch. On `ROLLBACK`, CDO applies the saved snapshot via K8s API.
- **Deferred path**: CDO records the Git commit SHA *before* creating a new commit/PR. On `ROLLBACK`, CDO runs `git revert` or ArgoCD rollback to the prior revision.

## Example Output (LLM Path)

```json
{
  "matched_runbook": "DatabaseConnectionRecoveryRunbook",
  "pattern_type": "urgent",
  "action_plan": [
    {
      "step": 1,
      "action": "RESTART_DEPLOYMENT",
      "target": "deployment/order-service",
      "params": {
        "namespace": "production",
        "grace_period_seconds": 30
      }
    }
  ],
  "blast_radius_config": {
    "max_pod_impact_pct": 25,
    "circuit_breaker_error_rate": 0.20,
    "allowed_namespaces": ["production"]
  },
  "verify_policy": {
    "window_seconds": 120,
    "success_conditions": [
      "pod_ready == true",
      "restart_count_no_increase == true",
      "service_error_rate < 0.05"
    ]
  },
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "dry_run_mode": false,
  "cost_cap_exceeded": false
}
```

## Example Output (Fallback Rule-Based Path — Cost Cap Exceeded)

```json
{
  "matched_runbook": "FallbackRule::RestartDeploymentRunbook",
  "pattern_type": "urgent",
  "action_plan": [
    {
      "step": 1,
      "action": "RESTART_DEPLOYMENT",
      "target": "deployment/order-service",
      "params": {
        "namespace": "production",
        "grace_period_seconds": 30
      }
    }
  ],
  "blast_radius_config": {
    "max_pod_impact_pct": 25,
    "circuit_breaker_error_rate": 0.20,
    "allowed_namespaces": ["production"]
  },
  "verify_policy": {
    "window_seconds": 120,
    "success_conditions": [
      "pod_ready == true",
      "restart_count_no_increase == true"
    ]
  },
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "dry_run_mode": false,
  "cost_cap_exceeded": true
}
```

This minimizes hallucination. The LLM can never invent actions — it only selects from approved runbooks. When the LLM is unavailable, the deterministic fallback guarantees a safe response within 500ms.

---

# Retrieval-Augmented Generation (RAG)

Runbooks are stored in **FAISS** (primary choice for capstone — local, no external dependency, zero cost):

- Each runbook is a structured JSON document describing: trigger conditions, actions, blast radius defaults, and verify conditions.
- Runbook text is embedded using a sentence-transformer model and indexed in FAISS.
- At query time, the `anomaly_context` is embedded and the top-K most similar runbooks are retrieved.

Workflow:

```text
Anomaly Context (suspected_fault_type + target_service + trigger_metric)
       ↓
Embed query using sentence-transformer
       ↓
FAISS similarity search → retrieve top 3 runbooks
       ↓
Inject runbooks into LLM prompt as constrained options
       ↓
LLM explains WHY this runbook matches + returns JSON Action Plan
```

The LLM **must never invent actions**.

It should only choose from approved runbooks retrieved through RAG. Runbooks remain deterministic while the LLM provides contextual reasoning for why a specific runbook is the best match.

---

# Component 3 — Safety Engine

This component enforces operational safety.

It should NOT rely on an LLM. All safety checks are deterministic and rule-based.

## Pipeline

```text
Decision (from Component 2)
       ↓
Idempotency Lock Check (DynamoDB)
       ↓
Tenant Isolation Validation
       ↓
Blast Radius Check
       ↓
Circuit Breaker Check
       ↓
Dry Run Check
       ↓
Allowed? ──No──→ REJECT or ESCALATE
       │
      Yes
       ↓
Return blast_radius_config + verify_policy
       ↓
CDO Controller Enforces Limits & Executes
```

## 5 Mandatory Safety Sub-Checkpoints

Per TF3 requirements, the following 5 safety sub-checkpoints are mandatory:

| # | Sub-Checkpoint | Implementation | Owner |
|---|---|---|---|
| 1 | **Dry Run** | `dry_run_mode` flag passed through all API calls. When `true`, engine generates action plan + audit log but CDO skips actual execution. | AI Engine (flag) + CDO (enforcement) |
| 2 | **Blast Radius** | AI Engine generates `blast_radius_config` (max_pod_impact_pct, circuit_breaker_error_rate, allowed_namespaces). CDO Controller validates against real-time cluster state before executing. | AI Engine (config) + CDO (enforcement) |
| 3 | **Verify Post-Action** | After CDO executes, it calls `/v1/verify` with post_telemetry_window. AI Engine evaluates success_conditions and returns DONE/RETRY/ROLLBACK/ESCALATE. | AI Engine (evaluation) + CDO (telemetry collection) |
| 4 | **Auto Rollback** | When `/v1/verify` returns `next_action: "ROLLBACK"`, CDO reverts to pre-action state. For urgent: revert K8s snapshot. For deferred: `git revert`. | CDO (execution) based on AI Engine verdict |
| 5 | **Circuit Breaker** | Trips when: (a) `cost_cap_exceeded` is true, (b) concurrent remediation requests exceed safety thresholds (>3 actions/5min per tenant, or >5% cluster affected), or (c) CDO reports error rate too high. When tripped, all automated actions halt → escalate to human. | AI Engine (cost cap) + CDO (action rate + cluster state) |

The AI Engine evaluates the Runbook and generates the `blast_radius_config` and `verify_policy` which are returned in the JSON response of `/v1/decide`.
The CDO Platform's Executor is ultimately responsible for evaluating the `blast_radius_config` against real-time cluster state before executing.

Safety rules include:

- **Idempotency Lock**: Managed via DynamoDB atomic conditional write (`PutItem` with `ConditionExpression: "attribute_not_exists(lock_key)"`) on the `/v1/decide` endpoint to prevent duplicate executions. Returns `409 Conflict` if duplicate. Scoped by `tenant_id + idempotency_key`.
- **Dry Run**: Flag passed through all API calls to mock execution. When `true`, all components behave normally but CDO skips actual K8s mutations.
- **Blast Radius**: Returned by the AI Engine (max_pod_impact_pct, circuit_breaker_error_rate, allowed_namespaces), enforced by the CDO Controller against real-time cluster state.
- **Tenant Isolation**: Cross-tenant validation at API layer + RBAC enforced via K8s Network Policies at infrastructure layer.
- **Circuit Breaker**: Trips if `cost_cap_exceeded` is true, if concurrent action count exceeds threshold, or if CDO determines error rates are too high. When tripped → halt all automated actions → escalate.

Operational safety comes from policy enforcement around the model rather than from the model itself.

### Handling Alert Storms (Cascading Failures)

*Note: Cross-service root cause analysis is explicitly Out of Scope for this Capstone. Therefore, if a shared dependency fails and causes dozens of services to trigger anomalies simultaneously, the system relies entirely on the **Circuit Breaker** and **Blast Radius** policies.*

When the volume of concurrent remediation requests exceeds safety thresholds (e.g., > 3 actions per 5 minutes per tenant, or > 5% of cluster affected), the engine will halt automated execution and immediately **escalate to the on-call engineer** with a bundled context report containing all related anomalies:

1. **"Dumb" Detection**: Because cross-service RCA is out of scope, Component 1 (Detect) will see this as N separate anomalies and Component 2 (Decision) will propose N separate remediation actions.
2. **"Smart" Safety Guard (Circuit Breaker)**: Before any action is executed, it must pass Component 3 (Safety Engine). The Capstone explicitly requires a Circuit Breaker and Blast Radius check.
3. **Tripping the Breaker**: If the system sees 10 restart requests at the same time, it will violate the Blast Radius rule (e.g., "Cannot affect more than 5% of the cluster") or trip the Circuit Breaker (e.g., "Max 3 automated actions per 5 minutes per tenant").
4. **Safe Escalation**: Because the Circuit Breaker tripped, the system aborts all automated actions (preventing cascade restart chaos). It bundles all anomaly context and escalates to the human engineer with a full AI-generated context bundle.

---

# Component 4 — Verification Engine

## Purpose

Determine whether remediation succeeded by comparing pre-action and post-action telemetry.

**SLA**: p99 latency < 500ms (per AI API Contract §4).

## Workflow

```text
POST /v1/verify receives:
  - action_executed (what CDO did + status)
  - post_telemetry_window (telemetry after remediation)
       ↓
Retrieve original anomaly context (via correlation_id)
       ↓
Retrieve verify_policy.success_conditions (from /v1/decide response)
       ↓
Evaluate each success_condition against post_telemetry_window
       ↓
ALL conditions pass? → success=true, next_action=DONE
ANY condition fails? → Check regression
       ↓
Regression detected? → next_action=ROLLBACK
No regression but not resolved? → next_action=RETRY (max 1 retry)
Retry already attempted? → next_action=ESCALATE (with full context bundle)
```

## Success Conditions per Pattern

Each pattern defines specific `success_conditions` that the verification engine evaluates against `post_telemetry_window`:

| Pattern | success_conditions | Evaluation Logic |
|---|---|---|
| Pod OOMKilled | `pod_ready == true`, `restart_count_no_increase == true`, `container_memory_usage_pct < 80` | Check `service_unhealthy` absent, `container_restart_count` not increasing, `container_resource_usage` below 80% of new limit |
| Queue Backlog | `queue_depth_decreasing == true`, `worker_pods_ready == true` | Check `queue_backlog` value is decreasing over verify window, new scaled pods are Ready |
| Service Unhealthy | `pod_ready == true`, `restart_count_no_increase == true`, `service_error_rate < 0.05` | Check no `service_unhealthy` events in post-window, `service_error_rate` below 5% threshold |
| Cert Expiry | `secret_rotated == true`, `service_healthy == true` | Check `secret_expiry_warning` value increased (days until expiry), no new errors after rotation |
| DB Connection Pool | `pool_saturation < 0.80`, `service_error_rate < 0.05` | Check `db_connection_pool_saturation` below 80%, `service_error_rate` normalized |

## Example Input (Verify Request)

*Headers: `X-Tenant-Id: d3b07384-...`, `X-Correlation-Id: 9b1deb4d-...`, `Idempotency-Key: <uuid>`, `X-Dry-Run-Mode: false`*

```json
{
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "a1c2e3f4-5678-9abc-def0-1234567890ab",
  "dry_run_mode": false,
  "action_executed": {
    "action": "RESTART_DEPLOYMENT",
    "target": "deployment/order-service",
    "status": "COMPLETED",
    "execution_time_seconds": 45
  },
  "post_telemetry_window": [
    {
      "ts": "2026-06-25T10:02:00.000Z",
      "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
      "service": "order-service",
      "signal_name": "service_error_rate",
      "value": 0.01,
      "labels": {
        "system": "E-COMMERCE",
        "namespace": "production",
        "deployment": "order-service"
      }
    }
  ]
}
```

## Example Output — Success

```json
{
  "success": true,
  "regression_detected": false,
  "next_action": "DONE"
}
```

## Example Output — Failure with Escalation

When remediation fails and the engine cannot self-resolve, it generates an **AI-powered escalation bundle** with full context for the on-call engineer (per TF3 requirement: "message phải kèm full context bundle — logs, metrics, deploy history, attempts đã thử"):

```json
{
  "success": false,
  "regression_detected": true,
  "next_action": "ESCALATE",
  "escalation_bundle": {
    "reason": "OOMKilled recurred within 120s after memory limit patch. Container memory usage reached 95% of new 768MB limit, indicating the root cause is a memory leak rather than an undersized limit.",
    "logs": [
      "2026-06-25T10:02:15Z ERROR order-service-5f8d9b7c-xyz12: java.lang.OutOfMemoryError: Java heap space",
      "2026-06-25T10:02:15Z WARN  kubelet: Container main in pod order-service-5f8d9b7c-xyz12 exceeded memory limit"
    ],
    "metrics": {
      "container_memory_usage_bytes": 805306368,
      "container_memory_limit_bytes": 805306368,
      "service_error_rate": 0.22,
      "container_restart_count": 4,
      "service_latency_p95_ms": 1250
    },
    "attempted_actions": [
      {
        "action": "PATCH_MEMORY_LIMIT",
        "target": "deployment/order-service",
        "params": {"memory_limit_mb": 768},
        "result": "FAILED — OOM recurred"
      }
    ],
    "timeline": [
      "10:00:00 — Anomaly detected: error_rate=15%, OOM event",
      "10:00:02 — Decision: PATCH_MEMORY_LIMIT 512→768MB (urgent)",
      "10:00:05 — CDO executed patch, pod restarted",
      "10:02:00 — Verification: memory at 95% of new limit",
      "10:02:15 — OOMKilled again, escalating to on-call"
    ]
  }
}
```

### Escalation Message Generation

The escalation bundle is generated using a **hybrid approach**:

1. **Structured data** (metrics, logs, timeline, attempted_actions) is assembled programmatically from the audit log and telemetry.
2. **`reason` field** is generated by the LLM (Bedrock Claude) with a constrained prompt that summarizes the incident in 1-2 sentences for human consumption. If LLM is unavailable, a template-based fallback generates the reason: `"Auto-remediation failed for {fault_type} on {target_service} after {N} attempts. Last action: {action}. Current state: {key_metrics}."`.

---

# Component 5 — Audit Trail (Tamper-Evident Logging)

Per the Deployment Contract §4 and TF3 requirements (SOC2 Type II compliance, audit log tamper-evident, retention ≥90 days), the AI Engine writes an audit record for **every API call** to Amazon S3 with Object Lock.

## What Gets Logged

Every `detect`, `decide`, and `verify` call produces a structured JSON audit record:

```json
{
  "audit_version": "1.0",
  "timestamp": "2026-06-25T10:00:00.123Z",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
  "endpoint": "/v1/detect",
  "dry_run_mode": false,
  "request_body": { "...full request payload..." },
  "response_body": { "...full response payload..." },
  "response_status_code": 200,
  "latency_ms": 145,
  "decision_path": "statistical_model",
  "model_version": "detect-v1.0.0",
  "safety_checks_passed": ["tenant_isolation", "schema_validation"]
}
```

For `/v1/decide`, the audit record additionally includes:

```json
{
  "decision_path": "llm_bedrock" | "fallback_rule_based",
  "matched_runbook": "DatabaseConnectionRecoveryRunbook",
  "pattern_type": "urgent",
  "cost_cap_exceeded": false,
  "idempotency_lock_acquired": true,
  "safety_checks_passed": ["tenant_isolation", "idempotency_lock", "blast_radius", "circuit_breaker", "dry_run"]
}
```

## Storage

| Aspect | Configuration |
|--------|---------------|
| **Storage** | Amazon S3 bucket: `tf-3-aiops-audit-trail` |
| **Object Lock** | Compliance mode (cannot be deleted or overwritten) |
| **Retention** | 90 days minimum |
| **Key format** | `audit/{tenant_id}/{YYYY-MM-DD}/{correlation_id}_{endpoint}_{timestamp}.json` |
| **Encryption** | SSE-S3 (server-side encryption) |
| **Access** | AI Engine writes via IRSA (s3:PutObject, s3:GetObject) |
| **Query** | CDO provides Athena or UI for querying audit logs |

## When

Audit records are written **synchronously** before returning the API response. This ensures that every decision is logged even if the system crashes immediately after responding.

Write flow:
1. Process request → generate response.
2. Write audit record to S3 (with Object Lock).
3. Return response to CDO.

If the S3 write fails, the API call returns `500 Internal Server Error` rather than proceeding without an audit trail (fail-closed behavior for compliance).

---

# Component 6 — Learning

Optional but valuable.

Store:

- Incident context
- Telemetry snapshot
- Selected Runbook
- Success / Failure result
- Rollback events
- Human Feedback (from escalation responses)

Pipeline:

```text
Incident resolved or escalated
       ↓
Write to Incident Database (DynamoDB or S3)
       ↓
Periodic Analytics (batch, manual)
       ↓
Improve Confidence Thresholds
       ↓
Update Runbook Priority Weights
       ↓
Improve Future Decisions
```

This is **incident memory**, not online model retraining. Per TF3 Out of Scope: "Auto-retrain ML model — engine rule-based hoặc hybrid, ML thì batch manual."

---

# AI Engine Folder Structure

```text
ai-engine/
│
├── app/
│   ├── api/
│   │   ├── detect.py          # POST /v1/detect endpoint
│   │   ├── decide.py          # POST /v1/decide endpoint
│   │   ├── verify.py          # POST /v1/verify endpoint
│   │   ├── health.py          # GET /health, GET /ready
│   │   └── metrics.py         # GET /metrics (Prometheus format)
│   │
│   ├── core/
│   │   ├── config.py          # Environment config, feature flags
│   │   ├── logger.py          # Structured logging setup
│   │   ├── exceptions.py      # Custom exception classes
│   │   └── tenant.py          # TenantContext extraction + validation
│   │
│   ├── detector/              # Component 1 — anomaly detection logic
│   │   ├── rule_engine.py     # Binary event detection (OOM, unhealthy, etc.)
│   │   ├── rrcf_engine.py     # RRCF anomaly scoring for continuous metrics
│   │   ├── aggregator.py      # Score aggregation from both layers
│   │   └── classifier.py      # Fault type classification
│   │
│   ├── decision/              # Component 2 — decision engine
│   │   ├── llm_engine.py      # Bedrock Claude integration
│   │   ├── fallback_engine.py # Rule-based fallback decision tree
│   │   ├── prompts/           # LLM prompt templates
│   │   └── router.py          # Routes between LLM and fallback
│   │
│   ├── safety/                # Component 3 — safety checks
│   │   ├── idempotency.py     # DynamoDB conditional write lock
│   │   ├── blast_radius.py    # Blast radius config generator
│   │   ├── circuit_breaker.py # Per-tenant action rate limiter
│   │   └── tenant_validator.py # Cross-tenant 403 checks
│   │
│   ├── verifier/              # Component 4 — verification
│   │   ├── evaluator.py       # Success condition evaluation
│   │   └── escalation.py      # Escalation bundle generation (LLM + template)
│   │
│   ├── audit/                 # Component 5 — audit trail
│   │   ├── writer.py          # S3 Object Lock writer
│   │   └── schema.py          # Audit record JSON schema
│   │
│   ├── rag/                   # RAG for runbook retrieval
│   │   ├── indexer.py         # FAISS index builder
│   │   └── retriever.py       # Similarity search
│   │
│   ├── models/                # Pydantic models
│   ├── schemas/               # JSON Schema definitions
│   └── main.py                # FastAPI app entrypoint
│
├── tests/
│   ├── test_detect.py
│   ├── test_decide.py
│   ├── test_verify.py
│   ├── test_safety.py
│   ├── test_tenant_isolation.py
│   └── test_fallback.py
│
├── runbooks/                  # Runbook JSON definitions
│   ├── restart_deployment.json
│   ├── patch_memory_limit.json
│   ├── scale_replicas.json
│   ├── rollout_undo.json
│   └── rotate_secret.json
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

# Recommended Tech Stack

| Layer | Technology | Rationale |
|--------|------------|-----------|
| API | FastAPI | Async, auto-docs, Pydantic integration |
| Validation | Pydantic v2 | Schema enforcement matching contract JSON schemas |
| AI Model (Primary) | Claude 3 Haiku (Amazon Bedrock) | Low latency (~1-2s), low cost, sufficient for constrained selection |
| AI Model (Fallback) | Static decision tree (Python) | Deterministic, p99 < 500ms, no external dependency |
| Vector Database | FAISS (local) | Zero cost, no infra dependency, sufficient for ~10 runbooks |
| Anomaly Detection (Events) | Rule engine (Python) | Deterministic, ~0ms, handles binary event signals |
| Anomaly Detection (Metrics) | RRCF (`rrcf` PyPI) + NumPy | Streaming anomaly scoring via Robust Random Cut Forest, no batch retrain needed |
| Logging & Observability | OpenTelemetry SDK + Prometheus client | Traces + structured logs + `/metrics` endpoint (Prometheus format per Deployment Contract §8) |
| Idempotency Lock | DynamoDB (conditional write) | Per Deployment Contract §4 |
| Audit Trail | Amazon S3 Object Lock (Compliance mode) | Per Deployment Contract §4, SOC2 compliance |
| Cost Tracking | DynamoDB (per-tenant counter) | Track daily Bedrock spend per tenant |
| Testing | pytest + httpx (async) | FastAPI test client |

---

# Five Patterns — Three Implemented + Two Designed

## Three Implemented Patterns (Built + Tested)

| # | Pattern | signal_name(s) | Detection Logic | Decision (action) | pattern_type | Verify Conditions |
|---|---------|----------------|-----------------|-------------------|---|---|
| 1 | Pod OOMKilled | `pod_oom_event` + `container_resource_usage` | OOM event present AND memory usage > 90% of limit | `PATCH_MEMORY_LIMIT` (increase by 50%, cap 4096MB) | `urgent` | `pod_ready == true`, `restart_count_no_increase == true`, `container_memory_usage_pct < 80` |
| 2 | Queue Backlog | `queue_backlog` + `service_throughput_rps` | Queue depth > 10,000 AND increasing over 3 data points | `SCALE_REPLICAS` (replicas × 2, cap 10) | `deferred` | `queue_depth_decreasing == true`, `worker_pods_ready == true` |
| 3 | Service Unhealthy | `service_unhealthy` + `service_error_rate` | Unhealthy event present AND error rate > 5% | `RESTART_DEPLOYMENT` (grace_period_seconds=30) | `urgent` | `pod_ready == true`, `restart_count_no_increase == true`, `service_error_rate < 0.05` |

## Two Designed Patterns (Paper Playbook + Diagram)

### Pattern 4 — Certificate Expiry → Rotate Secret

- **Detection**: `secret_expiry_warning` signal with `value ≤ 7` (days remaining).
- **Decision**: `ROTATE_SECRET` action targeting the secret identified in `labels.secret_name`.
- **pattern_type**: `deferred` — rotation is planned, not an emergency.
- **Execution**: CDO creates new secret version in AWS Secrets Manager, updates K8s Secret, restarts pods that mount the secret.
- **Verify**: Check `secret_expiry_warning` value increased (new cert has future expiry), no `service_unhealthy` events post-rotation.

```text
Playbook Flow:

secret_expiry_warning (value ≤ 7 days)
       ↓
/v1/detect → anomaly_detected=true, suspected_fault_type="secret_expiry_warning"
       ↓
/v1/decide → action=ROTATE_SECRET, pattern_type="deferred"
       ↓
CDO: Create new secret version → Update K8s Secret → Rolling restart pods
       ↓
/v1/verify → Check new expiry > 30 days, services healthy
       ↓
DONE or ESCALATE
```

### Pattern 5 — Database Connection Pool Saturation → Restart Service

- **Detection**: `db_connection_pool_saturation` signal with `value ≥ 0.90` (90% pool usage).
- **Decision**: `RESTART_DEPLOYMENT` action to reset leaked connections.
- **pattern_type**: `urgent` — active connection exhaustion causes cascading failures.
- **Execution**: CDO performs rolling restart with grace_period_seconds=30 to drain existing connections.
- **Verify**: Check `db_connection_pool_saturation < 0.80`, `service_error_rate < 0.05`.

```text
Playbook Flow:

db_connection_pool_saturation (value ≥ 0.90)
       ↓
/v1/detect → anomaly_detected=true, suspected_fault_type="db_connection_pool_saturation"
       ↓
/v1/decide → action=RESTART_DEPLOYMENT, pattern_type="urgent"
       ↓
CDO: Capture pre-state → Rolling restart (grace=30s)
       ↓
/v1/verify → Check pool saturation < 80%, error rate < 5%
       ↓
DONE or ROLLBACK or ESCALATE
```

---

# Why This Architecture?

This hybrid architecture separates responsibilities for safety, determinism, and cost-effectiveness:

| Layer | Technology | Why |
|-------|-----------|-----|
| Anomaly Detection | Statistical models (rule + Z-score) | Fast (p99 < 300ms), cheap ($0), deterministic, no hallucination risk |
| Decision Making | LLM (Bedrock Claude) + RAG | Contextual reasoning for runbook selection, constrained to approved actions only |
| Decision Fallback | Static rule-based engine | Guarantees response when LLM is unavailable (cost cap, timeout, throttle, parse failure) |
| Safety | Deterministic policy engine | Never delegate safety to a probabilistic model |
| Verification | Rule-based condition evaluation | Deterministic pass/fail against telemetry data |
| Escalation | LLM summary + structured data | Human-readable incident summary with full machine-readable context |
| Audit | S3 Object Lock (append-only) | SOC2 compliance, tamper-evident, 90-day retention |

This architecture aligns with modern AIOps reference designs where AI assists operators but deterministic guardrails, safety policies, and workflow orchestration remain outside the LLM. The LLM is treated as a "constrained advisor" — it can only choose from pre-approved options and its output is validated before execution.