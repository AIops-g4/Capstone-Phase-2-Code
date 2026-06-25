# AI API Contract - Generic Multi-Tenant Self-Heal Platform

## 1. Mục đích

Tài liệu này định nghĩa **Giao diện lập trình ứng dụng (API Endpoints)** do bộ phận AI cung cấp (expose) và bộ phận hạ tầng CDO tích hợp tiêu thụ (consume). Cam kết kỹ thuật này đảm bảo chu trình tự động khắc phục lỗi tự động (Self-Healing Loop) hoạt động an toàn và đồng bộ giữa các hệ thống:

```text
Phát hiện Bất thường (/v1/detect) ──> Lập Kế hoạch (/v1/decide) ──> CDO Thực thi ──> Xác thực kết quả (/v1/verify)
```

---

## 2. Quy tắc chung & Bảo mật

* **Đường dẫn cơ sở (API Path)**: `/v1/`
* **Xác thực (Authentication)**: Xác thực nội bộ trong cụm EKS thông qua **Local Trust (mTLS tùy chọn)** và K8s Network Policies.
* **Tính bất biến (Idempotency)**: Tất cả các endpoint (`/v1/detect`, `/v1/decide` và `/v1/verify`) bắt buộc gửi kèm header `Idempotency-Key` (định dạng UUID v4) để chống xử lý trùng lặp.
* **Chế độ thử nghiệm (Simulation Mode)**: Khi chạy mô phỏng ngoại tuyến, CDO Platform sẽ gửi dữ liệu telemetry trích xuất từ lịch sử sau thời điểm lỗi xảy ra và truyền vào cửa sổ `post_telemetry_window` của `/v1/verify` để kiểm chứng.

---

## 3. Đặc tả các API Endpoints (JSON Schema Specification)

### 3.1. Endpoint Phát hiện Bất thường: `POST /v1/detect`

Nhận dữ liệu telemetry thời gian thực, thực thi mô hình phát hiện bất thường và đánh giá mức độ nghiêm trọng.

#### A. Request Headers
* `X-Tenant-Id` (string, Bắt buộc): Định danh duy nhất của Tenant (ví dụ: `"d3b07384-d113-495f-9f58-20d18d357d75"`).

* `X-Correlation-Id` (string, Tùy chọn): Mã UUID v4 liên kết chuỗi vết lỗi. Nếu không truyền, hệ thống sẽ tự sinh mới.
* `Idempotency-Key` (string, Bắt buộc): Khóa bảo đảm tính bất biến để chống trùng lặp yêu cầu (UUID v4).
* `X-Dry-Run-Mode` (string, Bắt buộc): Chế độ chạy thử nghiệm (`"true"` hoặc `"false"`).

