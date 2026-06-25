# Phân tích Yêu cầu Thay đổi Hợp đồng — TF3 Self-Heal Engine

> **Tài liệu:** Contract Change Request Analysis Report
> **Mã tài liệu:** TF3-CRR-001
> **Phiên bản:** 1.1 — DRAFT FOR REVIEW
> **Ngày phát hành:** 2026-06-25 | **Cập nhật lần cuối:** 2026-06-25 (v1.1 — phản ánh thay đổi mô hình deployment từ W11_W12_capstone_announcement.md bản mới nhất)
> **Soạn bởi:** AIO Team — Task Force 3
> **Gửi tới:** AIO Lead · CDO-1 Lead · CDO-2 Lead · Principal AI Architect
> **Trạng thái:** Chờ phê duyệt trước khi cập nhật hợp đồng chính thức

---

## Executive Summary

Sau khi ba hợp đồng kỹ thuật giữa nhóm AI (AIO) và hai nhóm hạ tầng (CDO-1, CDO-2) được ký kết và đóng băng vào **T5 chiều W11 (25/06/2026)**, hai nhóm CDO đã tiến hành rà soát kỹ thuật độc lập và ghi nhận tổng cộng **11 điểm không tương thích hoặc thiếu sót** trải rộng trên cả ba hợp đồng:

- **AI API Contract** (`ai-api-contract.md`): 5 điểm — liên quan đến định nghĩa `pattern_type`, bộ tên action enum, giới hạn SLA, DSL `success_conditions`, và schema thiếu `cost_cap_exceeded`.
- **Telemetry Contract** (`telemetry-contract.md`): 2 điểm — đổi tên toàn bộ 5 tín hiệu và loại bỏ kênh SQS không được thông báo.
- **Deployment Contract** (`deployment-contract.md`): 4 điểm — thiếu spec IAM Role tenant, tham chiếu ArgoCD sai ngữ cảnh, phạm vi IAM ABAC chưa rõ, và **mô hình hosting AI Engine đã thay đổi căn bản** theo capstone announcement phiên bản mới nhất (v1.1).

> **⚠️ Thay đổi quan trọng từ Overview mới nhất (v1.1):** Capstone announcement đã được cập nhật — mô hình deployment AI Engine **không còn là shared backend 1 endpoint dùng chung**. Thay vào đó: AIO deploy **1 skeleton chung T5 W11 để bootstrap tạm**, sau đó **W12 mỗi CDO tự deploy engine thật lên platform riêng của mình** theo Deployment Contract (artifact bàn giao). Đây là thay đổi cốt lõi ảnh hưởng trực tiếp đến cách Deployment Contract phải được viết và các điểm [DC-01], [DC-03], [DC-04] cần được xử lý.

Tất cả những thay đổi này nảy sinh từ ba nguồn: **(1) CDO-2 ghi nhận breaking changes so với phiên bản hợp đồng trước khi ký**, **(2) CDO-1 phát hiện các thiếu sót kỹ thuật qua quá trình xây dựng thiết kế hạ tầng chi tiết**, và **(3) capstone announcement được cập nhật thay đổi mô hình deployment**. Không có yêu cầu nào vượt ra ngoài phạm vi đã thỏa thuận trong capstone; tất cả đều là làm rõ hoặc sửa lỗi kỹ thuật cần giải quyết trước khi hai nhóm CDO có thể tiếp tục build.

> **Khuyến nghị:** AIO Team cần xử lý và phản hồi toàn bộ 11 điểm trên **trước 09:00 T2 W12 (29/06/2026)** để không ảnh hưởng đến Integration Session T3 W12 (30/06/2026) — thời điểm CDO bắt buộc gọi AI engine thật **đã deploy trên platform của mình**.

---

## Contract Impact Table — Bảng Tổng hợp Tác động

