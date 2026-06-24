# Telemetry Contract - Generic Multi-Tenant Self-Heal Platform

<!-- Owner: Architecture & Platform Infrastructure Team
     Signed by: Principal AI Architect + Lead Platform Engineers
     Date signed: 2026-06-25
     🔒 FREEZE - no change without formal change request -->

## 1. Mục đích

Tài liệu này xác định **Hợp đồng Telemetry (Telemetry Specification)**. Hợp đồng định nghĩa cấu trúc, định dạng và quy chuẩn các tín hiệu giám sát (signals) mà bộ phận hạ tầng (Platform Infrastructure) có nhiệm vụ thu thập, chuẩn hóa từ cụm ứng dụng và truyền tải sang hệ thống trí tuệ nhân tạo (AI Engine) để phục vụ chẩn đoán lỗi tự động.

---

## 2. Quy tắc chung & Phân lập Tenant

* **Định danh Tenant (Tenant Scoping)**: Mọi điểm dữ liệu telemetry bắt buộc phải đính kèm định danh khách hàng (`tenant_id`) dưới dạng chuỗi UUID v4 để phục vụ cô lập dữ liệu.
* **Độ chính xác thời gian (Time Precision)**: Tất cả mốc thời gian (`ts`) bắt buộc phải tuân thủ định dạng RFC3339 UTC với độ chính xác đến mili-giây (ví dụ: `2026-06-25T10:30:00.123Z`).
* **Đóng gói dữ liệu (Payload Enrichment)**: Hệ thống tiền xử lý hạ tầng có trách nhiệm làm giàu (enrich) các siêu dữ liệu cấu trúc như Kubernetes namespace và deployment vào trường nhãn (`labels`) của telemetry trước khi truyền đi.

---

## 3. Lược đồ Dữ liệu Telemetry (JSON Schema)

