# AIOps AI Engine — Detect + Decide + Verify E2E Stage

Stage `detect_decide_verify` là bản đầy đủ của AI Engine cho vòng lặp self-healing:

1. `POST /v1/detect` — phát hiện anomaly + RCA service/fault.
2. `POST /v1/decide` — chọn runbook và sinh action plan.
3. `POST /v1/verify` — verify kết quả sau khi CDO/executor thực thi action.

Stage này được thiết kế để dùng chung cho nhiều team CDO. Những phần phụ thuộc kiến trúc từng team như service list, dependency graph, fault → runbook mapping, namespace, deployment template và runbook catalog phải được đặt trong **platform profile JSON**, không hardcode trong code.

---

## 1. Cấu trúc file quan trọng

```text
ai/ai-engine/detect_decide_verify/
├── .env
├── .env.example
├── README.md
├── scripts/
│   └── benchmark_e2e.py
└── src/
    ├── config.py
    ├── server.py
    ├── engine.py
    ├── correlation_analyzer.py
    ├── self_healer.py
    ├── incident.py
    ├── verifier.py
    └── llm.py

ai/ai-engine/dataset/
├── platform_profile.schema.json
├── platform_profile_online_boutique.json
├── runbooks.json
├── dependency_graph.json
├── ground_truth.json
└── benchmark_reports/
    └── benchmark_e2e.json
```

### File profile chính

```text
ai/ai-engine/dataset/platform_profile_online_boutique.json
```

Đây là profile mẫu hiện tại. Với 2 team CDO, nên tạo 2 file riêng:

```text
ai/ai-engine/dataset/platform_profile_cdo_team_a.json
ai/ai-engine/dataset/platform_profile_cdo_team_b.json
```

Sau đó chọn profile bằng biến môi trường:

```env
PLATFORM_PROFILE_PATH=../dataset/platform_profile_cdo_team_a.json
```

---

## 2. Platform profile JSON là gì?

Platform profile gom tất cả thông tin phụ thuộc kiến trúc/runbook của từng team CDO:

- tên system/app
- namespace mặc định
- deployment template
- service list
- metric type list
- dependency graph dùng cho symptom suppression
- fault type → runbook mapping
- runbook catalog

Schema đầy đủ nằm tại:

```text
ai/ai-engine/dataset/platform_profile.schema.json
```

---

## 3. JSON profile mẫu tối thiểu

```json
{
  "profile_name": "cdo-team-a-prod",
  "system": "TEAM-A-SYSTEM",
  "default_namespace": "team-a-prod",
  "default_deployment_template": "deployment/{{target_service}}",
  "default_service": "api-gateway",
  "allowed_namespaces": ["team-a-prod", "team-a-staging"],
  "services": ["api-gateway", "order-service", "payment-service"],
  "metric_types": ["cpu", "mem", "latency", "error", "socket", "diskio"],
  "fault_runbook_mapping": {
    "cpu": "TeamACPUScaleRunbook",
    "mem": "TeamAMemoryPatchRunbook",
    "delay": "TeamALatencyRestartRunbook",
    "loss": "TeamAPacketLossRunbook",
    "disk": "TeamADiskIORunbook",
    "socket": "TeamASocketScaleRunbook",
    "unknown": "TeamADefaultRunbook"
  },
  "dependency_graph": {
    "api-gateway": ["order-service", "payment-service"],
    "order-service": ["payment-service"]
  },
  "runbooks": {
    "TeamACPUScaleRunbook": {
      "name": "TeamACPUScaleRunbook",
      "description": "Scale replicas when CPU saturation is detected.",
      "pattern_type": "urgent",
      "action_plan": [
        {
          "step": 1,
          "action": "SCALE_REPLICAS",
          "target": "deployment/{{target_service}}",
          "params": {
            "namespace": "team-a-prod",
            "replicas": 3
          }
        }
      ],
      "blast_radius_config": {
        "max_pod_impact_pct": 25,
        "circuit_breaker_error_rate": 0.2,
        "allowed_namespaces": ["team-a-prod", "team-a-staging"]
      },
      "verify_policy": {
        "window_seconds": 120,
        "success_conditions": ["pod_ready == true"]
      }
    },
    "TeamADefaultRunbook": {
      "name": "TeamADefaultRunbook",
      "description": "Fallback restart for unknown faults.",
      "pattern_type": "urgent",
      "action_plan": [
        {
          "step": 1,
          "action": "RESTART_DEPLOYMENT",
          "target": "deployment/{{target_service}}",
          "params": {
            "namespace": "team-a-prod",
            "grace_period_seconds": 30
          }
        }
      ],
      "blast_radius_config": {
        "max_pod_impact_pct": 25,
        "circuit_breaker_error_rate": 0.2,
        "allowed_namespaces": ["team-a-prod", "team-a-staging"]
      },
      "verify_policy": {
        "window_seconds": 120,
        "success_conditions": ["pod_ready == true"]
      }
    }
  }
}
```