| # | Hợp đồng | Mục / Phần bị ảnh hưởng | Yêu cầu bởi | Mức độ tác động | Thay đổi cần thực hiện | Lý do kỹ thuật / Nghiệp vụ |
|---|---|---|---|---|---|---|
| 1 | Telemetry Contract | Mục 4 — Tên 5 tín hiệu (`signal_name`) | CDO-2 | 🔴 **Breaking** | Xác nhận đây là tên chính thức; phát hành migration notice và thời hạn cho CDO-2 cập nhật pipeline | CDO-2 đã build docs, test cases và telemetry pipeline theo tên cũ (`istio_request_error_rate`, v.v.); đổi tên không có thông báo phá vỡ toàn bộ mapping |
| 2 | Telemetry Contract | Mục 5 — Kênh truyền dữ liệu (SQS) | CDO-2 | 🟠 **Major** | Xác nhận channel chính thức: HTTP push (OTel/Prometheus) hay SQS; cập nhật vào mục Telemetry SLA | CDO-2 đã thiết kế SQS làm telemetry buffer; contract mới không nhắc SQS nhưng cũng không công bố thay thế rõ ràng |
| 3 | AI API Contract | Mục 3.2 — Trường `pattern_type` và nghĩa vụ CDO | CDO-2 | 🟠 **Major** | Bổ sung bảng định nghĩa hành vi CDO cho từng giá trị (`urgent` → Path B Direct Patch; `deferred` → Path A GitOps) | CDO không biết sự khác nhau về luồng thực thi, dẫn đến không thể code executor logic |
| 4 | AI API Contract | Mục 3.2 — Action enum thay đổi tên | CDO-2 | 🔴 **Breaking** | Phát hành changelog chính thức; định nghĩa rõ 2 action mới `ROLLOUT_UNDO` và `DELETE_POD` kèm điều kiện kích hoạt và hướng dẫn xử lý | CDO-2 đã code allow-list và executor theo tên cũ; `DELETE_POD` đặc biệt nhạy cảm cần spec an toàn rõ ràng |
| 5 | AI API Contract | Mục 4 — SLA `/v1/decide` tăng từ 500ms → 3000ms | CDO-2 | 🟡 **Medium** | Xác nhận điều kiện kích hoạt fallback rule-based (< 500ms) để CDO estimate worst-case end-to-end latency | CDO thiết kế timeout và SLO tổng thể "< 5 phút" dựa trên 500ms; margin hiện rất mỏng khi tăng 6 lần |
| 6 | AI API Contract | Mục 3.2 — `verify_policy.success_conditions` DSL | CDO-2 & CDO-1 | 🟠 **Major** | Publish full grammar/syntax spec của DSL; ánh xạ metric name trong condition sang `signal_name` trong telemetry contract; định nghĩa fallback khi parse lỗi | Không có spec DSL → CDO không thể implement evaluator; metric name trong condition không khớp telemetry contract |
| 7 | AI API Contract | Mục 3.2 — `cost_cap_exceeded` thiếu trong JSON Schema | CDO-2 | 🟠 **Major** | Thêm `cost_cap_exceeded` vào schema chính thức của `/v1/decide` response; mô tả CDO behavior khi nhận flag này | Schema có `additionalProperties: false`; field chưa khai báo sẽ bị CDO validator từ chối; CDO cần biết action plan có còn execute được không |
| 8 | Deployment Contract | Mục 3.E — IAM Role Tenant thiếu policy template | CDO-1 | 🟠 **Major** | Cung cấp template IAM policy (permissions) và trust policy; **xác nhận scope: cross-account** vì mỗi CDO host engine trên AWS account riêng | Với mô hình mỗi CDO tự host engine, trust policy phải trust CDO ECS task role từ account khác — không thể dùng single-account ARN như trước |
| 9 | Deployment Contract | Mục 6.C — Tham chiếu ArgoCD cho rollback AI Engine | CDO-2 | 🟡 **Medium** | Thay thế bằng cơ chế rollback ECS chuẩn (AWS CodeDeploy Blue/Green + ECS Deployment Circuit Breaker); ghi rõ ArgoCD chỉ quản lý workload K8s của CDO, không quản lý AI Engine trên ECS | AI Engine chạy ECS Fargate — ArgoCD không thể quản lý ECS task definition; mâu thuẫn kỹ thuật gây nhầm lẫn trong triển khai |
| 10 | Deployment Contract | Mục 3 — Phạm vi và cấu hình IAM ABAC chưa đủ | CDO-1 | 🟠 **Major** *(tăng từ Medium)* | Xác nhận scope là **cross-account** do mô hình deployment mới; cung cấp ABAC tag conditions mẫu với cross-account ARN format | Mô hình mới (mỗi CDO tự host engine) làm cho IAM AssumeRole chắc chắn là cross-account → không thể để ngỏ scope như phiên bản cũ |
| 11 | Deployment Contract | Mục 2 — Mô hình hosting AI Engine | Overview mới (v1.1) | 🔴 **Breaking** | Cập nhật toàn bộ Mục 2 để phản ánh mô hình mới: AIO đóng gói engine thành **artifact bàn giao** (image + Deployment Contract); mỗi CDO tự deploy engine thật trên platform riêng trong W12 | Capstone announcement đã thay đổi từ "1 shared endpoint" sang "mỗi CDO host engine riêng" — đây là thay đổi kiến trúc cốt lõi ảnh hưởng đến toàn bộ integration flow và là cơ sở chấm điểm "deploy được trên 2-3 CDO platform" |

---

## Deep-Dive Breakdown

### Hợp đồng 1: Telemetry Contract (`telemetry-contract.md`)

---

#### [TC-01] Đổi tên 5 tín hiệu — Breaking Change không có Migration Notice

