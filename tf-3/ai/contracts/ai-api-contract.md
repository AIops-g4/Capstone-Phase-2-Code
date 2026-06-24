# AI API Contract - Generic Multi-Tenant Self-Heal Platform

<!-- Owner: Architecture & Platform Infrastructure Team
     Signed by: Principal AI Architect + Lead Platform Engineers
     Date signed: 2026-06-25
     🔒 FREEZE - no change without formal change request -->

## 1. Mục đích

Tài liệu này định nghĩa **Giao diện lập trình ứng dụng (API Endpoints)** do bộ phận AI cung cấp (expose) và bộ phận hạ tầng CDO tích hợp tiêu thụ (consume). Cam kết kỹ thuật này đảm bảo chu trình tự động khắc phục lỗi tự động (Self-Healing Loop) hoạt động an toàn và đồng bộ giữa các hệ thống:

```text
Phát hiện Bất thường (/v1/detect) ──> Lập Kế hoạch (/v1/decide) ──> CDO Thực thi ──> Xác thực kết quả (/v1/verify)
```

---

## 2. Quy tắc chung & Bảo mật

* **Đường dẫn cơ sở (API Path)**: `/v1/`
* **Xác thực (Authentication)**: Sử dụng **IAM SigV4** cho toàn bộ các cuộc gọi liên dịch vụ (inter-service calls).
* **Tính bất biến (Idempotency)**: Các yêu cầu ghi/thay đổi trạng thái (`/v1/decide` và `/v1/verify`) bắt buộc gửi kèm header `Idempotency-Key` (định dạng UUID v4) để chống xử lý trùng lặp.
* **Chế độ thử nghiệm (Simulation Mode)**: Khi chạy mô phỏng ngoại tuyến, CDO Platform sẽ gửi dữ liệu telemetry trích xuất từ lịch sử sau thời điểm lỗi xảy ra và truyền vào cửa sổ `post_telemetry_window` của `/v1/verify` để kiểm chứng.

---

## 3. Đặc tả các API Endpoints (JSON Schema Specification)

### 3.1. Endpoint Phát hiện Bất thường: `POST /v1/detect`

Nhận dữ liệu telemetry thời gian thực, thực thi mô hình phát hiện bất thường và đánh giá mức độ nghiêm trọng.

#### A. Request Headers
* `X-Tenant-Id` (string, Bắt buộc): Định danh duy nhất của Tenant (ví dụ: `"d3b07384-d113-495f-9f58-20d18d357d75"`).
* `Authorization` (string, Bắt buộc): AWS Signature Version 4.
* `X-Correlation-Id` (string, Tùy chọn): UUID phục vụ liên kết vết lỗi.

* **Mô tả trường dữ liệu yêu cầu (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `telemetry_window` | array | ✓ | Danh sách các điểm dữ liệu telemetry trong cửa sổ thời gian giám sát |
| `telemetry_window[].ts` | string (RFC3339) | ✓ | Mốc thời gian xảy ra sự kiện theo múi giờ UTC (độ chính xác mili-giây) |
| `telemetry_window[].tenant_id` | string (UUID v4) | ✓ | UUID v4 định danh Tenant phát sinh tín hiệu |
| `telemetry_window[].service` | string | ✓ | Tên định danh của microservice phát sinh dữ liệu |
| `telemetry_window[].signal_name` | string | ✓ | Tên tín hiệu giám sát (ví dụ: `service_error_rate`) |
| `telemetry_window[].value` | number / string | ✓ | Giá trị đo lường metric hoặc nội dung log lỗi |
| `telemetry_window[].labels` | object | optional | Đối tượng chứa các nhãn bổ sung về topology cụm và định danh trace |

* **Lược đồ Schema Yêu cầu**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DetectRequest",
  "type": "object",
  "properties": {
    "telemetry_window": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "ts": { "type": "string", "format": "date-time" },
          "tenant_id": { "type": "string", "format": "uuid" },
          "service": { "type": "string" },
          "signal_name": { "type": "string" },
          "value": { "type": ["number", "string"] },
          "labels": { "type": "object" }
        },
        "required": ["ts", "tenant_id", "service", "signal_name", "value"]
      }
    }
  },
  "required": ["telemetry_window"],
  "additionalProperties": false
}
```

* **Payload Yêu cầu Mẫu**:
```json
{
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
    "correlation_id": { "type": "string", "format": "uuid" }
  },
  "required": ["anomaly_detected", "severity", "confidence", "correlation_id"],
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
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
}
```

---

### 3.2. Endpoint Lập Kế hoạch: `POST /v1/decide`

Đối chiếu ngữ cảnh lỗi với thư viện Runbook để đưa ra kịch bản khắc phục tuần tự (Action Plan) cùng các giới hạn an toàn (Blast Radius).

#### A. Request Headers
* `X-Tenant-Id` (string, Bắt buộc): Định danh Tenant (ví dụ: `"d3b07384-d113-495f-9f58-20d18d357d75"`).
* `Idempotency-Key` (string, Bắt buộc): Khóa bảo đảm tính bất biến (UUID v4).

#### B. Request Body Schema
* **Mô tả trường dữ liệu yêu cầu (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `correlation_id` | string (UUID v4) | ✓ | Mã UUID v4 liên kết chuỗi vết từ bước phát hiện bất thường `/v1/detect` |
| `anomaly_context` | object | ✓ | Ngữ cảnh lỗi chi tiết nhận được từ bước phát hiện bất thường |
| `dry_run_mode` | boolean | ✓ | Chế độ chạy thử nghiệm (`true` để chỉ sinh log/audit và bỏ qua thực thi thật, `false` để thực thi thật) |

* **Lược đồ Schema Yêu cầu**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DecideRequest",
  "type": "object",
  "properties": {
    "correlation_id": { "type": "string", "format": "uuid" },
    "anomaly_context": { "type": "object" },
    "dry_run_mode": { "type": "boolean" }
  },
  "required": ["correlation_id", "anomaly_context", "dry_run_mode"],
  "additionalProperties": false
}
```

