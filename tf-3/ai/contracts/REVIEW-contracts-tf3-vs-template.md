# Review Contracts — AIops-g4 (tf-3) đối chiếu Template TechX-Corp

> **Đối tượng review:** `AIops-g4/Capstone-Phase-2-Code` tại `tf-3/ai/contracts/` (3 file)
> **Template chuẩn:** `TechX-Corp/xbrain-learners` tại `capstone-phase2/templates/ai/contracts/` (3 file)
> **Ngày review:** 2026-06-24
> **Phạm vi:** `ai-api-contract.md`, `deployment-contract.md`, `telemetry-contract.md`

---

## 1. Kết luận nhanh (verdict)

**Đúng hướng và VƯỢT template ở nhiều mặt** (JSON Schema chính thức, idempotency, audit WORM, IAM least-privilege, blast-radius, readiness). Tuy nhiên **chưa thể "pass" ngay** vì:

- 🔴 **1 mâu thuẫn kiến trúc nghiêm trọng**: hai contract nói khác nhau về *ai thực thi hành động remediation* (CDO hay AI Engine) — đây chính là ranh giới trách nhiệm mà contract sinh ra để định nghĩa.
- 🔴 **1 lỗ hổng tuân thủ**: Telemetry **không có điều khoản PII/dữ liệu nhạy cảm**, trong khi `application_log_event` đẩy nguyên stack trace — mâu thuẫn với chính cam kết SOC2 của họ.
- 🟠 **Bỏ sót mảng vận hành/SLA của Telemetry + Versioning** mà template yêu cầu.

Mức độ hoàn thiện kỹ thuật cao hơn template; nhưng vài chỗ "làm thêm" lại tạo ra mâu thuẫn nội bộ và bỏ rơi vài ràng buộc gốc của template.

---

## 2. Bảng đối chiếu coverage so với template

### 2.1 AI API Contract

| Yêu cầu template | Team tf-3 | Trạng thái |
|---|---|---|
| Endpoints: `/v1/detect`, `/v1/verify` | `/v1/detect` + **`/v1/decide`** + `/v1/verify` (tách detect↔plan) | ⚠️ Lệch (cải tiến) |
| Versioning (breaking→/v2, dual-support 30d, minor bump) | Chỉ ghi base path `/v1/` | ❌ Thiếu |
| Auth: IAM SigV4 + **cross-account STS assume-role (session tag tenant_id)** + audit auth event | Chỉ IAM SigV4 | ⚠️ Thu hẹp |
| Rate limit: per-tenant + **global circuit breaker** + header `Retry-After` | 120 req/min/tenant; không có global/Retry-After | ⚠️ Một phần |
| Response `detect`: `suggested_action`, **`reasoning`**, `audit_id` | thay bằng `anomaly_context{...}`, `correlation_id`; **bỏ `reasoning`** | ⚠️ Lệch |
| Error codes: 400/401/429/503 | 400/**409**/429/503 (thêm 409, **bỏ 401**) | ⚠️ Lệch |
| JSON Schema chính thức | Có draft-07 cho mọi req/resp | ✅ Vượt |
| Idempotency, dry-run, blast_radius, escalation_bundle | Có đủ | ✅ Vượt |

### 2.2 Deployment Contract

| Yêu cầu template | Team tf-3 | Trạng thái |
|---|---|---|
| Compute / Scaling / Cooldown | Khớp (Fargate, min2/max10, 70% CPU, 60/300s) | ✅ |
| Secrets qua Secrets Manager, không long-lived key | Có + thêm kubeconfig path | ✅ |
| Networking private + internal ALB + SG-to-SG | Có, chi tiết hơn | ✅ Vượt |
| Canary 10→50→100 + abort criteria | Có; **bỏ tiêu chí "burn-rate fast alert"** | ⚠️ Một phần |
| Rollback ArgoCD + RTO<60s | Khớp | ✅ |
| Health check | **`/health` + `/ready` (dependency) + `/metrics`** | ✅ Vượt |
| Failure modes table | Có; **bỏ "Region outage / multi-region failover"** | ⚠️ Một phần |
| IAM role split + least-privilege | **Execution vs Task role + example policy + forbidden actions** | ✅ Vượt |
| Idempotency Lock + Audit WORM (SOC2) | **DynamoDB/Redis lock + S3 Object Lock 90d** | ✅ Vượt |
| Open question: **cost cap/ngày** | Bị bỏ, không thay thế | ❌ Thiếu |

