# Chi Tiết Các Thay Đổi Cần Thực Hiện Trên Các Hợp Đồng AI

Tài liệu này liệt kê chi tiết các phần, dòng cụ thể cần sửa trong các file hợp đồng tại thư mục [tf-3/ai/contracts/], lý do sửa đổi (đối chiếu theo phản hồi của CDO-02, báo cáo nền tảng CDOps và Q&A chính thức) và kết quả (outcome) kỳ vọng.

---

## 1. File [ai-api-contract.md]

### 1.1 Đặc tả luồng của `pattern_type`
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 267 (trong bảng mô tả Response Body của `/v1/decide`).
- **Lý do phải sửa**: CDO không biết phải xử lý khác nhau thế nào giữa `urgent` và `deferred`. Nếu không làm rõ, CDO có thể trì hoãn việc cứu service khẩn cấp hoặc áp dụng trực tiếp các thay đổi tối ưu hóa dài hạn gây drift hạ tầng.
- **Outcome kỳ vọng sau khi sửa**: Định nghĩa rõ ràng:
  - `urgent` (Path B - Direct apply): CDO chạy kiểm tra an toàn nhanh và apply trực tiếp qua Kubernetes API (bỏ qua GitOps PR) để giảm MTTR (<15s).
  - `deferred` (Path A - GitOps path): CDO tạo một Pull Request (PR) thay đổi cấu hình hạ tầng trong Git, đồng bộ tự động qua GitOps pipeline.

### 1.2 Bổ sung `DELETE_POD` vào Schema & Đặc tả hành động mới
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 270 (bảng mô tả), Dòng 331 (schema enum của `action_plan[].action`), Dòng 344 (schema params) và chèn thêm đoạn mô tả chi tiết ngay sau bảng (khoảng dòng 288).
- **Lý do phải sửa**: Hành động `DELETE_POD` hoàn toàn bị bỏ sót trong schema enum chính thức của `/v1/decide` response. Ngoài ra, CDO cần biết lúc nào AI trả về `DELETE_POD`/`ROLLOUT_UNDO` và các ràng buộc an toàn để tránh gây downtime khi xóa pod (Q&A Mục 2).
- **Outcome kỳ vọng sau khi sửa**:
  - Thêm `"DELETE_POD"` vào JSON schema enum.
  - Thêm thuộc tính `"pod_name"` vào object `params`.
  - Mô tả rõ: `ROLLOUT_UNDO` dùng để hoàn tác bản deploy lỗi; `DELETE_POD` dùng để giải quyết pod kẹt (CrashLoopBackOff/Evicted).
  - CDO bắt buộc kiểm tra điều kiện an toàn: chỉ thực thi `DELETE_POD` khi workload có `replicas > 1` và không vượt quá tỷ lệ ảnh hưởng `max_pod_impact_pct`.

### 1.3 Bổ sung `forbidden_namespaces` vào Blast Radius Config Schema
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 283 (bảng mô tả) và Dòng 356 (trong properties của `blast_radius_config` trong JSON Schema).
- **Lý do phải sửa**: Báo cáo nền tảng CDOps chỉ ra rằng các namespace hệ thống (`kube-system`, `observability`, `argocd`, `self-heal-system`) tuyệt đối cấm can thiệp. Việc truyền danh sách cấm này trong API giúp CDO tự động từ chối (deny) các quyết định vi phạm an toàn.
- **Outcome kỳ vọng sau khi sửa**: Bổ sung trường `forbidden_namespaces` (mảng các chuỗi ký tự) vào bảng mô tả và JSON Schema, đảm bảo AI Engine truyền danh sách các namespace hệ thống cần bảo vệ.

### 1.4 Bổ sung thuộc tính `on_fail` vào Verify Policy Schema
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 286 (bảng mô tả) và Dòng 345 (trong properties của `verify_policy` trong JSON Schema).
- **Lý do phải sửa**: Báo cáo nền tảng CDOps (Mục 13 & 20.3) yêu cầu có trường chỉ thị hành động khi xác thực thất bại (`on_fail` như `"ESCALATE"`, `"ROLLBACK"`, v.v.) để điều khiển orchestrator của CDO.
- **Outcome kỳ vọng sau khi sửa**: Thêm `on_fail` (enum: `"ESCALATE"`, `"ROLLBACK"`, `"RETRY"`, `"DONE"`) vào verify_policy trong schema.

