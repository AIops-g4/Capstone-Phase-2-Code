# Telemetry Contract - Task Force 3 (Self-Heal Engine)


## 1. Mục đích

Hợp đồng này định nghĩa các **telemetry signals** mà nhóm CDO (Platform Infrastructure) có nhiệm vụ phải thu thập, chuẩn hóa từ EKS Sandbox Cluster (hoặc mô phỏng từ offline dataset) và gửi cho AI Engine (Intelligence Layer) tiêu thụ. Các signals này được điều chỉnh để khớp chính xác với cấu trúc dữ liệu của **RE2 (Resource & Network Faults)** và **RE3 (Code-Level Faults)** nhằm đảm bảo phát hiện các lỗi của hệ thống Online Boutique.

---

## 2. Versioning & Simulation Rules

- **Current version**: `v1.0`
- **Quy tắc chạy mô phỏng (Offline Simulation Mode)**:
  * **Chèn Tenant ID**: Vì dữ liệu telemetry thô của hệ thống không chứa thông tin tenant, nhóm CDO Platform khi gửi dữ liệu mô phỏng bắt buộc phải **tự động chèn (inject)** trường `"tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75"` hoặc `"tenant_id": "6c8b4b2b-4d45-4209-a1b4-4b532d56a31c"` (tương ứng với từng nguồn kịch bản lỗi) vào mọi payload gửi sang AI Engine để đáp ứng cơ chế phân vùng multi-tenant.
  * **Tính toán chỉ số phái sinh**: CDO Platform có nhiệm vụ tiền xử lý (pre-processing) các chỉ số cộng dồn (Counter) trong dataset gốc thành dạng chỉ số tức thời (Gauge) theo đúng tần suất yêu cầu của hợp đồng.

---

## 3. Signals Specification

### Signal 1: `istio_request_error_rate` (Tính từ metrics thực tế)

Đo lường tỷ lệ các cuộc gọi dịch vụ bị lỗi trên tổng số requests. 
* **Nguồn dữ liệu gốc trong RE3**: CDO Platform đọc hai chỉ số counter là `<service>_istio-error-total` và `<service>_istio-request-total` từ file `metrics.csv`, sau đó tính toán tỷ lệ lỗi bằng công thức:
  $$\text{value} = \frac{\Delta(\text{istio-error-total})}{\Delta(\text{istio-request-total})}$$

| Attribute | Value |
|---|---|
| **Type** | Gauge |
| **Labels** | `service`, `endpoint`, `tenant_id` (Bắt buộc); `namespace`, `deployment` (Tùy chọn) |
| **Unit** | Percentage (0.0 to 1.0) |
| **Frequency** | 5 giây (Cửa sổ trượt) |
| **Emit point** | CDO Platform Preprocessor (đọc metrics.csv -> tính toán -> gửi qua SQS) |
| **Retention** | 7 ngày hot, 30 ngày cold |
| **Used for** | Anomaly detection và verify post-action |
| **Emit SLA** | p99 latency < 15 giây |

**Schema example (Hệ thống Online Boutique - OB)**:
```json
{
  "ts": "2026-06-25T10:30:00.123Z",
  "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
  "service": "adservice",
  "endpoint": "hipstershop.AdService/GetAds",
  "value": 0.45,
  "labels": {
    "system": "OB",
    "deployment_version": "v2.3.1",
    "namespace": "onlineboutique",
    "deployment": "adservice"
  }
}
```

---

### Signal 2: `istio_request_latency_p95` (Khớp metrics thực tế)

Đo lường độ trễ ở phân vị thứ 95 của các cuộc gọi gRPC/HTTP qua Istio.
* **Nguồn dữ liệu gốc trong RE3**: CDO Platform lấy trực tiếp từ chỉ số `<service>_istio-latency-95` trong file `metrics.csv`.

| Attribute | Value |
|---|---|
| **Type** | Gauge |
| **Labels** | `service`, `endpoint`, `tenant_id` (Bắt buộc); `namespace`, `deployment` (Tùy chọn) |
| **Unit** | Milliseconds |
| **Frequency** | 5 giây |
| **Emit point** | CDO Platform (Trích xuất từ metrics.csv -> gửi qua SQS) |
| **Retention** | 7 ngày hot, 30 ngày cold |
| **Used for** | Phát hiện treo dịch vụ (Service stuck / Latency spike) |
| **Emit SLA** | p99 latency < 15 giây |

**Schema example (Hệ thống Online Boutique - OB)**:
```json
{
  "ts": "2026-06-25T10:30:00.123Z",
  "tenant_id": "6c8b4b2b-4d45-4209-a1b4-4b532d56a31c",
  "service": "checkoutservice",
  "endpoint": "hipstershop.CheckoutService/PlaceOrder",
  "value": 245.0,
  "labels": {
    "system": "OB",
    "deployment_version": "v1.0.4",
    "namespace": "onlineboutique",
    "deployment": "checkoutservice"
  }
}
```

---

### Signal 3: `container_memory_working_set_bytes` (Khớp metrics thực tế)