> Lưu ý: các giá trị trong `fault_runbook_mapping` phải match key trong `runbooks`.

---

## 4. JSON Schema validation

Schema:

```text
../dataset/platform_profile.schema.json
```

Validate profile hiện tại:

```bash
cd ai/ai-engine/detect_decide_verify
/home/duckq1u/miniconda3/envs/capstone/bin/python - <<'PY'
import json
from pathlib import Path
import jsonschema

schema = json.loads(Path('../dataset/platform_profile.schema.json').read_text())
profile = json.loads(Path('../dataset/platform_profile_online_boutique.json').read_text())
jsonschema.validate(profile, schema)
print('PASS')
PY
```

Validate profile của team A:

```bash
cd ai/ai-engine/detect_decide_verify
/home/duckq1u/miniconda3/envs/capstone/bin/python - <<'PY'
import json
from pathlib import Path
import jsonschema

schema = json.loads(Path('../dataset/platform_profile.schema.json').read_text())
profile = json.loads(Path('../dataset/platform_profile_cdo_team_a.json').read_text())
jsonschema.validate(profile, schema)
print('PASS')
PY
```

---

## 5. Thiết lập `.env`

Copy từ example nếu chưa có:

```bash
cd ai/ai-engine/detect_decide_verify
cp .env.example .env
```

Các biến quan trọng:

```env
DATASET_DIR=../dataset
GROUND_TRUTH_PATH=../dataset/ground_truth.json
RUNBOOKS_PATH=../dataset/runbooks.json
DEPENDENCY_GRAPH_PATH=../dataset/dependency_graph.json
PLATFORM_PROFILE_PATH=../dataset/platform_profile_online_boutique.json
```

Với CDO team A:

```env
PLATFORM_PROFILE_PATH=../dataset/platform_profile_cdo_team_a.json
```

Với CDO team B:

```env
PLATFORM_PROFILE_PATH=../dataset/platform_profile_cdo_team_b.json
```

Các biến override profile nếu cần:

```env
SYSTEM_NAME=E-COMMERCE
DEFAULT_NAMESPACE=production
DEFAULT_DEPLOYMENT_TEMPLATE=deployment/{{target_service}}
DEFAULT_SERVICE=checkoutservice
ALLOWED_NAMESPACES=production,default
SERVICES_LIST=checkoutservice,currencyservice,emailservice
METRIC_TYPES_LIST=cpu,mem,latency,error,socket,diskio
```

Khuyến nghị: nếu đã cấu hình trong `PLATFORM_PROFILE_PATH`, chỉ override bằng env khi thật sự cần.

---

## 6. Cấu hình LLM cho `/v1/decide`

Mặc định tắt LLM để benchmark deterministic:

```env
USE_LLM_DECISION=False
```

Bật LLM khi có credential:

```env
USE_LLM_DECISION=True
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=...
```