### 1.5 Làm rõ điều kiện kích hoạt Fallback Rule-Based của `/v1/decide`
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 558 (trong mục SLA & Error Codes).
- **Lý do phải sửa**: SLA của quyết định `/v1/decide` tăng từ 500ms lên 3000ms do gọi LLM Bedrock. CDO cần biết worst-case latency khi chạy thực tế và các trường hợp cụ thể kích hoạt chế độ rule-based (<500ms) để tối ưu hóa timeout (Q&A Mục 4).
- **Outcome kỳ vọng sau khi sửa**: Liệt kê 4 điều kiện kích hoạt rule-based fallback:
  1. Vượt hạn mức chi phí LLM Bedrock hàng ngày ($50/ngày/tenant).
  2. Bedrock API trả về lỗi 429 (throttling) hoặc 5xx sau khi thử lại.
  3. Thời gian gọi Bedrock API vượt quá 2000 ms.
  4. Mất kết nối mạng tới Bedrock endpoint.
  Khi chạy chế độ fallback này, độ trễ phản hồi đảm bảo `< 500 ms`.

### 1.6 Hướng dẫn phân tích cú pháp (parse) `success_conditions`
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 286 (bảng mô tả) và chèn thêm đoạn đặc tả cú pháp ngay trước JSON schema (khoảng dòng 288).
- **Lý do phải sửa**: Điều kiện trả về dạng `"container_memory_usage_pct < 80"` là một mini DSL. CDO không biết ngữ pháp, các biến được phép lấy từ đâu, và cách xử lý khi không parse được.
- **Outcome kỳ vọng sau khi sửa**:
  - Định nghĩa cấu trúc DSL: `<biến> <toán tử> <giá trị>`.
  - Liệt kê các biến hợp lệ: `pod_ready`, `restart_count_no_increase`, `container_memory_usage_pct`, `service_error_rate`, `service_latency_p95`.
  - Đặc tả nguyên tắc xử lý lỗi: Nếu CDO không parse được điều kiện, bắt buộc áp dụng **FAIL-SAFE (đánh giá THẤT BẠI)** để bảo đảm an toàn hệ thống, sau đó ghi log warning và gửi escalation.

### 1.7 Xác định hành vi của CDO khi nhận cờ `cost_cap_exceeded`
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 287 (bảng mô tả).
- **Lý do phải sửa**: CDO lo ngại response bị reject nếu validate schema strict. Đồng thời cần biết action plan có tiếp tục thực thi được không khi chi phí LLM vượt hạn mức.
- **Outcome kỳ vọng sau khi sửa**: Xác nhận cờ `cost_cap_exceeded` là thuộc tính tùy chọn trong schema. Khi flag này bằng `true`, CDO vẫn thực thi `action_plan` bình thường, đồng thời ghi log warning kiểm toán và bắn alert cảnh báo.

---

## 2. File [deployment-contract.md]

### 2.1 Ngoại lệ thực thi thật cho buổi Demo W12 (Mock mode bypass)
- **Vị trí (Hàng thứ mấy trong file md)**: Chèn vào Mục 2.D (dòng 83-85).
- **Lý do phải sửa**: Mặc dù Mock mode được dùng làm mặc định cho offline testing, phiên hỏi đáp chính thức (Q&A Mục 6) yêu cầu bắt buộc chạy action thật trên Kubernetes sandbox cho 3 kịch bản: Restart Pod, Scale Replicas, và Patch Memory.
- **Outcome kỳ vọng sau khi sửa**: Thêm điều khoản ngoại lệ cho buổi demo W12, quy định rõ CDO Platform thực thi các hành động thật lên hạ tầng Kubernetes đối với các kịch bản lỗi mẫu bắt buộc.