**Yêu cầu bởi:** CDO-2
**Mức độ:** 🔴 Breaking

**Vị trí cần thay đổi:** Mục 4 — Đặc tả các Tín hiệu Telemetry; Mục 3 — JSON Schema (`signal_name` enum)

**Hiện trạng:**
Hợp đồng hiện tại (đã ký) định nghĩa 5 tên tín hiệu chính thức: `service_error_rate`, `service_latency_p95`, `container_resource_usage`, `application_log_event`, `distributed_trace_error_event`. Tuy nhiên, CDO-2 đã xây dựng toàn bộ pipeline, test cases, safety gate và tài liệu kỹ thuật dựa trên bộ tên cũ từ phiên bản draft:

| Tên cũ (CDO-2 đã dùng) | Tên mới (hợp đồng đã ký) |
|---|---|
| `istio_request_error_rate` | `service_error_rate` |
| `istio_request_latency_p95` | `service_latency_p95` |
| `container_memory_working_set_bytes` | `container_resource_usage` |
| `app_log_error_event` | `application_log_event` |
| `trace_span_error_event` | `distributed_trace_error_event` |

**Thay đổi cần thực hiện:**
1. AIO Team xác nhận bằng văn bản rằng 5 tên trong hợp đồng đã ký là tên **chính thức cuối cùng** và sẽ không thay đổi thêm.
2. Phát hành **Migration Notice** chính thức kèm deadline cho CDO-2 hoàn tất cập nhật (đề xuất: EOD T2 W12 — 29/06/2026).
3. Bổ sung vào Mục 6 — Chính sách Versioning một mục lịch sử thay đổi (Change Log) ghi nhận việc đổi tên này.
4. Xem xét thêm comment giải thích lý do đổi tên (ví dụ: từ tên cụ thể Istio sang tên trung lập vendor để hỗ trợ multi-platform).

**Lý do:**
Không có thông báo thay đổi tên chính thức trong khi CDO đã build theo tên cũ là một vi phạm thực tiễn quản lý hợp đồng, gây ra rủi ro mismatch dữ liệu nghiêm trọng tại `/v1/detect` — AI Engine sẽ nhận signal không khớp enum và từ chối với `400 Bad Request`.

---

#### [TC-02] Kênh truyền telemetry (SQS) bị loại bỏ không thông báo

**Yêu cầu bởi:** CDO-2
**Mức độ:** 🟠 Major

**Vị trí cần thay đổi:** Mục 5 — Thuộc tính Vận hành & Telemetry SLA (cột "Điểm phát / Emit Point")

**Hiện trạng:**
Hợp đồng hiện tại liệt kê emit point là `"Ingestion Prometheus / OTel"` và `"OTel Log Collector / Fluentd"` — không nhắc đến SQS. Tuy nhiên, CDO-2 đã thiết kế SQS Queue làm lớp buffer trung gian trong `02_infra_design.md` của mình dựa trên tài liệu hợp đồng phiên bản trước khi ký.

**Thay đổi cần thực hiện:**
1. AIO Team xác nhận rõ ràng **kiến trúc truyền telemetry chính thức** từ CDO Platform sang AI Engine theo một trong các mô hình sau:
   - **Mô hình A (Pull-based):** AI Engine tự pull metrics qua Prometheus scrape endpoint; CDO không cần push chủ động.
   - **Mô hình B (Push-based HTTP):** CDO gửi dữ liệu trực tiếp qua HTTP `POST /v1/detect` theo thời gian thực.
   - **Mô hình C (Queue-based):** CDO emit vào SQS/Kinesis; AI Engine (hoặc middleware) consume từ queue.
2. Sau khi chốt mô hình, cập nhật cột "Điểm phát (Emit Point)" trong bảng Telemetry SLA (Mục 5) để phản ánh chính xác.
3. Nếu Mô hình B được chọn (push qua HTTP), ghi rõ CDO chịu trách nhiệm batch và gửi `telemetry_window` trong body của `/v1/detect`.

**Lý do:**
CDO-1 đã thiết kế CDO-1 nội bộ với Kinesis Firehose cho audit trail, còn CDO-2 có thể đã thiết kế khác. Sự mơ hồ về kênh truyền dẫn đến hai CDO có thể build hai pipeline hoàn toàn không tương thích với AI Engine.

---

### Hợp đồng 2: AI API Contract (`ai-api-contract.md`)

---

#### [API-01] `pattern_type` thiếu định nghĩa hành vi CDO

**Yêu cầu bởi:** CDO-2
**Mức độ:** 🟠 Major

**Vị trí cần thay đổi:** Mục 3.2 — Endpoint `/v1/decide` → Response Body Schema (trường `pattern_type`)

