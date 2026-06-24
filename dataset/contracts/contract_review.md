# Review 3 Contracts
> **Tổng quan**: 3 file contract đã được cải thiện **rất nhiều** so với bản template. Đã tích hợp tốt phản hồi từ cả CDO-1 (Context Report) và CDO-2 (Questions). Tuy nhiên vẫn còn **một số điểm cần chỉnh sửa** để khớp 100% giữa 3 file với nhau và với yêu cầu đề TF3.

---

## FILE 1: [ai-api-contract.md]

### ✅ Điểm tốt
- Viết hoàn toàn bằng tiếng Việt, dễ hiểu.
- Có JSON Schema chính thức cho cả request/response — cực kỳ chuyên nghiệp.
- Đã tích hợp `anomaly_context` thay vì `anomaly_type` enum cứng, phù hợp hơn cho mô hình AI LLM/hybrid.
- Action enum đã được cập nhật: `RESTART_DEPLOYMENT`, `PATCH_MEMORY_LIMIT`, `SCALE_REPLICAS`, `ROLLOUT_UNDO`, `ROTATE_SECRET` — khớp với CDO-1 Allowed Action Matrix.
- `cost_cap_exceeded` trong `/v1/decide` — rất thông minh, CDO biết AI đang dùng rule-based fallback.
- `escalation_bundle` trong `/v1/verify` — đúng yêu cầu đề TF3 về context bundle.
- Có phần Versioning & Change-Request (Mục 5) rất chuyên nghiệp.

### ❌ Các điểm cần chỉnh sửa

---

#### Lỗi 1: Action enum THIẾU `DELETE_POD` — mâu thuẫn với câu trả lời CDO-2

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 3.2 — `/v1/decide` Response Body, dòng ~270 và dòng ~309 (JSON Schema enum) |
| **Vấn đề** | Enum action chỉ có 5 giá trị: `RESTART_DEPLOYMENT`, `PATCH_MEMORY_LIMIT`, `SCALE_REPLICAS`, `ROLLOUT_UNDO`, `ROTATE_SECRET`. Nhưng trong file `Question` (câu 2), AI team đã trả lời CDO-2 rằng **"Có, AI có thể trả về `DELETE_POD`"** cho các lỗi CrashLoopBackOff/Evicted. |
| **Lý do sửa** | CDO-2 đã ghi nhận DELETE_POD là action hợp lệ. Nếu contract không có, CDO-2 sẽ nhận action không parse được → lỗi runtime. Đồng thời CDO-1 Allowed Action Matrix (Mục 10) cũng liệt kê `DELETE_POD` là Limited/Allowed. |
| **Outcome kỳ vọng** | Thêm `"DELETE_POD"` vào enum `action` ở dòng ~309. Thêm params `pod_name` (string, required cho `DELETE_POD`) vào bảng mô tả params ở dòng ~274. |

---

#### Lỗi 2: Action enum CÒN KHÁC TÊN so với câu hỏi CDO-2

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 3.2 — `/v1/decide` Response, dòng ~270 và ~309 |
| **Vấn đề** | CDO-2 Question câu 2 nhắc đến enum cũ có `SCALE_UP_PODS`, `UPDATE_ENV_SECRET`, `ADJUST_MEMORY_LIMIT`. Contract hiện tại đã đổi thành `SCALE_REPLICAS`, `ROTATE_SECRET`, `PATCH_MEMORY_LIMIT`. Bản thân contract đã đúng hơn, nhưng cần **xác nhận chính thức** trong file contract rằng các tên cũ đã bị deprecated để CDO-2 không nhầm. |
| **Lý do sửa** | Tránh CDO-2 implement theo tên cũ rồi không khớp với tên mới. |
| **Outcome kỳ vọng** | Thêm 1 ghi chú nhỏ ngay dưới bảng action enum (khoảng dòng ~271): _"Lưu ý: Các tên action cũ (`SCALE_UP_PODS`, `ADJUST_MEMORY_LIMIT`, `UPDATE_ENV_SECRET`) đã bị thay thế. CDO vui lòng sử dụng tên mới trong contract này."_ |

---