Đo lường lượng bộ nhớ thực tế container đang sử dụng.
* **Nguồn dữ liệu gốc trong RE3**: CDO Platform lấy trực tiếp từ chỉ số `<service>_container-memory-working-set-bytes` trong file `metrics.csv`.

| Attribute | Value |
|---|---|
| **Type** | Gauge |
| **Labels** | `service`, `pod_name`, `container`, `tenant_id` (Bắt buộc); `namespace`, `deployment` (Tùy chọn) |
| **Unit** | Bytes |
| **Frequency** | 10 giây |
| **Emit point** | CDO Platform (Trích xuất từ metrics.csv -> gửi qua SQS) |
| **Used for** | Phát hiện rò rỉ bộ nhớ (Memory leak / OOM prevention) |
| **Emit SLA** | p99 latency < 20 giây |

**Schema example (Hệ thống Online Boutique - OB)**:
```json
{
  "ts": "2026-06-25T10:30:00.000Z",
  "tenant_id": "6c8b4b2b-4d45-4209-a1b4-4b532d56a31c",
  "service": "emailservice",
  "pod_name": "emailservice-68d7f5c9b-abcde",
  "container": "main",
  "value": 419430400,
  "labels": {
    "system": "OB",
    "namespace": "onlineboutique",
    "deployment": "emailservice"
  }
}
```

---

### Signal 4: `app_log_error_event` (Khớp logs thực tế)

Sự kiện log lỗi của ứng dụng khi phát hiện log có mức độ `ERROR` hoặc chứa nội dung stack trace trong file `logs.csv`.

| Attribute | Value |
|---|---|
| **Type** | Event |
| **Labels** | `service`, `pod_name`, `level`, `tenant_id` (Bắt buộc); `namespace`, `deployment` (Tùy chọn) |
| **Frequency** | Real-time (On-event) |
| **Emit point** | CDO Platform Log Parser (đọc logs.csv -> lọc log ERROR -> gửi qua SQS) |
| **Retention** | 30 ngày hot |
| **Used for** | AI Engine phân tích ngữ cảnh stack trace để chẩn đoán chính xác dòng code lỗi (f1-f5) |
| **Emit SLA** | p99 latency < 5 giây |

**Schema example (Hệ thống Online Boutique - OB)**:
```json
{
  "ts": "2026-06-25T10:30:05.456Z",
  "tenant_id": "d3b07384-d113-495f-9f58-20d18d357d75",
  "service": "adservice",
  "pod_name": "adservice-5f8d9b7c-xyz12",
  "level": "ERROR",
  "message": "java.lang.NullPointerException: Cannot invoke 'String.length()' because 'param' is null\n\tat com.hipstershop.adservice.AdService.getAds(AdService.java:45)",
  "labels": {
    "system": "OB",
    "namespace": "onlineboutique",
    "deployment": "adservice"
  }
}
```

---

### Signal 5: `trace_span_error_event` (Khớp traces thực tế)

Sự kiện được phát sinh khi có cuộc gọi giao dịch (trace span) kết thúc với lỗi (`statusCode != 0.0`) trong file `traces.csv`.

| Attribute | Value |
|---|---|
| **Type** | Event |
| **Labels** | `service`, `operation`, `trace_id`, `span_id`, `status_code`, `tenant_id` (Bắt buộc); `namespace`, `deployment` (Tùy chọn) |
| **Frequency** | Real-time (On-event) |
| **Emit point** | CDO Platform Trace Parser (đọc traces.csv -> lọc span statusCode != 0.0 -> gửi qua SQS) |
| **Retention** | 7 ngày hot |
| **Used for** | Chẩn đoán lỗi liên dịch vụ và xác định điểm đầu tiên phát sinh lỗi |
| **Emit SLA** | p99 latency < 10 giây |

**Schema example (Hệ thống Online Boutique - OB)**:
```json
{
  "ts": "2026-06-25T10:30:04.999Z",
  "tenant_id": "6c8b4b2b-4d45-4209-a1b4-4b532d56a31c",
  "service": "frontend",
  "operation": "grpc.hipstershop.ProductCatalogService/GetProduct",
  "trace_id": "d472bd0a6bda79d8d0b2852d8165cb97",
  "span_id": "cc3118e92762c87f",
  "status_code": 2.0,
  "duration_ms": 150.5,
  "labels": {
    "system": "OB",
    "namespace": "onlineboutique",
    "deployment": "frontend"
  }
}
```

---

## 4. Các yêu cầu chung (Cross-cutting Requirements)

- **Tenant Scoping (Bắt buộc)**: Mọi signal payload **phải** chứa trường `tenant_id` (với dữ liệu mô phỏng sẽ sử dụng giá trị chèn tương ứng là `"d3b07384-d113-495f-9f58-20d18d357d75"` hoặc `"6c8b4b2b-4d45-4209-a1b4-4b532d56a31c"`).
- **Time Precision**: Tất cả timestamps phải tuân thủ chuẩn RFC3339 UTC, độ chính xác ở mức millisecond.
- **Anonymization**: Nhóm CDO có trách nhiệm lọc bỏ hoặc mã hóa toàn bộ dữ liệu nhạy cảm (PII) xuất hiện trong log trước khi đẩy qua AI Engine.