* **Payload Yêu cầu Mẫu**:
```json
{
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "anomaly_context": {
    "target_service": "order-service",
    "suspected_fault_type": "database_connection_failure",
    "system": "E-COMMERCE",
    "namespace": "production",
    "deployment": "order-service",
    "trigger_metric": "service_error_rate",
    "trigger_value": 0.15
  },
  "dry_run_mode": false
}
```

#### C. Response Body Schema
* **Mô tả trường dữ liệu phản hồi (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `matched_runbook` | string | ✓ | Tên của Runbook được đối chiếu và kích hoạt để giải quyết sự cố |
| `action_plan` | array | ✓ | Kế hoạch hành động chi tiết chứa các bước tự chữa lành tuần tự |
| `action_plan[].step` | integer | ✓ | Số thứ tự của bước thực hiện hành động (bắt đầu từ 1) |
| `action_plan[].action` | string (Enum) | ✓ | Loại hành động tự chữa lành (`RESTART_DEPLOYMENT`, `SCALE_UP_PODS`, `UPDATE_ENV_SECRET`, `ADJUST_MEMORY_LIMIT`, `DELETE_POD`) |
| `action_plan[].target` | string | ✓ | Đối tượng hạ tầng đích chịu tác động (ví dụ: `deployment/order-service`) |
| `action_plan[].params` | object | optional | Đối tượng chứa các tham số cấu hình bổ sung cho hành động |
| `action_plan[].params.namespace` | string | optional | Kubernetes namespace của đối tượng đích (Tùy chọn) |
| `action_plan[].params.grace_period_seconds` | integer | optional | Thời gian chờ tắt pod cũ một cách an toàn tính bằng giây (Tùy chọn) |
| `blast_radius_config` | object | ✓ | Cấu hình giới hạn vùng ảnh hưởng (Blast Radius) bảo đảm an toàn cho cụm |
| `blast_radius_config.max_pod_impact_pct` | integer | ✓ | Tỷ lệ phần trăm tối đa các pod bị tác động đồng thời trong cụm |
| `blast_radius_config.circuit_breaker_error_rate` | number | ✓ | Ngưỡng tỷ lệ lỗi tối đa cho phép để kích hoạt ngắt mạch hệ thống |
| `blast_radius_config.allowed_namespaces` | array | ✓ | Danh sách các Kubernetes namespace hợp lệ được phép thực thi hành động |

* **Lược đồ Schema Phản hồi**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "DecideResponse",
  "type": "object",
  "properties": {
    "matched_runbook": { "type": "string" },
    "action_plan": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "step": { "type": "integer" },
          "action": { 
            "type": "string", 
            "enum": ["RESTART_DEPLOYMENT", "SCALE_UP_PODS", "UPDATE_ENV_SECRET", "ADJUST_MEMORY_LIMIT", "DELETE_POD"] 
          },
          "target": { "type": "string" },
          "params": {
            "type": "object",
            "properties": {
              "namespace": { "type": "string" },
              "grace_period_seconds": { "type": "integer" }
            }
          }
        },
        "required": ["step", "action", "target"]
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
    }
  },
  "required": ["matched_runbook", "action_plan", "blast_radius_config"],
  "additionalProperties": false
}
```

* **Payload Phản hồi Mẫu**:
```json
{
  "matched_runbook": "DatabaseConnectionRecoveryRunbook",
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
  }
}
```

---

### 3.3. Endpoint Xác thực: `POST /v1/verify`

Đánh giá hiệu quả của hành động khắc phục lỗi dựa trên dữ liệu telemetry thu được sau sự kiện.

#### A. Request Headers
* `X-Tenant-Id` (string, Bắt buộc): Định danh Tenant (ví dụ: `"d3b07384-d113-495f-9f58-20d18d357d75"`).
* `Idempotency-Key` (string, Bắt buộc): Khóa bảo đảm tính bất biến (UUID v4).

#### B. Request Body Schema
* **Mô tả trường dữ liệu yêu cầu (Fields Description)**:

| Trường (Field) | Kiểu dữ liệu (Type) | Bắt buộc (Required) | Mô tả (Description) |
|---|---|---|---|
| `correlation_id` | string (UUID v4) | ✓ | Mã UUID v4 định danh toàn bộ chu trình tự chữa lành phục vụ truy vết |
| `action_executed` | object | ✓ | Chi tiết hành động tự chữa lành đã được CDO thực thi |
| `action_executed.action` | string | ✓ | Loại hành động đã thực thi (ví dụ: `RESTART_DEPLOYMENT`) |
| `action_executed.target` | string | ✓ | Đối tượng hạ tầng chịu tác động thực tế (ví dụ: `deployment/order-service`) |
| `action_executed.status` | string (Enum) | ✓ | Kết quả thực thi của hành động từ phía CDO (`COMPLETED` hoặc `FAILED`) |
| `action_executed.execution_time_seconds` | integer | optional | Tổng thời gian thực thi hành động tính bằng giây (Tùy chọn) |
| `post_telemetry_window` | array | ✓ | Chuỗi dữ liệu telemetry thu thập được sau khi hành động khắc phục hoàn tất |
| `post_telemetry_window[].ts` | string (RFC3339) | ✓ | Mốc thời gian của điểm dữ liệu telemetry |
| `post_telemetry_window[].tenant_id` | string (UUID v4) | ✓ | UUID v4 định danh Tenant |
| `post_telemetry_window[].service` | string | ✓ | Tên dịch vụ phát sinh telemetry |
| `post_telemetry_window[].signal_name` | string | ✓ | Tên tín hiệu giám sát |
| `post_telemetry_window[].value` | number / string | ✓ | Giá trị đo lường metric hoặc log lỗi thực tế |
| `post_telemetry_window[].labels` | object | optional | Đối tượng chứa siêu nhãn topology cụm và trace tương ứng |

* **Lược đồ Schema Yêu cầu**:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "VerifyRequest",
  "type": "object",
  "properties": {
    "correlation_id": { "type": "string", "format": "uuid" },
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
      "items": {
        "type": "object",
        "properties": {
          "ts": { "type": "string", "format": "date-time" },
          "tenant_id": { "type": "string", "format": "uuid" },
          "service": { "type": "string" },
          "signal_name": { "type": "string" },
          "value": { "type": ["number", "string"] },
          "labels": { "type": "object" }
        },
        "required": ["ts", "tenant_id", "service", "signal_name", "value"]
      }
    }
  },
  "required": ["correlation_id", "action_executed", "post_telemetry_window"],
  "additionalProperties": false
}
```

* **Payload Yêu cầu Mẫu**:
```json
{
  "correlation_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
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

### KPI Target
- **p99 Latency**:
  - `/v1/detect`: < 300 ms
  - `/v1/decide`: < 500 ms
  - `/v1/verify`: < 500 ms
- **Availability**: 99.9%
- **Rate Limit**: Tối đa 120 requests/minute per tenant.

### API Error Codes
- **`400 Bad Request`**: Dữ liệu gửi lên không đúng định dạng schema. CDO cần log và kiểm tra code, **không tự động retry**.
- **`409 Conflict`**: Trùng lặp `Idempotency-Key` cho cùng một hành động đang xử lý.
- **`429 Too Many Requests`**: Vượt quá hạn mức rate limit. CDO cần thực hiện **Exponential Backoff** trước khi gọi lại.
- **`503 Service Unavailable`**: AI Engine bị sập hoặc quá tải. CDO **bắt buộc phải có luồng fallback nội bộ** (ví dụ: chuyển sang execute runbook tĩnh mặc định hoặc gửi thẳng escalation cho SRE).