#### Lỗi 3: THIẾU trường `audit_id` trong Response của `/v1/detect`

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 3.1 — `/v1/detect` Response Body Schema, dòng ~118-166 |
| **Vấn đề** | Response chỉ có `anomaly_detected`, `severity`, `anomaly_context`, `confidence`, `reasoning`, `correlation_id`. Nhưng đề TF3 yêu cầu **audit trail cho mọi quyết định**. Không có `audit_id` ở bước detect thì CDO không có gì để link chuỗi detect→decide→verify trong audit log. CDO-1 (Mục 9) cũng nêu rõ cần `audit_id` ở mọi endpoint response. |
| **Lý do sửa** | Mất khả năng truy vết (traceability) giữa các bước trong pipeline. |
| **Outcome kỳ vọng** | Thêm trường `"audit_id"` (string, UUID, required) vào bảng Fields Description ở dòng ~132 và JSON Schema ở dòng ~164. |

---

#### Lỗi 4: THIẾU `audit_id` và `correlation_id` trong Response `/v1/decide`

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 3.2 — `/v1/decide` Response Body Schema, dòng ~261-360 |
| **Vấn đề** | Response schema có `correlation_id`, `idempotency_key`, `dry_run_mode` nhưng **THIẾU** `audit_id`. CDO-1 (Mục 12, 20.5) đặc biệt nhấn mạnh mọi decision event phải có `audit_id` để link với pre-state snapshot. |
| **Lý do sửa** | CDO không thể link audit decision event với snapshot mà không có audit_id. |
| **Outcome kỳ vọng** | Thêm `"audit_id"` vào Response Body Description ở dòng ~266 và JSON Schema ở dòng ~352. |

---

#### Lỗi 5: `/v1/verify` Response THIẾU `confidence` và `correlation_id`

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 3.3 — `/v1/verify` Response Body Schema, dòng ~502-549 |
| **Vấn đề** | Response chỉ có `success`, `regression_detected`, `next_action`, `escalation_bundle`. Không có `confidence`, `correlation_id`, `audit_id`. CDO-1 (Mục 13) nêu rõ verify response cần `confidence` để CDO log vào audit, và `correlation_id` để link toàn bộ chuỗi. |
| **Lý do sửa** | Mất tính nhất quán (consistency) giữa 3 endpoints. Detect có confidence, Decide có confidence, nhưng Verify lại không. CDO không biết AI tự tin bao nhiêu vào kết quả verify. |
| **Outcome kỳ vọng** | Thêm `confidence` (number 0.0-1.0), `correlation_id` (UUID), `audit_id` (UUID) vào bảng Fields và Schema của `/v1/verify` response. |

---

#### Lỗi 6: THIẾU hoàn toàn mục Safety Gates

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Thiếu hoàn toàn — nên thêm giữa Mục 4 (SLA) và Mục 5 (Versioning), khoảng dòng ~574 |
| **Vấn đề** | Đề TF3 (dòng 42) yêu cầu **bắt buộc** 5 safety sub-checkpoints: `dry-run`, `blast-radius`, `verify post-act`, `auto rollback`, `circuit breaker`. Contract cũ có phần CDO Execution Rules rất rõ ràng. Bản mới của Huy đã gộp `blast_radius_config` vào response `/v1/decide` nhưng **KHÔNG có mục riêng** tổng hợp 5 safety gates thành checklist cho CDO. |
| **Lý do sửa** | Mentor chấm theo barem TF3 sẽ tìm mục Safety Gates. Không có = mất điểm phần Safety checkpoints. CDO-1 (Mục 10: Hard guardrails) cũng liệt kê rõ namespace lock, concurrent lock, circuit breaker, dry-run bắt buộc. Cần phần tương ứng phía AI contract. |
| **Outcome kỳ vọng** | Thêm mục mới `"4.5. Chốt chặn An toàn (Safety Gates)"` liệt kê rõ 5 điều kiện CDO phải check trước khi execute, và các điều kiện AI tự động trả `ESCALATE`. |

---

#### Lỗi 7: THIẾU mục Known Pattern Mapping

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Thiếu hoàn toàn — nên thêm sau Mục 4 (SLA), khoảng dòng ~574 |
| **Vấn đề** | Đề TF3 yêu cầu **≥3 known patterns implemented + ≥2 designed**. CDO-1 (Mục 15: Incident Injection Plan) đã liệt kê 12 scenarios cụ thể với expected AI action. Contract AI cần có bảng mapping tương ứng để CDO biết chính xác khi inject OOMKilled thì AI sẽ trả action gì, verify bằng metric nào. |
| **Lý do sửa** | CDO-1 đã hỏi rất rõ (Mục 17): _"AIOps cần cung cấp Runbook IDs, Action enum + params schema, Verify policy per runbook"_. Không có bảng mapping = CDO không biết code test case. |
| **Outcome kỳ vọng** | Thêm bảng mapping ít nhất 3 patterns thực hành (OOMKilled→PATCH_MEMORY_LIMIT, Service Stuck→RESTART_DEPLOYMENT, Queue Backlog→SCALE_REPLICAS) và 2 patterns thiết kế (Cert Expiring→ROTATE_SECRET, Crash Loop→RESTART_DEPLOYMENT + ESCALATE). |

