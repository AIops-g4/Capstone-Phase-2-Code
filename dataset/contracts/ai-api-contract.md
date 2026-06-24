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