**Hiện trạng:**
Response của `/v1/decide` trả về `pattern_type: "urgent"` hoặc `pattern_type: "deferred"` nhưng hợp đồng không định nghĩa CDO phải làm gì khác nhau với hai giá trị này.

**Thay đổi cần thực hiện:**
Bổ sung bảng định nghĩa hành vi vào Mục 3.2, ngay sau phần mô tả trường `pattern_type`:

| `pattern_type` | Luồng thực thi CDO | Cơ chế | Target latency |
|---|---|---|---|
| `"urgent"` | **Path B — Direct Patch:** CDO gọi K8s API trực tiếp để vá/restart ngay lập tức; async Git sync sau | K8s API Patch → ArgoCD async reconcile | < 15 giây cho patch nóng |
| `"deferred"` | **Path A — GitOps:** CDO commit thay đổi vào Git config-repo; ArgoCD tự sync vào cluster | Git commit → ArgoCD sync → K8s apply | < 120 giây |

Ngoài ra, AIO Team cần xác nhận thêm:
- CDO có cần delay execution với `deferred` không, hay bắt đầu luồng GitOps ngay?
- Safety gate và blast-radius check có khác nhau giữa `urgent` và `deferred` không?

**Lý do:**
Thiếu định nghĩa hành vi khiến CDO không thể implement executor logic. Đặc biệt với CDO-1 đã chọn "GitOps Hybrid" làm architecture angle, việc phân biệt hai path này là yếu tố cốt lõi trong thiết kế Self-Heal Controller.

---

#### [API-02] Action enum đổi tên và thêm action mới không có changelog

**Yêu cầu bởi:** CDO-2
**Mức độ:** 🔴 Breaking

**Vị trí cần thay đổi:** Mục 3.2 — `action_plan[].action` enum definition

**Hiện trạng:**

| Tên cũ (CDO-2 đã dùng) | Tên mới (hợp đồng đã ký) | Loại thay đổi |
|---|---|---|
| `ADJUST_MEMORY_LIMIT` | `PATCH_MEMORY_LIMIT` | Đổi tên |
| `SCALE_UP_PODS` | `SCALE_REPLICAS` | Đổi tên |
| `UPDATE_ENV_SECRET` | `ROTATE_SECRET` | Đổi tên |
| *(không có)* | `ROLLOUT_UNDO` | Action mới |
| *(không có)* | `DELETE_POD` | Action mới — **nhạy cảm** |

**Thay đổi cần thực hiện:**
1. Phát hành **Action Enum Changelog** chính thức kèm migration deadline cho CDO-2.
2. Bổ sung vào Mục 3.2 bảng mô tả chi tiết cho **2 action mới**:

**`ROLLOUT_UNDO`:**
- **Khi nào AI trả về:** Khi phát hiện sự cố do deployment version mới gây ra (regression pattern), `confidence` > 0.85, và `trigger_metric` là `service_error_rate` hoặc `service_latency_p95`.
- **CDO làm gì:** Thực hiện `kubectl rollout undo deployment/<name>` hoặc `git revert <commit-sha>` → ArgoCD sync; KHÔNG rollback tự động quá 1 lần liên tiếp.
- **Params bắt buộc:** `namespace`, `deployment`, tùy chọn `revision` (nếu rollback về revision cụ thể).

**`DELETE_POD`:**
- **Khi nào AI trả về:** Chỉ khi pod ở trạng thái `CrashLoopBackOff` không tự phục hồi sau `RESTART_DEPLOYMENT`, và pod là single-instance không ảnh hưởng đến `available_replicas`.
- **CDO làm gì:** Gọi K8s API xóa pod; Deployment Controller tự tạo pod mới.
- **Giới hạn an toàn:** Chỉ được phép trên namespace trong `blast_radius_config.allowed_namespaces`; bắt buộc có `dry_run_mode` check trước; không xóa pod cuối cùng còn lại (kiểm tra `available_replicas > 1`).
- **Params bắt buộc:** `namespace`, `pod_name`.

3. Xem xét đưa `DELETE_POD` vào danh sách action cần **xác nhận thêm** (high-risk action requiring extra circuit breaker check).

**Lý do:**
Breaking change mà không có changelog phá vỡ allow-list và executor logic CDO-2 đã xây dựng. `DELETE_POD` đặc biệt nguy hiểm vì tác động trực tiếp đến workload đang chạy — thiếu spec rõ ràng có thể dẫn đến CDO implement theo cách unsafe.

---

#### [API-03] SLA `/v1/decide` tăng 6 lần — Cần xác nhận fallback conditions

**Yêu cầu bởi:** CDO-2
**Mức độ:** 🟡 Medium

**Vị trí cần thay đổi:** Mục 4 — KPI Target & Throughput SLAs

