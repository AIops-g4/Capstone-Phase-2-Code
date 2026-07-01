# CDO/Kubernetes Simulation Guide — `detect_decide_verify`

Tài liệu này mô tả cách giả lập một môi trường CDO/Kubernetes local đã nhúng AI Engine stage `detect_decide_verify`, có workload Kubernetes phát log, Prometheus-compatible metrics replay từ dataset, và chạy closed-loop thật qua API:

```text
/v1/detect → /v1/decide → CDO executor giả lập → /v1/verify
```

AI Engine **không trực tiếp mutate Kubernetes**. Nó chỉ đọc telemetry và trả `action_plan`; CDO/executor là thành phần áp dụng action.

## 1. Thành phần mô phỏng

| Thành phần production | Thành phần local hiện tại |
|---|---|
| Kubernetes cluster | `kind` cluster Docker: `aiops-cdo-sim` |
| Namespace workload | namespace `production` |
| Online Boutique pods | 5 deployment log replay: `checkoutservice`, `currencyservice`, `emailservice`, `productcatalogservice`, `recommendationservice` |
| Release/application logs | pod stdout từ `k8s_simulator/deploy_log_replay.yaml` |
| Prometheus metrics | `k8s_simulator/prometheus_dataset_sim.py` đọc `../dataset/checkoutservice_cpu/1/simple_metrics.csv` |
| CDO telemetry source | AI Engine gọi Kubernetes API + Prometheus simulator qua `telemetry_source.kind=k8s` |
| CDO executor | script test gửi `action_executed` giả lập vào `/v1/verify` |
| AI Engine | FastAPI/uvicorn `src.server:app` ở port `8050` |

## 2. Luồng thực tế đang chạy

```text
kind cluster aiops-cdo-sim
  ├─ production/checkoutservice pod phát logs
  ├─ production/currencyservice pod phát logs
  ├─ production/emailservice pod phát logs
  ├─ production/productcatalogservice pod phát logs
  └─ production/recommendationservice pod phát logs

Prometheus dataset simulator :19090
  └─ replay CPU/memory metrics từ dataset hiện tại

CDO test client
  ↓ POST /v1/detect với telemetry_source.kind=k8s
AI Engine
  ↓ đọc pod logs bằng Kubernetes API
  ↓ đọc metrics bằng Prometheus query_range simulator
  ↓ BOCPD/EWMA + BARO RCA
  ↓ POST /v1/decide
  ↓ trả action_plan
CDO executor giả lập
  ↓ POST /v1/verify với post-heal telemetry
AI Engine
  ↓ DONE / RETRY / ROLLBACK / ESCALATE
```

## 3. Setup môi trường

> Lưu ý: cần active đúng conda env `capstone` để có `kubectl`.

```bash
source /home/duckq1u/miniconda3/etc/profile.d/conda.sh
conda activate capstone
export PATH="$PWD/.local/bin:$PATH"
```

Công cụ đã dùng:

- `kubectl` từ conda env `capstone`
- `kind` local tại `.local/bin/kind`
- Docker local

## 4. Tạo local Kubernetes cluster bằng kind

Từ repo root:

```bash
mkdir -p .local/bin
curl -fsSL -o .local/bin/kind https://kind.sigs.k8s.io/dl/v0.24.0/kind-linux-amd64
chmod +x .local/bin/kind

source /home/duckq1u/miniconda3/etc/profile.d/conda.sh
conda activate capstone
export PATH="$PWD/.local/bin:$PATH"

kind create cluster --name aiops-cdo-sim
kubectl wait --for=condition=Ready node/aiops-cdo-sim-control-plane --timeout=120s
kubectl get nodes
```

Kiểm tra context:

```bash
kubectl config current-context
# kind-aiops-cdo-sim
```

## 5. Deploy workload phát release logs

```bash
source /home/duckq1u/miniconda3/etc/profile.d/conda.sh
conda activate capstone
export PATH="$PWD/.local/bin:$PATH"

kubectl apply -f ai/ai-engine/detect_decide_verify/k8s_simulator/deploy_log_replay.yaml
kubectl -n production get pods -o wide
kubectl -n production logs deploy/checkoutservice --tail=5
```

File manifest:

```text
ai/ai-engine/detect_decide_verify/k8s_simulator/deploy_log_replay.yaml
```

## 6. Chạy Prometheus-compatible metrics simulator

```bash
cd ai/ai-engine/detect_decide_verify
source /home/duckq1u/miniconda3/etc/profile.d/conda.sh
conda activate capstone

/home/duckq1u/miniconda3/envs/capstone/bin/python \
  k8s_simulator/prometheus_dataset_sim.py \
  --dataset-dir ../dataset \
  --service-fault checkoutservice_cpu \
  --run-id 1 \
  --host 127.0.0.1 \
  --port 19090
```

