# AIOps Decide Service — Runbook Matching & Action Planning

Standalone **`POST /v1/decide`** service per `ai-api-contract.md`.  
The `detect/` folder is unchanged; CDO calls detect then decide separately.

## Setup

```powershell
cd decide
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Or use the parent venv at `../.venv`.

## Download RE2 & build labels

```powershell
python scripts/setup_dataset.py
```

Downloads RE2 from Google Drive (same ID as `detect/README.md`), extracts to `../dataset/` (shared with detect), writes:
- `../dataset/ground_truth.json`
- `../dataset/runbooks.json`

## Start decide API (port **8051**)

```powershell
python -m uvicorn src.server:app --host 127.0.0.1 --port 8051 --reload
```

Swagger: http://127.0.0.1:8051/docs

## Benchmark on RE2

```powershell
python scripts/benchmark.py
```

Output: `benchmark_report_re2.json` (runbook accuracy + p99 latency).

## Smoke test (server must be running)

```powershell
python scripts/test_api.py
```

## Fault → Runbook mapping

| `suspected_fault_type` | Runbook |
|------------------------|---------|
| cpu | CPUSaturationRecoveryRunbook |
| mem | MemoryLeakRecoveryRunbook |
| delay | NetworkLatencyRecoveryRunbook |
| loss | PacketLossRecoveryRunbook |
| disk | DiskIORecoveryRunbook |
| socket | SocketExhaustionRecoveryRunbook |
| other | DefaultRecoveryRunbook |

## Contract notes

- **No `rollback_snapshot`** in response — CDO captures K8s state before execute (contract §3.2).
- **Suppression**: duplicate/correlated symptoms return empty `action_plan`.
- **LLM**: set `DECIDER_TYPE=llm` + AWS credentials; auto fallback to rule-based on error/cost cap.

## Integration with detect

```text
CDO → detect:8050/v1/detect  → anomaly_context
CDO → decide:8051/v1/decide  → action_plan
```