### 2.3 Telemetry Contract

| Yêu cầu template | Team tf-3 | Trạng thái |
|---|---|---|
| ≥ 2-3 signals | **5 signals** cụ thể | ✅ Vượt |
| Per-signal: Type, Labels, Unit, **Frequency, Emit point, Retention, Emit SLA, Volume SLA, Cost estimate, Used for** | Chỉ có Type, Purpose, Value, Payload | ❌ Thiếu phần lớn (vận hành/SLA) |
| Versioning + change-request process | Không có | ❌ Thiếu |
| Tenant scoping + time precision (ms) | Có | ✅ |
| **PII: cấm PII, anonymize ở ingestion** | **Không có** | ❌ Thiếu (nghiêm trọng) |
| **Schema validation → dead-letter queue** | Có JSON Schema; **không có DLQ/xử lý malformed** | ⚠️ Một phần |
| JSON Schema chính thức + labels trace_id/span_id | Có draft-07, rich labels | ✅ Vượt |

---

## 3. Điểm mạnh (giữ nguyên & phát huy)

1. **JSON Schema draft-07 cho toàn bộ** request/response + telemetry datapoint → contract *testable/validatable tự động*, không chỉ là văn bản. Đây là nâng cấp lớn so với template (template chỉ có bảng + ví dụ).
2. **Tách `detect → decide → verify`** (separation of concerns): detection tách khỏi planning rõ ràng hơn template (gộp detect+action). `correlation_id` xuyên suốt 3 bước → trace tốt.
3. **Idempotency-Key + DynamoDB conditional write / Redis TTL 5m → 409 Conflict**: chống double-execution (restart 2 lần, scale gấp đôi) — đúng nỗi lo retry-storm của remediation tự động.
4. **Tamper-evident audit log**: S3 Object Lock WORM Compliance ≥ 90 ngày + truy vấn qua Athena → vượt template (template chỉ có `audit_id`).
5. **IAM least-privilege thực chất**: tách Task Execution Role vs Task Role, có example policy + *forbidden actions* (`iam:*`, `ec2:*`).
6. **An toàn cho autonomous remediation**: `blast_radius_config` (max_pod_impact_pct, circuit_breaker_error_rate, allowed_namespaces) + `dry_run_mode`/Mock simulation → khớp tinh thần W3 (blast radius, chaos safety).
7. **Readiness đúng chuẩn**: `/ready` check dependency (bedrock/dynamodb/s3) tách khỏi `/health` liveness + `/metrics` Prometheus → đúng bài học `/healthz` vs `/readyz` (W2-D3).
8. **Telemetry giàu & thực tế**: 5 signal có `trace_id`/`span_id` (distributed tracing), `escalation_bundle` cho human handoff, payload mẫu sát thật.
9. **Nhất quán liên-contract**: `signal_name` enum khớp giữa telemetry ↔ api; tenant ID khớp; `telemetry_window`/`post_telemetry_window` cùng schema.

---

## 4. Điểm yếu & cách khắc phục (theo mức độ)

### 🔴 CRITICAL — phải sửa trước khi ký/đóng băng

