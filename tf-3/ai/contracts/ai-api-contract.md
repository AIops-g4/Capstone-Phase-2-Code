# AI API Contract - Task Force 3 (Self-Heal Engine)

## 1. Mục đích

Tài liệu này định nghĩa **giao diện lập trình ứng dụng (API Endpoints)** do nhóm AI cung cấp (expose) và nhóm CDO tích hợp tiêu thụ (consume). Đây là cam kết kỹ thuật cho việc vận hành luồng xử lý tự động khắc phục lỗi của **Self-Heal Engine**:

```text
Detect Anomaly (v1/detect) ──> Match Runbook & Decide Action (v1/decide) ──> CDO Execute ──> Post-Verify State (v1/verify)
```

## 2. Versioning & Offline Simulation Rules

- **API Path**: `/v1/`
- **Authentication**: Sử dụng **IAM SigV4** cho inter-service calls.
- **Idempotency**: Các yêu cầu thay đổi trạng thái (như `/v1/decide` và `/v1/verify`) bắt buộc gửi kèm header `Idempotency-Key` (UUID v4).
- **Quy tắc chạy mô phỏng (Offline Simulation Mode)**:
  * Vì RE2 và RE3 dataset là dữ liệu offline tĩnh, luồng hành động chữa lành của CDO và kiểm chứng của AI sẽ chạy ở dạng **giả lập**.
  * CDO Platform sẽ gửi thông tin hành động giả định đã thực hiện sang `/v1/verify`, đồng thời trích xuất dữ liệu telemetry ở các mốc thời gian kế tiếp (sau thời điểm tiêm lỗi) trong file CSV của ca lỗi đó để gửi làm `post_telemetry_window`. AI Engine sẽ phân tích cửa sổ này để xác định xem lỗi đã tự hết (hoặc mô hình giả lập hết lỗi) chưa.
  * Các tham chiếu `tenant_id` sẽ sử dụng giá trị chèn `tnt-re2-simulation` hoặc `tnt-re3-simulation` (tương ứng với dataset nguồn) để đồng bộ.

---

## 3. Endpoints Specification

### Endpoint 1: `POST /v1/detect`

**Mục đích**: Nhận dữ liệu telemetry thời gian thực từ CDO (hoặc từ preprocessor đọc dataset), chạy mô hình phát hiện bất thường và phân loại mức độ nghiêm trọng.

#### Request Headers
| Header | Type | Required | Description |
|---|---|---|---|
| `X-Tenant-Id` | UUID v4 | ✓ | Định danh khách hàng (Dùng `tnt-re3-simulation` trong offline test) |
| `Authorization` | IAM SigV4 | ✓ | Xác thực liên dịch vụ |
| `X-Correlation-Id` | UUID | optional | Trace correlation ID |

#### Request Body
| Field | Type | Required | Description |
|---|---|---|---|
| `telemetry_window` | array | ✓ | Danh sách các datapoints telemetry trong cửa sổ phân tích |
| `telemetry_window[].ts` | RFC3339 | ✓ | Timestamp của sự kiện (UTC) |
| `telemetry_window[].signal_name` | string | ✓ | Tên tín hiệu (Khớp với Telemetry Contract) |
| `telemetry_window[].value` | float/string | ✓ | Đo lường hoặc nội dung log |
| `telemetry_window[].labels` | object | optional | Metadata nhãn bổ sung (Ví dụ: `service`, `pod_name`, `endpoint`, `container`, `namespace`, `deployment` theo Telemetry Contract) |