---

#### Lỗi 8: SLA latency cho `/v1/decide` = 3000ms — quá chậm so với CDO-1 yêu cầu

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 4 — SLA, dòng ~558 |
| **Vấn đề** | `/v1/decide` p99 < 3000ms (3 giây). CDO-1 (Mục 6, Path B) yêu cầu toàn bộ pipeline urgent phải < 15 giây (từ alert đến patch xong). Nếu riêng bước decide đã mất 3s, cộng detect + safety check + execute + verify thì chắc chắn vượt 15s. CDO-1 đề xuất AI p99 < 1000ms. |
| **Lý do sửa** | Ghi chú đã có: _"fallback rule-based bắt buộc < 500 ms"_ nhưng CDO cần biết rõ khi nào LLM khi nào rule-based. |
| **Outcome kỳ vọng** | Thêm ghi chú rõ hơn: _"Khi `pattern_type = urgent`, AI ưu tiên rule-based engine với p99 < 500ms. LLM Bedrock chỉ dùng cho `pattern_type = deferred` hoặc khi rule-based không match được."_ |

---

## FILE 2: [deployment-contract.md]

### ✅ Điểm tốt
- Phần IAM Role tách biệt (Execution vs Task Role) cực kỳ rõ ràng.
- Phần cấm `eks:*` trong Task Role — trả lời trực tiếp câu hỏi 3 của CDO-2.
- STS AssumeRole + Session Tags cho multi-tenant — đúng chuẩn ABAC.
- Mermaid diagram topology rất đẹp và dễ hiểu.
- Bedrock Cost Cap $50/ngày + fallback rule-based — rất thực tế.
- Canary rollout + abort criteria — chuyên nghiệp.
- Health/Ready/Metrics endpoints có JSON Schema đầy đủ.

### ❌ Các điểm cần chỉnh sửa

---

#### Lỗi 9: Tenant ID trong bảng routing KHÔNG KHỚP với CDO-1

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 2.C — CDO Platform Integration & Routing, dòng ~76-81 |
| **Vấn đề** | Bảng ghi: `cdo-1` = `d3b07384-...`, `cdo-2` = `6c8b4b2b-...`. Nhưng CDO-1 Context Report (Mục 4) ghi rõ tenant UUID của CDO-1 là: `tenant-a` = `11111111-1111-4111-8111-111111111111`, `tenant-b` = `22222222-2222-4222-8222-222222222222`. Hai bộ UUID hoàn toàn khác nhau. |
| **Lý do sửa** | CDO-1 sẽ gửi `X-Tenant-Id: 11111111-...` nhưng AI Engine routing table lại chỉ nhận `d3b07384-...`. Request sẽ bị reject hoặc map sai tenant → rò rỉ dữ liệu cross-tenant. |
| **Outcome kỳ vọng** | Cần họp chốt lại UUID chính thức. Nếu mỗi CDO platform quản lý nhiều tenant thì bảng phải liệt kê đầy đủ: `cdo-1 / tenant-a` → UUID `11111111-...`, `cdo-1 / tenant-b` → UUID `22222222-...`, `cdo-2` → UUID `6c8b4b2b-...`. Hoặc ghi rõ: _"UUID trong bảng là Platform ID, không phải Tenant ID. Mỗi platform tự quản lý nhiều tenant-id bên trong."_ |

---

#### Lỗi 10: THIẾU phần Simulation chỉ dẫn cụ thể cho W12 demo

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 2.D — Offline Simulation Mode, dòng ~83-85 |
| **Vấn đề** | Chỉ nói _"chạy ở chế độ Mock Mode trong sandbox"_. Nhưng trong file Question câu 6, AI team đã trả lời CDO-2: **"Mock Mode không được tính là đủ evidence cho W12 demo. Bắt buộc phải chạy action thật trên K8s sandbox ít nhất 3 known patterns."** Contract hiện tại chưa phản ánh câu trả lời này. |
| **Lý do sửa** | CDO đọc contract sẽ hiểu sai là Mock Mode vẫn đủ cho demo. |
| **Outcome kỳ vọng** | Bổ sung dòng ~85: _"Lưu ý: Chế độ Mock Mode chỉ dùng cho giai đoạn integration test ban đầu. Cho buổi demo W12, Client yêu cầu bắt buộc phải chạy ít nhất 3 scenarios thật trên K8s sandbox cluster (action thật: restart, scale, patch memory). Mock Mode KHÔNG được tính là evidence đủ cho buổi chấm cuối cùng."_ |