* **Mô tả trường dữ liệu yêu cầu (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `correlation_id` | string (UUID v4) | optional | Mã UUID v4 để liên kết chuỗi vết lỗi. Nếu không truyền, hệ thống sẽ tự sinh mới |
| `idempotency_key` | string (UUID v4) | ✓ | Khóa chống trùng lặp xử lý yêu cầu |
| `dry_run_mode` | boolean | ✓ | Chế độ chạy thử nghiệm để đồng bộ luồng kiểm tra hệ thống |
| `telemetry_window` | array (of objects) | ✓ | Danh sách các điểm dữ liệu telemetry trong cửa sổ thời gian giám sát. Cấu trúc chi tiết của mỗi phần tử tuân thủ hoàn toàn theo đặc tả [Telemetry Contract](./telemetry-contract.md#3-lược-đồ-dữ-liệu-telemetry-json-schema--description) |

* **Lược đồ Schema Yêu cầu**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DetectRequest",
  "type": "object",
  "properties": {
    "correlation_id": {
      "type": "string",
      "format": "uuid",
      "description": "Mã UUID v4 liên kết chuỗi vết lỗi (Tùy chọn ở bước detect)"
    },
    "idempotency_key": {
      "type": "string",
      "format": "uuid",
      "description": "Khóa chống trùng lặp xử lý yêu cầu"
    },
    "dry_run_mode": {
      "type": "boolean",
      "description": "Chế độ chạy thử nghiệm để đồng bộ luồng hệ thống"
    },
    "telemetry_window": {
      "type": "array",
      "description": "Danh sách các điểm dữ liệu telemetry. Cấu trúc chi tiết của mỗi phần tử tuân thủ hoàn toàn theo hợp đồng telemetry-contract.md",
      "items": {
        "type": "object",
        "description": "Chi tiết cấu trúc và các trường dữ liệu xem tại contracts/telemetry-contract.md"
      }
    }
  },
  "required": ["idempotency_key", "dry_run_mode", "telemetry_window"],
  "additionalProperties": false
}
```

* **Payload Yêu cầu Mẫu**:
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
        "deployment": "order-service"
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
        "deployment": "order-service"
      }
    }
  ]
}
```

#### C. Response Body Schema
* **Mô tả trường dữ liệu phản hồi (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `anomaly_detected` | boolean | ✓ | Kết quả phát hiện bất thường (`true` nếu phát hiện lỗi, ngược lại `false`) |
| `severity` | number | ✓ | Mức độ nghiêm trọng của sự cố, giá trị từ `0.0` (thấp) đến `1.0` (nghiêm trọng) |
| `anomaly_context` | object | optional | Ngữ cảnh chi tiết của bất thường phát hiện (bắt buộc khi `anomaly_detected` là `true`) |
| `anomaly_context.target_service` | string | ✓ | Tên dịch vụ nghi ngờ bị lỗi chính |
| `anomaly_context.suspected_fault_type` | string | ✓ | Phân loại loại lỗi nghi ngờ (ví dụ: `database_connection_failure`) |
| `anomaly_context.system` | string | ✓ | Tên hệ thống nghiệp vụ (ví dụ: `E-COMMERCE`) |
| `anomaly_context.namespace` | string | optional | Kubernetes namespace nơi lỗi xảy ra (Tùy chọn) |
| `anomaly_context.deployment` | string | optional | Tên đối tượng Kubernetes Deployment quản lý dịch vụ (Tùy chọn) |
| `anomaly_context.trigger_metric` | string | optional | Tên tín hiệu telemetry trực tiếp kích hoạt cảnh báo lỗi (Tùy chọn) |
| `anomaly_context.trigger_value` | number | optional | Giá trị cụ thể của tín hiệu kích hoạt cảnh báo lỗi (Tùy chọn) |
| `confidence` | number | ✓ | Độ tin cậy của phân tích dự đoán lỗi, giá trị từ `0.0` đến `1.0` |
| `reasoning` | string (max 300 chars) | ✓ | Giải thích tóm tắt nguyên nhân hoặc logic phân tích phát hiện lỗi |
| `correlation_id` | string (UUID v4) | ✓ | Mã UUID v4 dùng để theo vết toàn bộ chu trình xử lý lỗi này |

* **Lược đồ Schema Phản hồi**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DetectResponse",
  "type": "object",
  "properties": {
    "anomaly_detected": { "type": "boolean" },
    "severity": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
    "anomaly_context": {
      "type": "object",
      "properties": {
        "target_service": { "type": "string" },
        "suspected_fault_type": { "type": "string" },
        "system": { "type": "string" },
        "namespace": { "type": "string" },
        "deployment": { "type": "string" },
        "trigger_metric": { "type": "string" },
        "trigger_value": { "type": "number" }
      },
      "required": ["target_service", "suspected_fault_type", "system"]
    },
    "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
    "reasoning": {
      "type": "string",
      "maxLength": 300,
      "description": "Giải thích ngắn gọn lý do phát hiện bất thường hoặc dự đoán lỗi"
    },
    "correlation_id": { "type": "string", "format": "uuid" }
  },
  "required": ["anomaly_detected", "severity", "confidence", "reasoning", "correlation_id"],
  "additionalProperties": false
}
```

* **Payload Phản hồi Mẫu**:
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
  "reasoning": "Tỷ lệ lỗi của order-service (15%) vượt ngưỡng an toàn 5% đồng thời xuất hiện lỗi NullPointerException liên tục trong stack trace.",
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
}
```

---

### 3.2. Endpoint Lập Kế hoạch: `POST /v1/decide`

Đối chiếu ngữ cảnh lỗi với thư viện Runbook để đưa ra kịch bản khắc phục tuần tự (Action Plan) cùng các giới hạn an toàn (Blast Radius).

#### A. Request Headers
* `X-Tenant-Id` (string, Bắt buộc): Định danh Tenant (ví dụ: `"d3b07384-d113-495f-9f58-20d18d357d75"`).

* `X-Correlation-Id` (string, Bắt buộc): Mã UUID v4 liên kết chuỗi vết từ bước `/v1/detect` truyền sang.
* `Idempotency-Key` (string, Bắt buộc): Khóa bảo đảm tính bất biến để chống trùng lặp yêu cầu (UUID v4).
* `X-Dry-Run-Mode` (string, Bắt buộc): Chế độ chạy thử nghiệm (`"true"` hoặc `"false"`).

#### B. Request Body Schema
* **Mô tả trường dữ liệu yêu cầu (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `correlation_id` | string (UUID v4) | ✓ | Mã UUID v4 liên kết chuỗi vết từ bước phát hiện bất thường `/v1/detect` |
| `idempotency_key` | string (UUID v4) | ✓ | Khóa chống trùng lặp xử lý yêu cầu |
| `dry_run_mode` | boolean | ✓ | Chế độ chạy thử nghiệm (`true` để chỉ sinh log/audit và bỏ qua thực thi thật, `false` để thực thi thật) |
| `anomaly_context` | object | ✓ | Ngữ cảnh lỗi chi tiết nhận được từ bước phát hiện bất thường |

* **Lược đồ Schema Yêu cầu**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DecideRequest",
  "type": "object",
  "properties": {
    "correlation_id": {
      "type": "string",
      "format": "uuid",
      "description": "Mã UUID v4 liên kết chuỗi vết từ bước phát hiện bất thường /v1/detect"
    },
    "idempotency_key": {
      "type": "string",
      "format": "uuid",
      "description": "Khóa chống trùng lặp xử lý yêu cầu"
    },
    "dry_run_mode": {
      "type": "boolean",
      "description": "Chế độ chạy thử nghiệm"
    },
    "anomaly_context": {
      "type": "object",
      "description": "Ngữ cảnh lỗi chi tiết"
    }
  },
  "required": ["correlation_id", "idempotency_key", "dry_run_mode", "anomaly_context"],
  "additionalProperties": false
}
```

* **Payload Yêu cầu Mẫu**:
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

#### C. Response Body Schema
* **Mô tả trường dữ liệu phản hồi (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `matched_runbook` | string | ✓ | Tên của Runbook được đối chiếu và kích hoạt để giải quyết sự cố |
| `pattern_type` | string (Enum) | ✓ | Phân loại luồng xử lý: `"urgent"` (Path B - Vá trực tiếp) hoặc `"deferred"` (Path A - GitOps) |
| `action_plan` | array | ✓ | Kế hoạch hành động chi tiết chứa các bước tự chữa lành tuần tự |
| `action_plan[].step` | integer | ✓ | Số thứ tự của bước thực hiện hành động (bắt đầu từ 1) |
| `action_plan[].action` | string (Enum) | ✓ | Loại hành động tự chữa lành (`RESTART_DEPLOYMENT`, `PATCH_MEMORY_LIMIT`, `SCALE_REPLICAS`, `ROLLOUT_UNDO`, `ROTATE_SECRET`) |
| `action_plan[].target` | string | ✓ | Đối tượng hạ tầng đích chịu tác động (ví dụ: `deployment/order-service`) |
| `action_plan[].params` | object | ✓ | Đối tượng chứa các tham số cấu hình bắt buộc cho hành động |
| `action_plan[].params.namespace` | string | ✓ | Kubernetes namespace của đối tượng đích |
| `action_plan[].params.container` | string | optional | Tên container chịu tác động (bắt buộc cho `PATCH_MEMORY_LIMIT`) |
| `action_plan[].params.memory_request_mb` | integer | optional | Cấu hình bộ nhớ request mới tính bằng MB (cho `PATCH_MEMORY_LIMIT`) |
| `action_plan[].params.memory_limit_mb` | integer | optional | Cấu hình bộ nhớ limit mới tính bằng MB (cho `PATCH_MEMORY_LIMIT`) |
| `action_plan[].params.replicas` | integer | optional | Số lượng replicas mong muốn mới (cho `SCALE_REPLICAS`) |
| `action_plan[].params.secret_name` | string | optional | Tên của secret cần rotate (cho `ROTATE_SECRET`) |
| `action_plan[].params.grace_period_seconds` | integer | optional | Thời gian chờ tắt pod cũ một cách an toàn tính bằng giây (cho `RESTART_DEPLOYMENT`) |
| `blast_radius_config` | object | ✓ | Cấu hình giới hạn vùng ảnh hưởng (Blast Radius) bảo đảm an toàn cho cụm |
| `blast_radius_config.max_pod_impact_pct` | integer | ✓ | Tỷ lệ phần trăm tối đa các pod bị tác động đồng thời trong cụm |
| `blast_radius_config.circuit_breaker_error_rate` | number | ✓ | Ngưỡng tỷ lệ lỗi tối đa cho phép để kích hoạt ngắt mạch hệ thống |
| `blast_radius_config.allowed_namespaces` | array | ✓ | Danh sách các Kubernetes namespace hợp lệ được phép thực thi hành động |
| `verify_policy` | object | ✓ | Chính sách xác thực sau khi thực hiện hành động tự chữa lành |
| `verify_policy.window_seconds` | integer | ✓ | Thời gian chờ tối thiểu (giây) trước khi CDOps thu thập telemetry để xác thực |
| `verify_policy.success_conditions` | array (of strings) | optional | Danh sách các điều kiện kiểm tra thành công (ví dụ: `pod_ready == true`) |
| `cost_cap_exceeded` | boolean | optional | Cờ báo hiệu chi phí gọi LLM Bedrock trong ngày của Tenant đã vượt hạn mức $50 (khi bằng `true`, hệ thống tự động chuyển sang chế độ dự phòng rule-based truyền thống, kế hoạch hành động vẫn có thể thực thi bình thường) |

* **Ghi chú quan trọng về `pattern_type` (Quy trình xử lý dành cho CDOps Executor)**:

> [!warning] 
> 
> CDOps Platform bắt buộc phải tuân thủ nghiêm ngặt quy trình xử lý khác biệt giữa hai loại `pattern_type` dưới đây để đảm bảo tính nhất quán của hạ tầng và tránh xung đột trạng thái (state drift):
> 
> 1. **Đối với `"pattern_type": "urgent"` (Path B - Vá trực tiếp / Hotfix)**:
>    * **Mục đích**: Áp dụng cho các sự cố khẩn cấp đe dọa trực tiếp tính liên tục của dịch vụ (như `pod_oom_event`, `service_unhealthy`).
>    * **Hành vi thực thi**: CDOps Executor thực thi hành động tự chữa lành **ngay lập tức** bằng cách gọi trực tiếp vào Kubernetes API Server (ví dụ: chạy lệnh patch tài nguyên, restart deployment trực tiếp).
>    * **Quy trình Safety Gate**: Kiểm tra giới hạn vùng ảnh hưởng (Blast Radius) theo thời gian thực trước khi thực thi. Bỏ qua luồng duyệt thủ công để tối ưu hóa thời gian phục hồi (RTO < 60 giây).
> 
> 2. **Đối với `"pattern_type": "deferred"` (Path A - Luồng đồng bộ GitOps)**:
>    * **Mục đích**: Áp dụng cho các sự cố mang tính chất tích lũy cấu hình lâu dài (như điều chỉnh giới hạn tài nguyên vĩnh viễn, tăng số lượng replicas do nghẽn hàng đợi `queue_backlog`).
>    * **Hành vi thực thi**: CDOps Platform **nghiêm cấm** việc ghi đè trực tiếp lên cụm Kubernetes. Thay vào đó, CDOps phải tự động **tạo một Git commit hoặc mở một Pull Request (PR)** cập nhật thông số cấu hình trên Git Repository quản lý manifest của dịch vụ nghiệp vụ (ví dụ: cập nhật file Helm `values.yaml` hoặc Kube manifest). Trạng thái mới sẽ được đồng bộ tự động xuống cụm K8s thông qua công cụ GitOps (như ArgoCD/FluxCD).
>    * **Quy trình Safety Gate**: Logic an toàn sẽ được tích hợp trực tiếp vào quá trình kiểm thử tự động của CI/CD pipeline hoặc luồng duyệt PR. CDOps chấp nhận độ trễ đồng bộ của GitOps (thường từ 2 - 5 phút).

* **Lược đồ Schema Phản hồi**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DecideResponse",
  "type": "object",
  "properties": {
    "matched_runbook": { "type": "string" },
    "pattern_type": { 
      "type": "string", 
      "enum": ["urgent", "deferred"] 
    },
    "action_plan": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "step": { "type": "integer" },
          "action": { 
            "type": "string", 
            "enum": ["RESTART_DEPLOYMENT", "PATCH_MEMORY_LIMIT", "SCALE_REPLICAS", "ROLLOUT_UNDO", "ROTATE_SECRET"] 
          },
          "target": { "type": "string" },
          "params": {
            "type": "object",
            "properties": {
              "namespace": { "type": "string" },
              "container": { "type": "string" },
              "memory_request_mb": { "type": "integer" },
              "memory_limit_mb": { "type": "integer" },
              "replicas": { "type": "integer" },
              "secret_name": { "type": "string" },
              "grace_period_seconds": { "type": "integer" }
            },
            "required": ["namespace"]
          }
        },
        "required": ["step", "action", "target", "params"]
      }
    },
    "blast_radius_config": {
      "type": "object",
      "properties": {
        "max_pod_impact_pct": { "type": "integer" },
        "circuit_breaker_error_rate": { "type": "number" },
        "allowed_namespaces": {
          "type": "array",
          "items": { "type": "string" }
        }
      },
      "required": ["max_pod_impact_pct", "circuit_breaker_error_rate", "allowed_namespaces"]
    },
    "verify_policy": {
      "type": "object",
      "properties": {
        "window_seconds": { "type": "integer" },
        "success_conditions": {
          "type": "array",
          "items": { "type": "string" }
        }
      },
      "required": ["window_seconds"]
    },
    "correlation_id": { "type": "string", "format": "uuid" },
    "idempotency_key": { "type": "string", "format": "uuid" },
    "dry_run_mode": { "type": "boolean" },
    "cost_cap_exceeded": { "type": "boolean" }
  },
  "required": ["matched_runbook", "pattern_type", "action_plan", "blast_radius_config", "verify_policy", "correlation_id", "idempotency_key", "dry_run_mode"],
  "additionalProperties": false
}
```

* **Payload Phản hồi Mẫu**:
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
        "namespace": "production",
        "container": "main",
        "memory_request_mb": 512,
        "memory_limit_mb": 768
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
      "container_memory_usage_pct < 80"
    ]
  },
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
  "dry_run_mode": false,
  "cost_cap_exceeded": false
}
```

---

### 3.3. Endpoint Xác thực: `POST /v1/verify`

Đánh giá hiệu quả của hành động khắc phục lỗi dựa trên dữ liệu telemetry thu được sau sự kiện.

#### A. Request Headers
* `X-Tenant-Id` (string, Bắt buộc): Định danh Tenant (ví dụ: `"d3b07384-d113-495f-9f58-20d18d357d75"`).

* `X-Correlation-Id` (string, Bắt buộc): Mã UUID v4 định danh toàn bộ chu trình tự chữa lành phục vụ truy vết.
* `Idempotency-Key` (string, Bắt buộc): Khóa bảo đảm tính bất biến (UUID v4).
* `X-Dry-Run-Mode` (string, Bắt buộc): Chế độ chạy thử nghiệm (`"true"` hoặc `"false"`).

#### B. Request Body Schema
* **Mô tả trường dữ liệu yêu cầu (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `correlation_id` | string (UUID v4) | ✓ | Mã UUID v4 định danh toàn bộ chu trình tự chữa lành phục vụ truy vết |
| `idempotency_key` | string (UUID v4) | ✓ | Khóa chống trùng lặp xử lý yêu cầu |
| `dry_run_mode` | boolean | ✓ | Chế độ chạy thử nghiệm để đồng bộ trạng thái thực thi với các bước trước |
| `action_executed` | object | ✓ | Chi tiết hành động tự chữa lành đã được CDO thực thi |
| `action_executed.action` | string | ✓ | Loại hành động đã thực thi (ví dụ: `RESTART_DEPLOYMENT`) |
| `action_executed.target` | string | ✓ | Đối tượng hạ tầng chịu tác động thực tế (ví dụ: `deployment/order-service`) |
| `action_executed.status` | string (Enum) | ✓ | Kết quả thực thi của hành động từ phía CDO (`COMPLETED` hoặc `FAILED`) |
| `action_executed.execution_time_seconds` | integer | optional | Tổng thời gian thực thi hành động tính bằng giây (Tùy chọn) |
| `post_telemetry_window` | array | ✓ | Chuỗi dữ liệu telemetry thu thập được sau khi hành động khắc phục hoàn tất. Cấu trúc chi tiết của mỗi phần tử tuân thủ hoàn toàn theo hợp đồng telemetry-contract.md |

* **Lược đồ Schema Yêu cầu**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "VerifyRequest",
  "type": "object",
  "properties": {
    "correlation_id": {
      "type": "string",
      "format": "uuid",
      "description": "Mã UUID v4 định danh toàn bộ chu trình tự chữa lành phục vụ truy vết"
    },
    "idempotency_key": {
      "type": "string",
      "format": "uuid",
      "description": "Khóa chống trùng lặp xử lý yêu cầu"
    },
    "dry_run_mode": {
      "type": "boolean",
      "description": "Chế độ chạy thử nghiệm"
    },
    "action_executed": {
      "type": "object",
      "properties": {
        "action": { "type": "string" },
        "target": { "type": "string" },
        "status": { "type": "string", "enum": ["COMPLETED", "FAILED"] },
        "execution_time_seconds": { "type": "integer" }
      },
      "required": ["action", "target", "status"]
    },
    "post_telemetry_window": {
      "type": "array",
      "description": "Chuỗi dữ liệu telemetry thu thập được sau khi hành động khắc phục hoàn tất. Cấu trúc chi tiết của mỗi phần tử tuân thủ hoàn toàn theo hợp đồng telemetry-contract.md",
      "items": {
        "type": "object",
        "description": "Chi tiết cấu trúc và các trường dữ liệu xem tại contracts/telemetry-contract.md"
      }
    }
  },
  "required": ["correlation_id", "idempotency_key", "dry_run_mode", "action_executed", "post_telemetry_window"],
  "additionalProperties": false
}
```