**Request Example (Online Boutique System)**:
```json
{
  "telemetry_window": [
    {
      "ts": "2026-06-25T10:00:00.123Z",
      "signal_name": "istio_request_error_rate",
      "value": 0.45,
      "labels": { 
        "service": "adservice",
        "namespace": "onlineboutique",
        "deployment": "adservice"
      }
    },
    {
      "ts": "2026-06-25T10:00:01.456Z",
      "signal_name": "app_log_error_event",
      "value": "java.lang.NullPointerException: Cannot invoke 'String.length()' because 'param' is null\n\tat com.hipstershop.adservice.AdService.getAds(AdService.java:45)",
      "labels": { 
        "service": "adservice", 
        "pod_name": "adservice-5f8d9b7c-xyz12",
        "namespace": "onlineboutique",
        "deployment": "adservice"
      }
    }
  ]
}
```

#### Response Body
| Field | Type | Description |
|---|---|---|
| `anomaly_detected` | bool | `true` nếu phát hiện bất thường |
| `severity` | float | Điểm số độ nghiêm trọng (0.0 đến 1.0) |
| `anomaly_context` | object | Chi tiết lỗi: dịch vụ bị ảnh hưởng, loại lỗi nghi ngờ |
| `anomaly_context.target_service` | string | Tên service bị lỗi (Phải thuộc 10 services của Online Boutique) |
| `anomaly_context.suspected_fault_type` | string | Loại lỗi nghi ngờ. Đối với lỗi code RE3: `f1` đến `f5`. Đối với lỗi tài nguyên/mạng RE2: `cpu`, `mem`, `disk`, `loss`, `delay`, `socket`. |
| `anomaly_context.system` | string | Luôn là `"OB"` (Online Boutique) |
| `anomaly_context.namespace` | string | Namespace xảy ra lỗi (Tùy chọn, ví dụ: `"onlineboutique"`) |
| `anomaly_context.deployment` | string | Tên Kubernetes Deployment của service bị lỗi (Tùy chọn, ví dụ: `"adservice"`) |
| `confidence` | float | Độ tin cậy của mô hình AI (0.0 đến 1.0) |
| `correlation_id` | UUID | Định danh correlation để liên kết sang decide step |

**Response Example**:
```json
{
  "anomaly_detected": true,
  "severity": 0.85,
  "anomaly_context": {
    "target_service": "adservice",
    "suspected_fault_type": "f3",
    "system": "OB",
    "namespace": "onlineboutique",
    "deployment": "adservice",
    "trigger_metric": "istio_request_error_rate",
    "trigger_value": 0.45
  },
  "confidence": 0.92,
  "correlation_id": "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o"
}
```

---

### Endpoint 2: `POST /v1/decide`

**Mục đích**: Đối chiếu bất thường với thư viện Runbook chuẩn để đưa ra kịch bản tự chữa lành (Action Plan) kèm theo cấu hình giới hạn vùng ảnh hưởng (Blast Radius).

#### Request Headers
| Header | Type | Required | Description |
|---|---|---|---|
| `X-Tenant-Id` | UUID v4 | ✓ | Định danh khách hàng (`cdo-1`, `cdo-2`, hoặc giá trị giả lập `tnt-re3-simulation`)  |
| `Idempotency-Key` | UUID v4 | ✓ | Tránh chạy lặp kịch bản quyết định |

#### Request Body
| Field | Type | Required | Description |
|---|---|---|---|
| `correlation_id` | UUID v4 | ✓ | Lấy từ kết quả `/v1/detect` |
| `anomaly_context` | object | ✓ | Thông tin ngữ cảnh lỗi (phản hồi từ detect step) |
| `dry_run_mode` | bool | ✓ | Nếu `true`, chỉ trả về plan mà không ghi nhận thực thi thật |

**Request Example**:
```json
{
  "correlation_id": "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o",
  "anomaly_context": {
    "target_service": "adservice",
    "suspected_fault_type": "f3",
    "system": "OB",
    "trigger_metric": "istio_request_error_rate",
    "trigger_value": 0.45
  },
  "dry_run_mode": false
}
```