### 2.2 Cung cấp template IAM Role cho ABAC multi-tenant
- **Vị trí (Hàng thứ mấy trong file md)**: Chèn sau mục 3.E (khoảng dòng 192).
- **Lý do phải sửa**: Hợp đồng yêu cầu CDO tạo IAM Role cho AI thực hiện AssumeRole phân lập ABAC, nhưng không nói rõ quyền hạn (permissions) và chính sách tin cậy (trust policy) cụ thể cần thiết lập (Q&A Mục 3 & 6).
- **Outcome kỳ vọng sau khi sửa**: Cung cấp 2 khối JSON mẫu:
  - **Trust Policy**: Cho phép AI Engine Task Role `AssumeRole` và đính kèm session tag `TenantID`.
  - **Permission Policy**: Cấp quyền tối thiểu truy cập các bucket S3 và DynamoDB được phân tách theo tenant (dưới dạng pattern `arn:aws:s3:::tf-3-tenant-[tenant_id]-*`).

### 2.3 Đồng bộ cơ chế Rollback (loại bỏ ArgoCD khỏi ECS)
- **Vị trí (Hàng thứ mấy trong file md)**: Kiểm tra chéo mục 6.C (Dòng 319-322).
- **Lý do phải sửa**: Phiên bản hợp đồng cũ đề cập tới việc dùng ArgoCD để rollback Kubernetes Git SHA cho AI Engine (trong khi AI Engine chạy trên ECS Fargate).
- **Outcome kỳ vọng sau khi sửa**: Xác nhận và làm sạch hoàn toàn các từ khóa liên quan đến ArgoCD trong rollback của AI Engine. Chỉ sử dụng cơ chế AWS CodeDeploy Blue/Green và ECS Deployment Circuit Breaker để rollback task definition.

---

## 3. File [telemetry-contract.md]

### 3.2 Bổ sung các tín hiệu cảnh báo phát sinh (Derived/Enriched Alerts) vào Enum Schema
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 86-96 (enum `signal_name` của Telemetry JSON Schema).
- **Lý do phải sửa**: Báo cáo nền tảng CDOps (Mục 8 & 20.2) quy định CDO sẽ gửi các alert cụ thể như `pod_oom_event`, `service_unhealthy`, và `queue_backlog`. Nếu không khai báo chúng trong enum schema của telemetry contract, các request chứa alert này sẽ bị AI Engine từ chối vì sai schema.
- **Outcome kỳ vọng sau khi sửa**: Đưa `"pod_oom_event"`, `"service_unhealthy"`, và `"queue_backlog"` vào enum của `signal_name` trong schema.

### 3.3 Bổ sung trường `correlation_id` vào Telemetry Schema
- **Vị trí (Hàng thứ mấy trong file md)**: Bảng mô tả trường (dòng 61) và JSON Schema properties (dòng 71-163).
- **Lý do phải sửa**: Báo cáo nền tảng CDOps (Mục 8 & 20.1) yêu cầu payload telemetry gửi đi có chứa trường `correlation_id` ở cấp root để theo vết sự cố. Schema hiện tại đang cấu hình strict `"additionalProperties": false` nên sẽ reject nếu có trường này.
- **Outcome kỳ vọng sau khi sửa**: Thêm `correlation_id` (kiểu string, định dạng uuid) vào bảng mô tả và schema properties ở cấp root để CDO có thể truyền vết sự cố.

### 3.4 Làm rõ kênh truyền tải telemetry (SQS vs HTTP POST API)
- **Vị trí (Hàng thứ mấy trong file md)**: Dòng 280 (bảng mô tả) và chèn bổ sung đoạn làm rõ kênh truyền.
- **Lý do phải sửa**: CDO thiết kế SQS làm telemetry buffer. Tuy nhiên hợp đồng telemetry mới lại xóa bỏ SQS và ghi emit point là Prometheus/OTel, khiến CDO không rõ kênh truyền chính thức giữa CDO và AI Engine là gì (Q&A Mục 7).
- **Outcome kỳ vọng sau khi sửa**:
  - Xác nhận AI Engine không kết nối trực tiếp đến SQS hay pull Prometheus.
  - CDO đóng vai trò là bên chủ động gửi (push) dữ liệu telemetry qua các API HTTP POST `/v1/detect` (`telemetry_window`) và `/v1/verify` (`post_telemetry_window`).
  - Việc CDO sử dụng SQS làm buffer nội bộ để tránh mất dữ liệu hoàn toàn được chấp nhận và nằm ngoài phạm vi giao tiếp trực tiếp của hợp đồng này.