**Hiện trạng:**
SLA `/v1/decide` đã tăng từ `< 500ms` lên `< 3000ms` trong phiên bản hợp đồng đã ký. Hợp đồng có đề cập fallback rule-based `< 500ms` nhưng không nêu rõ điều kiện kích hoạt.

**Thay đổi cần thực hiện:**
Bổ sung vào Mục 4, ngay sau bảng KPI Target, một đoạn mô tả **điều kiện kích hoạt fallback rule-based**:

```
Fallback Rule-Based Mode kích hoạt khi:
1. Chi phí Bedrock hàng ngày vượt $50/tenant (cost_cap_exceeded = true)
2. Bedrock API trả lỗi 429 (Throttling) sau 3 lần retry với Exponential Backoff
3. Timeout LLM vượt quá 2500ms (buffer 500ms trước khi đạt SLA 3000ms)
4. Bedrock trả lỗi 5xx liên tục ≥ 2 lần trong 5 phút (Circuit Breaker)

Trong fallback mode:
- Latency target: < 500ms
- Response vẫn đúng schema nhưng `reasoning` sẽ ghi rõ "fallback_rule_based_mode"
- `cost_cap_exceeded: true` khi reason là budget
```

**Lý do:**
Với SLA 3000ms và detect (300ms) + verify (500ms), tổng thời gian AI xử lý có thể lên đến ~3.8 giây. Nếu có retry và fallback không rõ ràng, CDO không thể tính toán được worst-case end-to-end latency để thiết kế timeout hợp lý cho toàn pipeline.

---

#### [API-04] `success_conditions` DSL thiếu grammar spec và ánh xạ metric

**Yêu cầu bởi:** CDO-2 và CDO-1
**Mức độ:** 🟠 Major

**Vị trí cần thay đổi:** Mục 3.2 — `verify_policy.success_conditions` field description

**Hiện trạng:**
Response `/v1/decide` trả về mảng:
```json
"success_conditions": [
  "pod_ready == true",
  "restart_count_no_increase == true",
  "container_memory_usage_pct < 80"
]
```
Đây là một mini DSL nhưng không có grammar spec, không có ánh xạ metric name sang telemetry signal, không định nghĩa behavior khi parse lỗi.

**Thay đổi cần thực hiện:**

**Phương án A (Ưu tiên) — Publish DSL Spec đầy đủ:**
Bổ sung vào Mục 3.2 bảng Condition Grammar và Metric Mapping:

| Condition trong `success_conditions` | `signal_name` tương ứng trong Telemetry Contract | Cách CDO lấy giá trị | Loại comparison |
|---|---|---|---|
| `pod_ready == true` | `container_resource_usage` + K8s pod status API | Query K8s API: `pods/<name>/status.containerStatuses[].ready` | boolean |
| `restart_count_no_increase == true` | `container_resource_usage` (labels: restart_count) | So sánh restart_count tại t+0 và t+verify_window | boolean (delta == 0) |
| `container_memory_usage_pct < N` | `container_resource_usage` | `value / memory_limit_bytes * 100 < N` | numeric comparison |
| `service_error_rate < N` | `service_error_rate` | `value < N` | numeric comparison |
| `service_latency_p95 < N` | `service_latency_p95` | `value < N` | numeric comparison |

Operators hỗ trợ: `==`, `!=`, `<`, `>`, `<=`, `>=`

Parse error behavior: Nếu CDO không nhận dạng được 1 condition → **treat as FAIL** (conservative) và log warning.

**Phương án B (Đơn giản hóa) — Thay bằng enum predefined:**
Nếu DSL quá phức tạp để implement trong timeline capstone, thay thế bằng enum có sẵn:
```json
"success_conditions": ["pod_ready", "error_rate_normal", "memory_safe", "latency_normal"]
```

**Lý do:**
CDO phải implement evaluator để đánh giá conditions này sau mỗi action. Không có spec → CDO không thể code; metric name trong condition không khớp telemetry contract → data pipeline broken.

---

#### [API-05] `cost_cap_exceeded` thiếu trong JSON Schema chính thức

**Yêu cầu bởi:** CDO-2
**Mức độ:** 🟠 Major

**Vị trí cần thay đổi:** Mục 3.2 — JSON Schema `DecideResponse` (Lược đồ Schema Phản hồi)

**Hiện trạng:**
Hợp đồng mô tả `cost_cap_exceeded` trong bảng field description nhưng **chưa khai báo trong JSON Schema chính thức**. Schema hiện tại có `"additionalProperties": false` — có nghĩa là bất kỳ field nào không khai báo trong `properties` sẽ khiến CDO validator từ chối response với lỗi validation.