---

#### Lỗi 11: Health endpoint path — `/health` vs `/healthz`

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 7.A — Health Check Endpoint, dòng ~330 |
| **Vấn đề** | Contract ghi `GET /health`. Nhưng CDO-1 (Mục 16) yêu cầu `/healthz` và `/readyz`. CDO-2 Question câu 5 cũng ghi _"GET /healthz /readyz /metrics"_. Hai tên path khác nhau. |
| **Lý do sửa** | CDO sẽ config ALB/Prometheus probe nhắm vào `/healthz` nhưng AI Engine chỉ có `/health` → probe fail → traffic bị ngắt. |
| **Outcome kỳ vọng** | Thống nhất 1 trong 2 cách: (a) Đổi `/health` thành `/healthz` và `/ready` thành `/readyz` cho chuẩn Kubernetes convention. Hoặc (b) Giữ `/health` + `/ready` nhưng ghi rõ CDO phải config probe đúng path này, không dùng `/healthz`. |

---

#### Lỗi 12: THIẾU phần Network chi tiết cho VPC Peering/Transit Gateway

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 5 — Networking, dòng ~232-261 |
| **Vấn đề** | CDO-1 (Mục 16) nêu rõ: _"Shared VPC ↔ CDO VPC via VPC Peering or Transit Gateway; Route 53 Resolver Rules for private DNS"_. Contract hiện tại chỉ nói Private Subnet + Internal ALB + Route 53, nhưng không ghi rõ cách CDO từ VPC của mình kết nối vào VPC của AI. |
| **Lý do sửa** | CDO cần biết cụ thể để cấu hình VPC Peering rules, Security Group references, DNS resolver. |
| **Outcome kỳ vọng** | Thêm 1 mục con ~dòng 261: _"Cross-VPC Connectivity: CDO platforms kết nối vào AI Engine thông qua VPC Peering (ưu tiên) hoặc Transit Gateway. CDO cần cấu hình Route 53 Resolver Rules trỏ về Private Hosted Zone `tf-3.internal` của AI VPC."_ |

---

## FILE 3: [telemetry-contract.md]

### ✅ Điểm tốt
- 5 canonical signals rất rõ ràng, có payload mẫu cho từng signal.
- JSON Schema chính thức có `additionalProperties: true` cho labels — linh hoạt cho CDO thêm field mà không break schema.
- Mục PII/Scrubbing + Dead-Letter Queue — rất chuyên nghiệp, đúng SOC2.
- Bảng SLA telemetry (Mục 5) cực kỳ chi tiết: frequency, emit SLA, volume SLA, retention.
- Versioning & Change-Request process giống API contract → nhất quán.

### ❌ Các điểm cần chỉnh sửa

---

#### Lỗi 13: THIẾU signal cho OOMKilled event — CDO-1 đã đề xuất rõ

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 3 — JSON Schema, dòng ~88 (enum `signal_name`) và Mục 4 — Signals Specification |
| **Vấn đề** | Enum signal_name chỉ có 5 giá trị canonical: `service_error_rate`, `service_latency_p95`, `container_resource_usage`, `application_log_event`, `distributed_trace_error_event`. CDO-1 (Mục 8) đã đề xuất thêm derived signals: `pod_oom_event`, `service_unhealthy`, `queue_backlog`. CDO-1 nêu rõ: _"event-specific alerts được xem là derived/enriched alert payload"_. |
| **Lý do sửa** | 5 canonical signals là đủ cho detect input. Nhưng contract cần ghi rõ derived signals có được chấp nhận không, hay CDO phải transform hết về canonical. |
| **Outcome kỳ vọng** | Thêm 1 ghi chú ở Mục 4 (khoảng dòng ~167): _"Ngoài 5 tín hiệu canonical, CDO có thể gửi thêm các tín hiệu derived (ví dụ: `pod_oom_event`, `service_unhealthy`, `queue_backlog`) nhưng AI Engine sẽ chỉ xử lý nếu `signal_name` khớp với enum đã đăng ký. Các tín hiệu ngoài enum sẽ bị bỏ qua (ignored) chứ không reject `400`. CDO nên ưu tiên transform derived alerts về canonical signals trước khi gửi."_ Hoặc mở rộng enum thêm derived signals nếu AI team có thể xử lý. |