* **Payload Yêu cầu Mẫu**:
```json
{
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "idempotency_key": "d3b07384-d113-495f-9f58-20d18d357d75",
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
      "value": 0.00,
      "labels": { 
        "system": "E-COMMERCE",
        "namespace": "production",
        "deployment": "order-service"
      }
    }
  ]
}
```

#### C. Response Body Schema
* **Mô tả trường dữ liệu phản hồi (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `success` | boolean | ✓ | Xác nhận sự cố đã được khắc phục hoàn toàn (`true` nếu chỉ số phục hồi, ngược lại `false`) |
| `regression_detected` | boolean | ✓ | Phát hiện sự cố suy thoái mới phát sinh do tác dụng phụ của hành động khắc phục lỗi |
| `next_action` | string (Enum) | ✓ | Chỉ dẫn bước tiếp theo cho CDO Platform (`DONE`, `RETRY`, `ROLLBACK`, hoặc `ESCALATE`) |
| `escalation_bundle` | object | optional | Gói thông tin ngữ cảnh phong phú dùng để leo thang lên kỹ sư trực ban (khi `next_action` là `ESCALATE`) |
| `escalation_bundle.reason` | string | optional | Nguyên nhân cụ thể dẫn đến tự động khắc phục thất bại |
| `escalation_bundle.logs` | array | optional | Danh sách các log lỗi ứng dụng liên quan phục vụ chẩn đoán thủ công |
| `escalation_bundle.metrics` | object | optional | Bản tóm tắt các metric hệ thống liên quan tại thời điểm leo thang |

* **Lược đồ Schema Phản hồi**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "VerifyResponse",
  "type": "object",
  "properties": {
    "success": { "type": "boolean" },
    "regression_detected": { "type": "boolean" },
    "next_action": { 
      "type": "string", 
      "enum": ["DONE", "RETRY", "ROLLBACK", "ESCALATE"] 
    },
    "escalation_bundle": {
      "type": "object",
      "properties": {
        "reason": { "type": "string" },
        "logs": { "type": "array", "items": { "type": "string" } },
        "metrics": { "type": "object" }
      }
    }
  },
  "required": ["success", "regression_detected", "next_action"],
  "additionalProperties": false
}
```

* **Payload Phản hồi Mẫu**:
```json
{
  "success": true,
  "regression_detected": false,
  "next_action": "DONE"
}
```

---

## 4. Cam kết chất lượng dịch vụ (SLA) & Mã lỗi

### KPI Target & Throughput SLAs
- **p99 Latency**:
  - `/v1/detect`: < 300 ms
  - `/v1/decide`: < 3000 ms (Nới lỏng độ trễ khi gọi LLM AWS Bedrock; các kịch bản fallback rule-based bắt buộc < 500 ms)
  - `/v1/verify`: < 500 ms

#### Quy trình và Điều kiện Kích hoạt Chế độ Dự phòng Rule-Based (Fallback Rule-Based)
Để đảm bảo thời gian xử lý sự cố toàn trình (End-to-End SLO) của CDOps Platform luôn nằm trong giới hạn dưới 5 phút, AI Engine thiết lập cơ chế tự động chuyển đổi sang chế độ dự phòng Rule-Based (thời gian phản hồi p99 < 500 ms, dựa trên cây quyết định tĩnh thay vì gọi LLM Bedrock) khi xảy ra các điều kiện sau:
1. **Vượt hạn mức chi phí (Cost Cap Exceeded)**: Chi phí sử dụng AWS Bedrock tích lũy trong ngày của Tenant vượt quá **$50/ngày** (reset vào lúc 00:00:00 UTC). Phản hồi trả về sẽ chứa thuộc tính `"cost_cap_exceeded": true`.
2. **AWS Bedrock API bị giới hạn tần suất (Rate Limiting - HTTP 429)**: Khi dịch vụ AWS Bedrock trả về lỗi `429 Too Many Requests` và việc thực hiện thử lại (retry với exponential backoff) có nguy cơ đẩy tổng thời gian xử lý vượt quá ngân sách thời gian nội bộ (2000 ms).
3. **Lỗi hệ thống hoặc Thời gian chờ dịch vụ LLM Bedrock (Downtime & Timeouts)**: Khi dịch vụ AWS Bedrock gặp sự cố kết nối, phản hồi chậm (hơn 2500 ms) hoặc trả về các mã lỗi `5xx`.
4. **Lỗi phân tích cú pháp phản hồi (LLM Response Parse Failure)**: Khi mô hình LLM phản hồi dữ liệu không đúng cấu trúc JSON hoặc không vượt qua bộ kiểm tra schema nghiêm ngặt của `DecideResponse`. Hệ thống sẽ tự động dùng bộ rule engine tĩnh để sinh ra kế hoạch hành động an toàn và hợp lệ.

- **Availability**: 99.9%
- **Hạn mức Lưu lượng (Throughput SLAs & Rate Limit)**:
  - `/v1/detect`: Hạn mức 100 RPS (Requests Per Second) per tenant.
  - `/v1/decide`: Hạn mức 10 RPS per tenant.
  - `/v1/verify`: Hạn mức 10 RPS per tenant.
  - Vượt quá hạn mức trên sẽ kích hoạt cơ chế giới hạn tần suất (Rate Limiting).

### API Error Codes
- **`400 Bad Request`**: Dữ liệu gửi lên không đúng định dạng schema. CDO cần log và kiểm tra code, **không tự động retry**.
- **`401 Unauthorized`**: Yêu cầu bị từ chối do cấu hình Local Trust/mTLS không hợp lệ hoặc thiếu/sai phân quyền Tenant.
- **`409 Conflict`**: Trùng lặp `Idempotency-Key` cho cùng một hành động đang xử lý hoặc đã xử lý gần đây.
- **`429 Too Many Requests`**: Vượt quá hạn mức lưu lượng (RPS/RPM) được cam kết. Phản hồi sẽ đi kèm HTTP header **`Retry-After`** chỉ định rõ số giây cần chờ trước khi CDO thực hiện gọi lại (Exponential Backoff).
- **`503 Service Unavailable`**: AI Engine bị lỗi hệ thống hoặc quá tải. CDO **bắt buộc phải có luồng fallback nội bộ** (ví dụ: chuyển sang execute runbook tĩnh mặc định hoặc gửi thẳng escalation cho SRE).

---

## 5. Chính sách Quản lý Phiên bản & Quy trình Thay đổi (Versioning & Change-Request)

Hợp đồng API này được đóng băng ("FREEZE") để bảo đảm tính ổn định tích hợp. Mọi thay đổi trong tương lai phải tuân thủ quy trình sau:

### A. Phân loại Thay đổi (Change Classification)
1. **Thay đổi lớn (Breaking Changes)**:
   * Định nghĩa: Xóa trường bắt buộc, thay đổi kiểu dữ liệu của trường hiện tại, thay đổi định dạng phản hồi, hoặc xóa/thay đổi ý nghĩa mã lỗi.
   * Quy trình: Bắt buộc nâng cấp phiên bản hợp đồng lên `/v2`. Hệ thống phải hỗ trợ song song cả hai phiên bản (Dual-support) tối thiểu **30 ngày** để các bên hoàn tất chuyển đổi.
2. **Thay đổi nhỏ (Non-breaking Changes)**:
   * Định nghĩa: Thêm trường tùy chọn (optional) trong request/response, hoặc bổ sung mã lỗi mới không ảnh hưởng logic cũ.
   * Quy trình: Tăng số phiên bản phụ (minor bump), triển khai trực tiếp sau khi thông báo trước 5 ngày làm việc.

### B. Quy trình Thay đổi (Change-Request Process)
* Bước 1: Bên đề xuất gửi yêu cầu thay đổi hợp đồng (RFC - Request for Comments) bằng văn bản cho hội đồng kỹ thuật.
* Bước 2: Tổ chức họp đánh giá tác động với sự tham gia bắt buộc của AI Lead và các CDO Platform Leads.
* Bước 3: Sau khi thống nhất, cập nhật schema, chạy bộ test tự động và ký duyệt phiên bản hợp đồng mới.