**Thay đổi cần thực hiện:**
1. Thêm `cost_cap_exceeded` vào `properties` của `DecideResponse` schema:
```json
"cost_cap_exceeded": {
  "type": "boolean",
  "description": "Cờ báo hiệu chi phí LLM Bedrock hàng ngày của tenant đã vượt hạn mức $50. Khi true, engine chuyển sang chế độ rule-based nhưng action_plan vẫn hợp lệ để execute."
}
```
2. Bổ sung vào mảng `"required"` nếu field này luôn có mặt, hoặc giữ là optional nếu chỉ xuất hiện khi có sự kiện.
3. Bổ sung hướng dẫn CDO behavior khi nhận `cost_cap_exceeded: true`:

> **CDO Behavior khi `cost_cap_exceeded: true`:**
> - `action_plan` vẫn hợp lệ và CDO nên **tiếp tục execute** bình thường.
> - CDO không cần escalate do field này.
> - CDO nên ghi nhận vào audit log event `COST_CAP_EXCEEDED` kèm `correlation_id` để AI Team theo dõi.
> - CDO không cần xử lý khác biệt — sự khác biệt chỉ nằm trong chất lượng `reasoning` của AI Engine (rule-based thay vì LLM-generated).

**Lý do:**
Schema với `additionalProperties: false` là best practice cho type safety, nhưng đòi hỏi mọi field có thể xuất hiện trong response phải được khai báo. Field thiếu trong schema sẽ phá vỡ CDO validation middleware trong production build.

---

### Hợp đồng 3: Deployment Contract (`deployment-contract.md`)

---

#### [DC-01] Thiếu IAM Role Template cho tenant ABAC AssumeRole

**Yêu cầu bởi:** CDO-1
**Mức độ:** 🟠 Major

**Vị trí cần thay đổi:** Mục 3.E — Phân lập dữ liệu đa thuê bao qua AWS STS AssumeRole & Session Tags

**Hiện trạng:**
Hợp đồng hiện tại mô tả AI Engine sẽ `AssumeRole` vào `arn:aws:iam::*:role/tf-3-tenant-[tenant_id]-role` và yêu cầu CDO tạo role này. Tuy nhiên hợp đồng không cung cấp:
- Danh sách permissions cần thiết cho role
- Trust policy (ai được phép assume role này)
- Scope: single-account hay cross-account

**⚠️ Tác động từ mô hình deployment mới (v1.1):** Capstone announcement đã xác nhận **mỗi CDO tự host AI Engine trên AWS account riêng của mình**. Điều này có nghĩa là AI Engine ECS task role và CDO resources (S3, DynamoDB) **nằm trên cùng một AWS account của CDO đó** — không còn là kiến trúc cross-account nữa. Trust Policy vì vậy phải trust chính ECS task role trong cùng account, không phải trust một account AI riêng biệt.

**Thay đổi cần thực hiện:**
Bổ sung vào Mục 3.E một subsection mới **"F. Tenant Role Specification Template"** với nội dung phù hợp mô hình **CDO self-hosted**:

**Trust Policy template (CDO provisioned — same-account vì CDO tự host engine):**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::<CDO_AWS_ACCOUNT_ID>:role/tf-3-ai-engine-task-role"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "sts:ExternalId": "<tenant_id>"
        }
      }
    }
  ]
}
```

> **Lưu ý:** `<CDO_AWS_ACCOUNT_ID>` là account của chính CDO đó (CDO-1 dùng account CDO-1; CDO-2 dùng account CDO-2). Đây là same-account AssumeRole, không phải cross-account, vì mỗi CDO host engine riêng.

**Permission Policy template (CDO provisioned — scope giới hạn trong resources của CDO đó):**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadAuditTrail",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::tf3-sh-audit-logs",
        "arn:aws:s3:::tf3-sh-audit-logs/tenant_id=<tenant_id>/*"
      ],
      "Condition": {
        "StringEquals": {
          "aws:PrincipalTag/TenantID": "<tenant_id>"
        }
      }
    },
    {
      "Sid": "DenyOtherTenantData",
      "Effect": "Deny",
      "Action": "s3:*",
      "Resource": "arn:aws:s3:::tf3-sh-audit-logs/*",
      "Condition": {
        "StringNotEquals": {
          "aws:PrincipalTag/TenantID": "<tenant_id>"
        }
      }
    }
  ]
}
```

AIO Team cần xác nhận rõ trong hợp đồng: với mô hình mỗi CDO tự host engine, **scope là same-account per CDO** — mỗi CDO tự provision role này trong account của mình cho engine instance của mình.

**Lý do:**
Không có template → CDO-1 không biết permission gì là đủ và không thừa. Mô hình deployment mới làm thay đổi hoàn toàn cách Trust Policy được viết — nếu CDO dùng cross-account ARN format cho same-account setup, `AssumeRole` sẽ luôn thất bại với lỗi authorization.

---

#### [DC-02] Tham chiếu ArgoCD sai ngữ cảnh cho AI Engine rollback

**Yêu cầu bởi:** CDO-2
**Mức độ:** 🟡 Medium