Hoặc Anthropic:

```env
USE_LLM_DECISION=True
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=...
```

Hoặc Bedrock:

```env
USE_LLM_DECISION=True
LLM_PROVIDER=bedrock
LLM_MODEL=us.anthropic.claude-3-5-sonnet-20241022-v2:0
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_SESSION_TOKEN=
AWS_ENDPOINT_URL=
```

### LLM JSON guardrail

`src/self_healer.py` có `LLMDecisionOutputParser`, tương tự structured output parser:

- extract JSON object
- validate required keys
- validate runbook thuộc profile `runbooks`
- validate action thuộc enum contract
- validate target/namespace
- invalid thì fallback rule-based

Do đó LLM chỉ hỗ trợ `/v1/decide`; nó không execute Kubernetes trực tiếp.

---

## 7. Chạy API server

```bash
cd ai/ai-engine/detect_decide_verify
/home/duckq1u/miniconda3/envs/capstone/bin/python -m src.server
```

Mặc định:

```text
http://127.0.0.1:8050
```

Health check:

```bash
curl http://127.0.0.1:8050/health
```

---

## 8. Endpoint E2E

### `POST /v1/detect`

Input: telemetry window.

Output: anomaly + RCA context.

```json
{
  "anomaly_detected": true,
  "anomaly_context": {
    "target_service": "checkoutservice",
    "suspected_fault_type": "cpu",
    "system": "E-COMMERCE",
    "namespace": "production",
    "deployment": "deployment/checkoutservice"
  },
  "confidence": 0.9,
  "reasoning": "...",
  "correlation_id": "..."
}
```

### `POST /v1/decide`

Input: `anomaly_context` từ detect.

Output: runbook + action plan.

```json
{
  "matched_runbook": "CPUSaturationRecoveryRunbook",
  "pattern_type": "urgent",
  "action_plan": [
    {
      "step": 1,
      "action": "SCALE_REPLICAS",
      "target": "deployment/checkoutservice",
      "params": {
        "namespace": "production",
        "replicas": 3
      }
    }
  ],
  "blast_radius_config": {
    "max_pod_impact_pct": 25,
    "circuit_breaker_error_rate": 0.2,
    "allowed_namespaces": ["production", "default"]
  },
  "verify_policy": {
    "window_seconds": 120,
    "success_conditions": ["pod_ready == true"]
  }
}
```

### `POST /v1/verify`

Input: action đã được CDO/executor thực thi + post-healing telemetry.

Output:

```json
{
  "success": true,
  "regression_detected": false,
  "next_action": "DONE"
}
```

---

## 9. Chạy benchmark E2E

```bash
cd ai/ai-engine/detect_decide_verify
/home/duckq1u/miniconda3/envs/capstone/bin/python scripts/benchmark_e2e.py --sample-size 90 --engine baro --top-k 3
```

Report lưu tại:

```text
../dataset/benchmark_reports/benchmark_e2e.json
```

Report gồm:

- detection rate
- service Top-1/Top-3 accuracy
- fault type accuracy
- runbook accuracy E2E
- oracle runbook accuracy
- verify success rate
- full pipeline success
- fault confusion matrix
- fault accuracy by type

---

## 10. Checklist cho mỗi team CDO

1. Tạo file profile riêng:
   - `platform_profile_cdo_team_a.json`
   - `platform_profile_cdo_team_b.json`
2. Fill đúng:
   - `services`
   - `dependency_graph`
   - `fault_runbook_mapping`
   - `runbooks`
   - namespace/deployment template
3. Validate bằng `platform_profile.schema.json`.
4. Trỏ `.env`:
   ```env
   PLATFORM_PROFILE_PATH=../dataset/platform_profile_cdo_team_a.json
   ```
5. Chạy server hoặc benchmark.
6. Không hardcode service/team-specific logic trong code; nếu kiến trúc đổi thì sửa JSON profile.