**C1. Mâu thuẫn "AI Engine hay CDO thực thi remediation?"**
- API contract ([ai-api §1](https://github.com/AIops-g4/Capstone-Phase-2-Code/blob/main/tf-3/ai/contracts/ai-api-contract.md)): luồng ghi rõ *"…/v1/decide → **CDO Thực thi** → /v1/verify"*, và `action_executed` = "hành động **CDO** đã thực thi".
- Deployment contract ([deployment §3, §5 diagram, secrets](https://github.com/AIops-g4/Capstone-Phase-2-Code/blob/main/tf-3/ai/contracts/deployment-contract.md)): Task Role nêu "gọi … **EKS API**", secrets có **kubeconfig** cho EKS, sơ đồ vẽ `ECS (AI Engine) → EKS_API: Execute Self-Heal Actions`. ⇒ ngụ ý **AI Engine tự thực thi** trên K8s.
- Hệ quả: ranh giới trách nhiệm — thứ mà contract tồn tại để chốt — bị nhập nhằng; kéo theo việc *ai enforce blast_radius*, *ai giữ idempotency lock* (deployment nói CDO dùng DynamoDB, nhưng IAM/egress lại cấp DynamoDB cho AI Engine) cũng mâu thuẫn.
- **Khắc phục:** chọn DỨT KHOÁT 1 mô hình và đồng bộ cả 3 file:
  - (a) *CDO executes* (đúng template): bỏ kubeconfig + EKS API khỏi AI Engine Task Role, bỏ mũi tên ECS→EKS, blast_radius do CDO enforce. **Khuyến nghị** vì giữ ranh giới AI(brain)/CDO(hands).
  - (b) *AI Engine executes* (POC sandbox): sửa API contract bỏ "CDO Thực thi", thêm egress→EKS API server, thêm `eks:*`/RBAC vào Task Role, ghi rõ ai enforce blast_radius. Nếu chọn (b), nêu rõ đây là shortcut POC, không phải mô hình production.

**C2. Telemetry thiếu điều khoản PII/secret — mâu thuẫn cam kết SOC2**
- Template bắt buộc: *"KHÔNG được chứa PII … anonymize ở ingestion layer của CDO"*. Team **bỏ hẳn** điều khoản này.
- Rủi ro cụ thể: signal `application_log_event` đẩy **raw stack trace** (vd `OrderService.java:102`) — loại dữ liệu hay lẫn email/SĐT/token/connection string. Đẩy thẳng sang AI Engine + lưu WORM 90 ngày = lưu PII/secret bất biến 90 ngày → ngược với chính phần "SOC2 compliance" họ tự đặt.
- **Khắc phục:** thêm mục "Cross-cutting / Data handling" vào telemetry: (1) cấm PII trong `value`/`labels`; (2) CDO redact/anonymize stack trace + scrub secret (regex token/connstring) ở ingestion *trước* khi gửi; (3) nêu rõ trách nhiệm scrub thuộc CDO; (4) cân nhắc field hash thay vì raw cho định danh.

**C3. Link hỏng + lộ đường dẫn máy cá nhân**
- `ai-api-contract.md` tham chiếu telemetry qua `file:///home/duckq1u/Documents/Aiops-g4/capstone/dataset/contracts/telemetry-contract.md` (xuất hiện **2 lần**).
- Vấn đề: (1) link chết với mọi người khác; (2) lộ username + cấu trúc máy cá nhân; (3) đường dẫn `capstone/dataset/contracts/` còn *khác* path repo thật `tf-3/ai/contracts/` → chứng tỏ file đang lệch nguồn.
- **Khắc phục:** đổi sang relative link `./telemetry-contract.md#3-...`; rà soát còn `file:///` hay path tuyệt đối nào khác không.

### 🟠 HIGH

**H1. Telemetry thiếu toàn bộ thuộc tính vận hành/SLA per-signal.** Template yêu cầu mỗi signal có *Frequency, Emit point (pipeline), Retention (hot/cold), Emit SLA (event→consumable), Volume SLA (events/sec), Cost estimate*. Thiếu → AI không biết signal *tươi hay cũ* (staleness), không *size* được capacity, không tính được cost. **Khắc phục:** bổ sung bảng thuộc tính vận hành cho cả 5 signal (ít nhất Frequency + Emit SLA + Retention).

**H2. Thiếu chính sách Versioning.** AI API chỉ có base path `/v1/`; Telemetry mất hẳn mục versioning + change-request. Contract đã "🔒 FREEZE" mà không định nghĩa *đổi version thế nào* là tự mâu thuẫn. **Khắc phục:** thêm lại mục Versioning (breaking → `/v2`, dual-support tối thiểu N ngày, non-breaking = thêm field optional; quy trình change request).

**H3. Thiếu xử lý malformed telemetry (dead-letter).** Có JSON Schema nhưng không nói reject/DLQ. **Khắc phục:** ghi rõ "ingestion validate theo schema; payload sai → DLQ + alert", thống nhất với template.

**H4. SLA latency có thể bất khả thi nếu có LLM trong luồng.** `/v1/detect` p99 < 300ms, `/v1/decide` < 500ms; nhưng deployment cấp Bedrock + có failure mode "Bedrock throttling" ⇒ nếu `decide`/`detect` gọi LLM (Bedrock), p99 thường 1–5s, không thể < 500ms. **Khắc phục:** xác minh đường nào gọi LLM; hoặc nới SLA cho endpoint có LLM, hoặc tách xử lý async (queue + callback) và đặt SLA "time-to-accept" thay vì "time-to-answer".

### 🟡 MEDIUM

**M1. Mất cost governance.** Template có open question "cost cap per task force per ngày"; team bỏ hẳn, không có cơ chế chặn chi phí LLM cho remediation tự động (rủi ro runaway cost). → Thêm cost cap/ngày + alert.
**M2. Auth thu hẹp.** Mất cross-account STS assume-role (session tag `tenant_id`) + audit mọi auth event. → Bổ sung nếu multi-account; tối thiểu nêu lý do bỏ.
**M3. Egress SG thiếu rule tới EKS API server** dù sơ đồ/Task Role cần (nếu giữ mô hình AI-executes). → Đồng bộ theo quyết định C1.
**M4. Error code: bỏ `401`; `429` thiếu `Retry-After`.** → Thêm lại 401 (auth fail → refresh credential) và header `Retry-After` cho 429.
**M5. Signatory đổi sang vai trò chung** ("Principal AI Architect + Lead Platform Engineers") thay vì **song phương AI Lead + CDO Leads + Reviewer panel**. Mất tính "cả hai bên cùng ký" — bản chất của contract. → Trả lại mô hình ký song phương AI↔CDO.
**M6. Placeholder chưa điền trong contract đã FREEZE**: `[task_force_identifier]`, `[cdo_platform_name_1/2]`, cluster name. Frozen contract nên có giá trị cụ thể. → Điền giá trị thật của tf-3 (đã có tenant_id thật, làm nốt phần còn lại).

### 🟢 LOW / nit

- **L1.** `reasoning` (rationale ≤300 chars) bị bỏ khỏi `detect` response → giảm explainability/audit; cân nhắc thêm lại 1 field rationale ngắn.
- **L2.** Đổi tên `signal_window` → `telemetry_window`: lệch template nhưng self-consistent — chấp nhận được nếu hai bên đồng ý (ghi vào changelog).
- **L3.** `labels.level` dùng trong payload `application_log_event` nhưng không có trong bảng field/schema (chỉ lọt nhờ `additionalProperties:true`) → bổ sung vào bảng.
- **L4.** Bỏ label `region` (template có) — nhất quán với việc bỏ multi-region; OK nhưng nên ghi chú.
- **L5.** Bỏ "Open questions" ở cả 3 file → mất nơi track vấn đề chưa chốt (delivery exactly-once vs at-least-once, encryption-in-transit ngoài TLS, multi-region DR). Nên giữ lại mục này.
- **L6.** API SLA bỏ chỉ tiêu throughput (RPS) (template detect 100 RPS) → thêm lại.

---

## 5. Khắc phục theo thứ tự ưu tiên

1. **Chốt mô hình thực thi (C1)** → đồng bộ 3 file theo lựa chọn. *(blocking — ảnh hưởng kiến trúc, IAM, network, blast radius)*
2. **Thêm điều khoản PII + redaction (C2)** vào telemetry. *(blocking — compliance)*
3. **Sửa link `file:///` → relative (C3).** *(nhanh, 5 phút)*
4. Bổ sung **thuộc tính vận hành/SLA telemetry (H1)** + **Versioning (H2)** + **DLQ (H3)**.
5. **Verify SLA vs LLM (H4)** → điều chỉnh số hoặc tách async.
6. Dọn nốt MEDIUM (cost cap, auth, egress, 401/Retry-After, signatory, placeholder).
7. LOW/nit khi rảnh.

---

## 6. Một câu tóm tắt cho team

> "Contracts của tf-3 **vượt template về độ chặt chẽ kỹ thuật** (JSON Schema, idempotency, audit WORM, least-privilege, blast-radius) — rất tốt. Nhưng cần **chốt dứt khoát ai thực thi remediation** (đang mâu thuẫn giữa API và Deployment), **thêm điều khoản PII** cho log (đang ngược với cam kết SOC2 của chính mình), và **bù lại phần SLA/Versioning của Telemetry** mà template yêu cầu. Xong 3 việc này là đủ điều kiện ký."