Để đảm bảo tính linh hoạt và dễ dàng kiểm thử tự động, cấu trúc của mọi điểm dữ liệu telemetry được chuẩn hóa bằng lược đồ JSON Schema dưới đây. Định nghĩa này thay thế toàn bộ các bảng thuộc tính thủ công.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TelemetryDataPoint",
  "description": "Lược đồ chuẩn hóa cho một điểm dữ liệu telemetry gửi sang AI Engine",
  "type": "object",
  "properties": {
    "ts": {
      "type": "string",
      "format": "date-time",
      "description": "Timestamp xảy ra sự kiện theo chuẩn RFC3339 UTC"
    },
    "tenant_id": {
      "type": "string",
      "format": "uuid",
      "description": "UUID v4 định danh duy nhất của Tenant/Khách hàng"
    },
    "service": {
      "type": "string",
      "description": "Tên định danh của microservice phát sinh dữ liệu"
    },
    "signal_name": {
      "type": "string",
      "enum": [
        "service_error_rate",
        "service_latency_p95",
        "container_resource_usage",
        "application_log_event",
        "distributed_trace_error_event"
      ],
      "description": "Tên tín hiệu được định nghĩa trong hợp đồng"
    },
    "value": {
      "type": [
        "number",
        "string"
      ],
      "description": "Giá trị đo lường (đối với metric) hoặc thông điệp lỗi (đối với log/event)"
    },
    "labels": {
      "type": "object",
      "properties": {
        "system": {
          "type": "string",
          "description": "Mã định danh hệ thống phần mềm"
        },
        "namespace": {
          "type": "string",
          "description": "Kubernetes namespace đang chạy tài nguyên (Tùy chọn)"
        },
        "deployment": {
          "type": "string",
          "description": "Tên đối tượng Kubernetes Deployment (Tùy chọn)"
        },
        "pod_name": {
          "type": "string",
          "description": "Tên pod phát sinh lỗi (đối với logs/resource metrics)"
        },
        "container": {
          "type": "string",
          "description": "Tên container phát sinh lỗi"
        },
        "endpoint": {
          "type": "string",
          "description": "Tên API endpoint hoặc phương thức gRPC"
        },
        "trace_id": {
          "type": "string",
          "description": "Mã định danh trace phục vụ liên kết vết lỗi"
        },
        "span_id": {
          "type": "string",
          "description": "Mã định danh span lỗi cụ thể"
        }
      },
      "required": [
        "system"
      ],
      "additionalProperties": true
    }
  },
  "required": [
    "ts",
    "tenant_id",
    "service",
    "signal_name",
    "value"
  ],
  "additionalProperties": false
}
```

---

## 4. Đặc tả các Tín hiệu Telemetry (Signals Specification)

### Tín hiệu 1: Tỷ lệ Lỗi Dịch vụ (`service_error_rate`)
* **Kiểu dữ liệu**: Gauge (Metric).
* **Mục đích**: Đo lường tỷ lệ các cuộc gọi dịch vụ bị lỗi (HTTP 5xx hoặc gRPC non-zero status) trên tổng số requests trong một cửa sổ trượt.
* **Giá trị**: Số thực từ `0.0` đến `1.0` (thể hiện phần trăm từ 0% đến 100%).
* **Payload mẫu**:
```json
{
  "ts": "2026-06-25T10:30:00.123Z",
  "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
  "service": "order-service",
  "signal_name": "service_error_rate",
  "value": 0.085,
  "labels": {
    "system": "E-COMMERCE",
    "endpoint": "/v1/orders/checkout",
    "namespace": "production",
    "deployment": "order-service"
  }
}
```

### Tín hiệu 2: Độ trễ Phân vị 95 (`service_latency_p95`)
* **Kiểu dữ liệu**: Gauge (Metric).
* **Mục đích**: Đo lường độ trễ ở phân vị thứ 95 của các cuộc gọi API để phát hiện hiện tượng nghẽn hoặc treo dịch vụ.
* **Giá trị**: Số thực thể hiện thời gian phản hồi bằng mili-giây (milliseconds).
* **Payload mẫu**:
```json
{
  "ts": "2026-06-25T10:30:00.123Z",
  "tenant_id": "6c8b4b2b-4d45-4209-a1b4-4b532d56a31c",
  "service": "payment-gateway",
  "signal_name": "service_latency_p95",
  "value": 450.5,
  "labels": {
    "system": "E-COMMERCE",
    "endpoint": "/v1/charge",
    "namespace": "production",
    "deployment": "payment-gateway"
  }
}
```

### Tín hiệu 3: Bộ nhớ Container sử dụng thực tế (`container_resource_usage`)
* **Kiểu dữ liệu**: Gauge (Metric).
* **Mục đích**: Giám sát tài nguyên phần cứng (RAM/CPU) của container để phát hiện rò rỉ bộ nhớ (Memory Leak) hoặc nguy cơ bị OOMKilled.
* **Giá trị**: Số nguyên thể hiện dung lượng bộ nhớ làm việc thực tế tính bằng Bytes.
* **Payload mẫu**:
```json
{
  "ts": "2026-06-25T10:30:00.000Z",
  "tenant_id": "6c8b4b2b-4d45-4209-a1b4-4b532d56a31c",
  "service": "inventory-service",
  "signal_name": "container_resource_usage",
  "value": 1073741824,
  "labels": {
    "system": "E-COMMERCE",
    "pod_name": "inventory-service-68d7f5c9b-abcde",
    "container": "main",
    "namespace": "production",
    "deployment": "inventory-service"
  }
}
```

### Tín hiệu 4: Sự kiện Log lỗi ứng dụng (`application_log_event`)
* **Kiểu dữ liệu**: Event (Log).
* **Mục đích**: Ghi nhận các log có mức độ nghiêm trọng `ERROR` hoặc chứa nội dung Stack Trace để mô hình AI phân tích sâu nguyên nhân ở cấp độ dòng code.
* **Giá trị**: Chuỗi văn bản thô chứa nội dung log lỗi và stack trace.
* **Payload mẫu**:
```json
{
  "ts": "2026-06-25T10:30:05.456Z",
  "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
  "service": "order-service",
  "signal_name": "application_log_event",
  "value": "NullPointerException: Cannot invoke 'Database.connect()' because 'conn' is null\n\tat com.ecommerce.OrderService.saveOrder(OrderService.java:102)",
  "labels": {
    "system": "E-COMMERCE",
    "pod_name": "order-service-5f8d9b7c-xyz12",
    "level": "ERROR",
    "namespace": "production",
    "deployment": "order-service"
  }
}
```

### Tín hiệu 5: Sự kiện lỗi giao dịch phân tán (`distributed_trace_error_event`)
* **Kiểu dữ liệu**: Event (Trace Span).
* **Mục đích**: Phát hiện các lỗi phát sinh trong chuỗi gọi dịch vụ liên kết (giao dịch phân tán) và xác định điểm đầu tiên phát sinh lỗi.
* **Giá trị**: Số nguyên thể hiện mã trạng thái lỗi của span giao dịch (ví dụ: HTTP Status Code hoặc gRPC Error Code).
* **Payload mẫu**:
```json
{
  "ts": "2026-06-25T10:30:04.999Z",
  "tenant_id": "6c8b4b2b-4d45-4209-a1b4-4b532d56a31c",
  "service": "frontend-web",
  "signal_name": "distributed_trace_error_event",
  "value": 503,
  "labels": {
    "system": "E-COMMERCE",
    "operation": "GET /v1/checkout",
    "trace_id": "d472bd0a6bda79d8d0b2852d8165cb97",
    "span_id": "cc3118e92762c87f",
    "namespace": "production",
    "deployment": "frontend-web"
  }
}
```