**Vị trí cần thay đổi:** Mục 6.C — Cơ chế Rollback

**Hiện trạng:**
Mục 6.C hiện có đề cập đến ArgoCD như một phương thức rollback cho AI Engine. Tuy nhiên AI Engine chạy trên **ECS Fargate** — ArgoCD là GitOps controller cho Kubernetes, không thể quản lý ECS services hay task definitions.

**Thay đổi cần thực hiện:**
Thay thế mọi tham chiếu ArgoCD trong Mục 6.C bằng cơ chế rollback ECS chuẩn. Ví dụ nội dung cập nhật:

> **Phương thức rollback AI Engine (ECS Fargate):**
> - **Phương thức chính:** AWS CodeDeploy Blue/Green Deployment — khi CloudWatch Alarm cảnh báo, CodeDeploy tự động chuyển traffic về Task Set phiên bản ổn định trước đó.
> - **Phương thức dự phòng:** ECS Deployment Circuit Breaker — nếu tasks mới không vượt qua health check sau N lần, ECS service tự rollback về Task Definition version ổn định gần nhất.
> - **Lưu ý:** ArgoCD **không quản lý** AI Engine. ArgoCD chỉ được CDO sử dụng để quản lý workload Kubernetes trong EKS cluster (CDO side).

**Lý do:**
Tham chiếu ArgoCD trong deployment contract của AI Engine (ECS) gây nhầm lẫn kỹ thuật nghiêm trọng. Kỹ sư đọc contract có thể hiểu nhầm phải setup ArgoCD để quản lý ECS — điều này là không thể và lãng phí thời gian.

---

#### [DC-03] Phạm vi IAM ABAC và cấu hình chưa đủ chi tiết

**Yêu cầu bởi:** CDO-1
**Mức độ:** 🟠 Major *(tăng từ Medium — do tác động của mô hình deployment mới)*

**Vị trí cần thay đổi:** Mục 3.E — Phân lập dữ liệu đa thuê bao qua AWS STS AssumeRole & Session Tags

**Hiện trạng:**
Mục 3.E mô tả cơ chế ABAC qua Session Tags nhưng không làm rõ scope của mechanism này — đặc biệt không nêu rõ đây là single-account hay cross-account setup, và không cung cấp ví dụ Resource-based policy điều kiện ABAC đầy đủ.

**⚠️ Tác động từ mô hình deployment mới (v1.1):** Với việc mỗi CDO tự host engine trên platform riêng, scope IAM ABAC **đã được xác định rõ là same-account per CDO** (xem [DC-01]). Hệ quả: các Resource Policy trong Mục 3.E cần được viết theo ARN format same-account, và cơ chế Session Tags propagation cần được kiểm tra lại trong ngữ cảnh CDO tự vận hành engine.

**Thay đổi cần thực hiện:**
1. Ghi rõ trong hợp đồng: **Scope là same-account per CDO** — mỗi CDO host engine trên AWS account của mình, toàn bộ resources (S3, DynamoDB, ECS) nằm trong cùng account đó.
2. Bổ sung ví dụ Resource Policy cho DynamoDB Idempotency Lock với điều kiện ABAC (same-account format):
```json
{
  "Condition": {
    "StringEquals": {
      "aws:PrincipalTag/TenantID": "${dynamodb:LeadingKeys}"
    }
  }
}
```
3. Xác nhận Session Tags CDO engine phải gắn khi gọi: hiện tại chỉ có `TenantID` — xác nhận đây là đủ hay cần thêm tag `CDOPlatform` để phân biệt request từ CDO-1 vs CDO-2 trong audit trail chung.
4. Cập nhật ví dụ ARN trong Mục 3.E từ wildcard `arn:aws:iam::*:role/...` sang format rõ ràng phù hợp same-account setup.

**Lý do:**
Mô hình mới (CDO self-hosted) giải quyết câu hỏi single vs cross-account — nhưng hợp đồng vẫn đang dùng wildcard ARN `*` gây mơ hồ. CDO-1 cần ARN format chính xác để provision đúng IAM infrastructure mà không phải đoán.

---

## Next Steps / Implementation Checklist

Dưới đây là danh sách hành động cụ thể cần hoàn thành **trước Integration Session T3 W12 (30/06/2026)**.

### AIO Team — Action Required

#### Ưu tiên Cao (Deadline: EOD T2 W12 — 29/06)