---

#### Lỗi 14: `labels.namespace` và `labels.deployment` là optional — CDO-1 yêu cầu required

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 3 — Bảng Fields Description, dòng ~53-54 và JSON Schema, dòng ~148 (`required: ["system"]`) |
| **Vấn đề** | `labels.namespace` và `labels.deployment` đều ghi "optional". Nhưng CDO-1 (Mục 8) nêu rõ: _"`labels.namespace` = Required for action-able signals"_ và _"`labels.deployment` = Required for action-able signals"_. Nếu thiếu 2 trường này, AI không thể trả action plan có `params.namespace` và `target: deployment/<name>`. |
| **Lý do sửa** | AI trả action plan nhắm vào `deployment/order-service` nhưng không biết deployment đó ở namespace nào → CDO execute sai chỗ hoặc không execute được. |
| **Outcome kỳ vọng** | Có 2 lựa chọn: (a) Đổi `labels.namespace` và `labels.deployment` thành **"Required cho signals dùng trong action (container_resource_usage, service_error_rate)"**, giữ optional cho `distributed_trace_error_event`. Hoặc (b) Giữ optional nhưng thêm ghi chú: _"AI Engine sẽ trả `ESCALATE` thay vì action plan nếu thiếu `labels.namespace` hoặc `labels.deployment`."_ |

---

#### Lỗi 15: THIẾU trường `correlation_id` trong telemetry schema

| Hạng mục | Chi tiết |
|---|---|
| **Vị trí** | Mục 3 — JSON Schema, dòng ~65-162 |
| **Vấn đề** | Telemetry schema hiện tại **không có** trường `correlation_id`. Nhưng CDO-1 (Mục 8) ghi: _"`correlation_id` = Required for incident flow"_. CDO-1 sample payload (Appendix 20.1) cũng có `"correlation_id"`. AI api-contract payload mẫu `/v1/detect` request (dòng ~82) cũng có `correlation_id` trong body nhưng nó nằm ở body level chứ không nằm trong telemetry datapoint. |
| **Lý do sửa** | Nếu CDO muốn gửi correlation_id bên trong mỗi telemetry datapoint (như sample 20.1 của CDO-1), schema sẽ reject với `additionalProperties: false` trên root level (dòng ~161). Tuy nhiên, `additionalProperties: true` trên labels (dòng ~151) cho phép CDO đặt `correlation_id` vào labels. |
| **Outcome kỳ vọng** | Ghi rõ: _"Trường `correlation_id` không nằm trong telemetry datapoint mà được truyền qua request body level của API `/v1/detect` hoặc qua header `X-Correlation-Id`. CDO KHÔNG cần thêm correlation_id vào mỗi telemetry datapoint."_ Hoặc thêm `correlation_id` là optional field vào root schema. |

---

## TỔNG KẾT NHANH

| File | Số lỗi | Mức độ nghiêm trọng |
|---|---|---|
| `ai-api-contract.md` | 8 lỗi | 🔴 **Cao** — thiếu audit_id, safety gates, pattern mapping |
| `deployment-contract.md` | 4 lỗi | 🟡 **Trung bình** — tenant ID mismatch, mock mode clarification |
| `telemetry-contract.md` | 3 lỗi | 🟡 **Trung bình** — namespace required, derived signals, correlation_id |

### TOP 5 ƯU TIÊN SỬA TRƯỚC (Ảnh hưởng lớn nhất đến CDO integration):

1. **Thêm `audit_id`** vào response của cả 3 endpoints (Lỗi 3, 4, 5) — CDO không thể làm audit pipeline nếu thiếu.
2. **Thêm mục Safety Gates** vào AI API contract (Lỗi 6) — Mentor chấm theo barem TF3, mất mục này = mất điểm.
3. **Thêm `DELETE_POD`** vào action enum (Lỗi 1) — Đã trả lời CDO-2 là có, contract phải match.
4. **Chốt lại Tenant UUID** giữa AI contract và CDO-1 (Lỗi 9) — Cross-tenant data leak nếu sai.
5. **Ghi rõ Mock Mode không đủ cho W12** trong deployment contract (Lỗi 10) — Tránh CDO hiểu sai.