Health check:

```bash
curl -s http://127.0.0.1:19090/-/ready
```

Log runtime hiện tại:

```text
ai/ai-engine/dataset/benchmark_reports/k8s_prometheus_dataset_sim.log
```

## 7. Chạy AI Engine ở mode đọc K8s telemetry

Khuyến nghị dùng uvicorn nhiều worker để health/readiness vẫn phản hồi trong lúc `/v1/detect` đang tính toán BOCPD/BARO:

```bash
cd ai/ai-engine/detect_decide_verify
source /home/duckq1u/miniconda3/etc/profile.d/conda.sh
conda activate capstone

PYTHONUNBUFFERED=1 \
TELEMETRY_RUNTIME_MODE=production \
DEFAULT_TELEMETRY_SOURCE_KIND=k8s \
K8S_CONTEXT=kind-aiops-cdo-sim \
K8S_NAMESPACE=production \
K8S_LABEL_SELECTOR='app.kubernetes.io/part-of=online-boutique' \
K8S_IN_CLUSTER=False \
K8S_METRICS_PROVIDER=prometheus \
PROMETHEUS_BASE_URL=http://127.0.0.1:19090 \
USE_LLM_DECISION=False \
USE_LLM_FAULT_TYPE=False \
/home/duckq1u/miniconda3/envs/capstone/bin/python -m uvicorn src.server:app \
  --host 127.0.0.1 \
  --port 8050 \
  --workers 2
```

Health check:

```bash
curl -s http://127.0.0.1:8050/health | jq
```

Log runtime hiện tại:

```text
ai/ai-engine/dataset/benchmark_reports/k8s_ai_engine_server.log
```

## 8. Kết quả closed-loop đã chạy

Report:

```text
ai/ai-engine/dataset/benchmark_reports/k8s_closed_loop_report.json
```

Tóm tắt kết quả hiện tại:

```json
{
  "success": true,
  "detect": {
    "anomaly_detected": true,
    "anomaly_context": {
      "target_service": "emailservice",
      "suspected_fault_type": "cpu",
      "system": "E-COMMERCE",
      "namespace": "production",
      "deployment": "deployment/emailservice",
      "trigger_metric": "emailservice_mem",
      "trigger_value": 43241472.0
    },
    "service_top_k": [
      "emailservice",
      "recommendationservice",
      "productcatalogservice",
      "checkoutservice",
      "currencyservice"
    ]
  },
  "decide": {
    "matched_runbook": "CPUSaturationRecoveryRunbook",
    "action_plan": [
      {
        "step": 1,
        "action": "SCALE_REPLICAS",
        "target": "deployment/emailservice",
        "params": {
          "namespace": "production",
          "replicas": 3
        }
      }
    ]
  },
  "verify": {
    "success": true,
    "regression_detected": false,
    "next_action": "DONE"
  }
}
```

## 9. File được thêm cho K8s simulation

```text
.local/bin/kind
ai/ai-engine/detect_decide_verify/k8s_simulator/deploy_log_replay.yaml
ai/ai-engine/detect_decide_verify/k8s_simulator/prometheus_dataset_sim.py
ai/ai-engine/dataset/benchmark_reports/k8s_ai_engine_server.log
ai/ai-engine/dataset/benchmark_reports/k8s_prometheus_dataset_sim.log
ai/ai-engine/dataset/benchmark_reports/k8s_closed_loop_report.json
```

## 10. Dừng môi trường

Dừng process local:

```bash
pkill -f 'uvicorn src.server:app' || true
pkill -f 'python -m src.server' || true
pkill -f 'prometheus_dataset_sim.py' || true
```

Xóa cluster kind nếu muốn reset hoàn toàn:

```bash
source /home/duckq1u/miniconda3/etc/profile.d/conda.sh
conda activate capstone
export PATH="$PWD/.local/bin:$PATH"
kind delete cluster --name aiops-cdo-sim
```

## 11. Giới hạn mô phỏng hiện tại

- Workload Kubernetes là pod phát log synthetic, chưa phải Online Boutique thật.
- Metrics được replay từ dataset qua Prometheus-compatible API, chưa phải Prometheus scrape thật.
- CDO executor vẫn là mô phỏng: chưa mutate Kubernetes resource thật, đúng với ranh giới AI Engine chỉ trả action plan.
- Dataset đang replay `checkoutservice_cpu/1`, nhưng RCA trong run hiện tại chọn `emailservice(cpu)` do logic BARO trên metric replay. Đây là kết quả thật của pipeline hiện tại, cần phân tích thêm nếu muốn align tuyệt đối với ground truth dataset.