- [ ] **[TC-01]** Gửi văn bản xác nhận 5 tên `signal_name` là chính thức; phát hành Migration Notice cho CDO-2 với deadline migration.
- [ ] **[API-02]** Phát hành Action Enum Changelog chính thức; bổ sung spec đầy đủ cho `ROLLOUT_UNDO` và `DELETE_POD` vào `ai-api-contract.md`.
- [ ] **[API-01]** Bổ sung bảng định nghĩa hành vi CDO cho `pattern_type: "urgent"` vs `"deferred"` vào Mục 3.2.
- [ ] **[API-05]** Thêm `cost_cap_exceeded` vào JSON Schema chính thức của `DecideResponse`; bổ sung mô tả CDO behavior.
- [ ] **[API-04]** Quyết định giữa Phương án A (DSL spec đầy đủ) và Phương án B (enum đơn giản hóa); publish spec tương ứng.
- [ ] **[DC-01]** Cung cấp IAM Role Trust Policy template và Permission Policy template cho `tf-3-tenant-[tenant_id]-role`.
- [ ] **[DC-02]** Sửa Mục 6.C của Deployment Contract — thay ArgoCD bằng cơ chế ECS rollback.
- [ ] **[TC-02]** Xác nhận và ghi rõ kênh truyền telemetry chính thức (Mô hình A/B/C); cập nhật Mục 5.

#### Ưu tiên Trung bình (Deadline: Trước Integration Session T3 W12)

- [ ] **[API-03]** Bổ sung điều kiện kích hoạt fallback rule-based chi tiết vào Mục 4.
- [ ] **[DC-03]** Xác nhận scope IAM ABAC (single-account vs cross-account); bổ sung ví dụ Resource Policy điều kiện ABAC.

---

### CDO-1 — Action Required

- [ ] Sau khi nhận IAM Role template từ AIO, provision `tf-3-tenant-[tenant_id]-role` trong AWS account.
- [ ] Xác nhận network path và VPC connectivity tới AI Engine internal endpoint sau khi AIO cung cấp DNS và Security Group details.
- [ ] Cập nhật thiết kế Self-Heal Controller theo định nghĩa `pattern_type` (urgent = Path B, deferred = Path A) sau khi AIO confirm.

### CDO-2 — Action Required

- [ ] Sau khi nhận Migration Notice từ AIO, cập nhật toàn bộ pipeline, test cases, allow-list sang tên signal/action mới.
- [ ] Cập nhật executor logic theo định nghĩa chính thức của `ROLLOUT_UNDO` và `DELETE_POD`.
- [ ] Cập nhật JSON Schema validator để bao gồm `cost_cap_exceeded` khi AIO publish schema mới.
- [ ] Cập nhật thiết kế `02_infra_design.md` để phản ánh kênh truyền telemetry chính thức sau khi AIO confirm.

### Tất cả — Xác nhận

- [ ] **T2 W12 (29/06) — 14:00:** Standup xác nhận AIO đã publish tất cả items Ưu tiên Cao; CDO xác nhận đã nhận và bắt đầu cập nhật.
- [ ] **T3 W12 (30/06) — 14:00:** Integration Session — CDO gọi AI endpoint thật; toàn bộ schema và enum phải đã sync trước thời điểm này.
- [ ] **T3 W12 (30/06) — 16:00:** Task Force Sync — review E2E test result và xác nhận không còn contract mismatch.

---

## Phụ lục — Ma trận Rủi ro nếu Không Xử lý

| Điểm không xử lý | Rủi ro kỹ thuật | Rủi ro buổi chấm |
|---|---|---|
| [TC-01] Signal name mismatch | AI Engine từ chối 100% telemetry với `400 Bad Request` | E2E demo không chạy được; auto-resolve rate = 0% |
| [API-02] Action enum sai | CDO executor không nhận dạng được action → no-op; `DELETE_POD` không có guard → unsafe mutation | Safety sub-checkpoint fail; "Zero unsafe action" requirement vi phạm |
| [API-05] Schema thiếu field | CDO validator reject response → CDO không nhận kết quả từ `/v1/decide` | Pipeline broken ở bước Decide; không thể demo |
| [API-04] DSL không có spec | CDO không thể implement verify evaluator → verify luôn fail | Auto-resolve rate không đạt 60%; rollback không trigger đúng |
| [DC-01] IAM Role thiếu spec | AI Engine nhận 403 khi AssumeRole → không thể đọc tenant data | Multi-tenant isolation không hoạt động; RBAC requirement fail |
| [DC-02] ArgoCD reference sai | Kỹ sư CDO mất thời gian debug rollback không hoạt động | Rollback sub-checkpoint không có evidence |

---

*Tài liệu này được soạn bởi AIO Team — Task Force 3 dựa trên pushback từ CDO-2 và Context Report từ CDO-1 (TF3-CDO1-AIOPS-HANDOFF-001). Mọi thay đổi đề xuất trong tài liệu này phải được hai bên ký xác nhận trước khi cập nhật hợp đồng chính thức. Mọi thay đổi vào hợp đồng đã freeze phải tuân thủ Change-Request Process theo Mục 6 của từng hợp đồng.*