#### Response Body
| Field | Type | Description |
|---|---|---|
| `matched_runbook` | string | Tên runbook được khớp |
| `action_plan` | array | Danh sách các bước lệnh tuần tự để CDO infra thực thi |
| `action_plan[].step` | integer | Thứ tự thực hiện lệnh |
| `action_plan[].action` | enum | Lệnh: `RESTART_DEPLOYMENT`, `SCALE_UP_PODS`, `UPDATE_ENV_SECRET`, `ADJUST_MEMORY_LIMIT` |
| `action_plan[].target` | string | Target resource (ví dụ: deployment name) |
| `action_plan[].params` | object | Tham số đi kèm lệnh |
| `blast_radius_config` | object | Giới hạn vùng ảnh hưởng cho phép CDO áp dụng |
| `blast_radius_config.max_pod_impact_pct` | integer | Tỷ lệ pod tối đa được phép khởi động lại cùng lúc |
| `blast_radius_config.circuit_breaker_error_rate` | float | Ngưỡng ngắt mạch tự động dừng rollback/action |

**Response Example**:
```json
{
  "matched_runbook": "RestartDeploymentRunbook",
  "action_plan": [
    {
      "step": 1,
      "action": "RESTART_DEPLOYMENT",
      "target": "deployment/adservice",
      "params": {
        "namespace": "onlineboutique",
        "grace_period_seconds": 30
      }
    }
  ],
  "blast_radius_config": {
    "max_pod_impact_pct": 25,
    "circuit_breaker_error_rate": 0.20,
    "allowed_namespaces": ["onlineboutique"]
  }
}
```

---

### Endpoint 3: `POST /v1/verify`

**Mục đích**: Nhận thông tin từ CDO sau khi đã thực thi xong action, tiến hành phân tích telemetry hậu sự kiện để xác định xem lỗi đã được xử lý triệt để hay chưa.

#### Request Headers
| Header | Type | Required | Description |
|---|---|---|---|
| `X-Tenant-Id` | string | ✓ | Định danh khách hàng (`cdo-1`, `cdo-2`, hoặc giá trị giả lập `tnt-re3-simulation`) |
| `Idempotency-Key` | UUID v4 | ✓ | Tránh chạy lặp kịch bản kiểm chứng (Verify) |


#### Request Body
| Field | Type | Required | Description |
|---|---|---|---|
| `correlation_id` | UUID v4 | ✓ | Khớp phiên xử lý |
| `action_executed` | object | ✓ | Chi tiết hành động CDO đã chạy |
| `post_telemetry_window` | array | ✓ | Telemetry thu được sau khi thực thi (form telemetry xem trong [telemetry-contract.md](https://github.com/AIops-g4/Capstone-Phase-2-Code/blob/main/tf-3/ai/contracts/telemetry-contract.md) |

**Request Example**:
```json
{
  "correlation_id": "c1a2b3c4-d5e6-4f7g-8h9i-0j1k2l3m4n5o",
  "action_executed": {
    "action": "RESTART_DEPLOYMENT",
    "target": "deployment/adservice",
    "status": "COMPLETED",
    "execution_time_seconds": 45
  },
  "post_telemetry_window": [
    {
      "ts": "2026-06-25T10:02:00.000Z",
      "signal_name": "istio_request_error_rate",
      "value": 0.00,
      "labels": { 
        "service": "adservice",
        "namespace": "onlineboutique",
        "deployment": "adservice"
      }
    }
  ]
}
```

#### Response Body
| Field | Type | Description |
|---|---|---|
| `success` | bool | `true` nếu hệ thống trở về trạng thái bình thường (hết lỗi) |
| `regression_detected` | bool | `true` nếu phát hiện có sự suy giảm hiệu năng khác phát sinh |
| `next_action` | enum | `DONE` (Xong), `RETRY` (Thử lại), `ROLLBACK` (Lùi phiên bản), `ESCALATE` (Gửi cảnh báo đến kỹ sư) |
| `escalation_bundle` | object | Gói context bundle AI sinh ra để gửi cho on-call engineer (khi `next_action = ESCALATE`). |

**Response Example**:
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
