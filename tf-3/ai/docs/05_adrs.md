# Architecture Decision Records - Generic Multi-Tenant Self-Heal Platform (AIOps TF3)

Doc owner: AI Team
Status: Active Log
Word count: 1200 words

Tài liệu này ghi nhận lại các quyết định kiến trúc quan trọng trong quá trình thiết kế và phát triển hệ thống tự chữa lành AIOps TF3, bao gồm bối cảnh, các lựa chọn thay thế được xem xét, quyết định cuối cùng và các hệ quả đi kèm.

---

## ADR-001 - Phân tách Vai trò Quyết định và Thực thi (Brain vs Hands)

* Status: Accepted
* Date: 2026-06-20
* Context: Khi xây dựng một giải pháp tự chữa lành tự động cho cụm Kubernetes (EKS), có một mối lo ngại lớn về bảo mật hạ tầng và rủi ro vận hành. Nếu AI Engine trực tiếp thực thi các hành động sửa lỗi lên cụm, nó sẽ yêu cầu quyền hạn rất cao (`eks:*` hoặc quyền admin cụm) và tiếp xúc trực tiếp với API server của Kubernetes, tạo ra một bề mặt tấn công cực kỳ nguy hiểm nếu hệ thống AI bị xâm nhập.
* Decision: Chốt phân tách hoàn toàn trách nhiệm giữa AI Engine và CDOps Platform. AI Engine chỉ đóng vai trò là bộ não phân tích dữ liệu telemetry và trả về kế hoạch hành động tối ưu dưới dạng dữ liệu cấu trúc (Brain). Quyền sửa đổi hạ tầng và gọi Kubernetes API thuộc về CDOps Platform (Hands). CDOps Platform có trách nhiệm kiểm tra tính an toàn, xác thực vùng ảnh hưởng (Blast Radius) của kế hoạch trước khi thực thi.
* Consequence:
  * Pro: Tối đa hóa tính an toàn bảo mật. IAM Role gắn qua IRSA (IAM Roles for Service Accounts) của AI Engine Pod trên EKS hoàn toàn không có quyền can thiệp Kubernetes API, tuân thủ nghiêm ngặt nguyên tắc đặc quyền tối thiểu (Least Privilege). CDOps Platform đóng vai trò chốt chặn an toàn cuối cùng.
  * Pro: Dễ dàng kiểm toán độc lập các hành động can thiệp hạ tầng.
  * Trade-off: Tăng thêm độ trễ truyền thông giữa hai hệ thống và yêu cầu định nghĩa giao thức API cực kỳ chặt chẽ ở hai đầu.
* Alternatives considered:
  * Option A (AI Engine trực tiếp thực thi): Bị bác bỏ vì vi phạm nghiêm trọng các quy định về an toàn thông tin của SOC2 và gây rủi ro mất an toàn cho cụm máy chủ sản xuất.

---

## ADR-002 - Lựa chọn Mô hình Ngôn ngữ Lớn (LLM) và Nhà cung cấp

* Status: Accepted
* Date: 2026-06-21
* Context: Hệ thống tự chữa lành cần một mô hình AI có khả năng phân tích logic tốt để đọc hiểu logs, traces hệ thống, đối chiếu với danh mục Runbook và sinh ra cấu hình JSON hợp lệ. Đồng thời, mô hình phải có độ trễ cực thấp và chi phí vận hành tối ưu để phù hợp với tần suất gọi lớn của chu trình tự chữa lành.
* Decision: Lựa chọn mô hình Claude 3 Haiku (`anthropic.claude-3-haiku-20240307-v1:0`) được cung cấp dưới dạng dịch vụ serverless quản trị hoàn toàn trên AWS Bedrock.
* Consequence:
  * Pro: Tốc độ phản hồi cực nhanh (p99 của API decide dưới 3 giây), nhanh hơn nhiều so với Claude 3 Sonnet hay GPT-4.
  * Pro: Chi phí token cực kỳ thấp ($0.00025/1k input tokens), giúp duy trì chi phí vận hành hệ thống ở mức tối ưu.
  * Pro: Tích hợp sẵn sàng với các dịch vụ bảo mật của AWS (IAM, KMS, CloudTrail, Bedrock Guardrails).
  * Trade-off: Khả năng suy luận logic đối với các kịch bản lỗi cực kỳ phức tạp hoặc đa dịch vụ có phần hạn chế hơn so với các mô hình lớn (như Claude Sonnet).
* Alternatives considered:
  * Option A (Claude 3.5 Sonnet): Bị bác bỏ vì độ trễ phản hồi cao (thường từ 4 đến 8 giây) và chi phí token đắt gấp 12 lần so với Haiku, không phù hợp với yêu cầu RTO khắt khe của hệ thống tự chữa lành.
  * Option B (Mô hình tự host trên EC2/SageMaker): Bị bác bỏ vì gánh nặng vận hành hạ tầng lớn, chi phí duy trì phần cứng GPU đắt đỏ và không đáp ứng được tiến độ phát triển của Phase 2.

---

## ADR-003 - Cơ chế Quản trị Chi phí LLM và Chế độ Dự phòng Rule-Based

* Status: Accepted
* Date: 2026-06-22
* Context: Sử dụng LLM trong chu trình tự chữa lành tự động tiềm ẩn rủi ro bùng nổ chi phí (runaway cost) ngoài kiểm soát nếu hệ thống rơi vào vòng lặp lỗi vô hạn (looping) hoặc bị tấn công từ chối dịch vụ (DoS) bằng cách gửi liên tục các prompt telemetry giả mạo.
* Decision: Thiết lập giới hạn cứng về chi phí gọi Bedrock ở mức $50/ngày cho mỗi Tenant. Đồng thời xây dựng cơ chế tự động chuyển đổi sang chế độ dự phòng Rule-Based (chạy cây quyết định tĩnh nội bộ, không gọi LLM Bedrock) khi xảy ra một trong các điều kiện: vượt hạn mức chi phí hàng ngày, AWS Bedrock bị rate limit (HTTP 429), Bedrock gặp sự cố kết nối (HTTP 5xx / Timeout) hoặc lỗi phân tích cấu trúc phản hồi.
* Consequence:
  * Pro: Đảm bảo an toàn tài chính tuyệt đối cho các Tenant, ngăn chặn hoàn toàn rủi ro bùng nổ chi phí ngoài ý muốn.
  * Pro: Tăng cường tính sẵn sàng cao của hệ thống. Kế hoạch hành động chữa lành vẫn được sinh ra ngay cả khi dịch vụ LLM Bedrock bị sập hoàn toàn.
  * Pro: Chế độ dự phòng Rule-Based có độ trễ cực thấp (dưới 500ms).
  * Trade-off: Kế hoạch hành động sinh ra từ Rule-Based tĩnh sẽ kém linh hoạt và không có khả năng phân tích ngữ cảnh thông minh như LLM.
* Alternatives considered:
  * Option A (Không giới hạn chi phí, chỉ cảnh báo): Bị bác bỏ vì không đáp ứng yêu cầu quản trị rủi ro tài chính của doanh nghiệp.

---

## ADR-004 - Cơ chế Cô lập Dữ liệu Đa thuê bao qua AWS STS và Session Tags

* Status: Accepted
* Date: 2026-06-23
* Context: AI Engine được triển khai dưới dạng một dịch vụ backend dùng chung (shared service) phục vụ đồng thời nhiều Tenants (khách hàng) khác nhau. Yêu cầu đặt ra là phải đảm bảo tính cô lập và bảo mật dữ liệu tuyệt đối giữa các Tenants, không để xảy ra hiện tượng rò rỉ dữ liệu chéo (cross-tenant data bleed) trên các tài nguyên lưu trữ chung như S3 hay DynamoDB.
* Decision: Thiết lập cơ chế kiểm soát truy cập dựa trên thuộc tính (ABAC) thông qua AWS STS AssumeRole động. Khi tiếp nhận request, AI Engine sẽ dựa vào `tenant_id` trong HTTP header để giả lập một IAM Role chuyên biệt của tenant đó (`arn:aws:iam::*:role/tf-3-tenant-[tenant_id]-role`) và bắt buộc đính kèm thẻ phiên làm việc (Session Tag) `"TenantID": "[tenant_id]"`. Các tài nguyên S3 và DynamoDB cấu hình chính sách Resource-based Policy chỉ cho phép truy cập nếu giá trị tag này trùng khớp với nhãn sở hữu tài nguyên.
* Consequence:
  * Pro: Đảm bảo cô lập dữ liệu tuyệt đối ở mức hạ tầng AWS. Một tenant không thể truy cập dữ liệu của tenant khác ngay cả khi có lỗi logic trong mã nguồn của AI Engine.
  * Pro: Đáp ứng hoàn hảo các tiêu chí khắt khe về bảo mật dữ liệu đa thuê bao của chứng nhận SOC2 Type II.
  * Trade-off: Tăng thêm độ trễ nhỏ (khoảng 50-100ms) cho mỗi yêu cầu gọi API do phải thực hiện cuộc gọi AssumeRole tới AWS STS.
* Alternatives considered:
  * Option A (Cô lập ở mức logic ứng dụng - Application-level isolation): Bị bác bỏ vì rủi ro rò rỉ dữ liệu chéo rất cao nếu xảy ra lỗi lập trình (code bug) trong ứng dụng AI Engine.

---

## ADR-005 - Sử dụng Khóa Idempotency Phân tán để Chống Trùng lặp Lệnh

* Status: Accepted
* Date: 2026-06-24
* Context: Trong môi trường phân tán hoặc khi xảy ra sự cố nghẽn mạng, CDOps Platform có thể gửi yêu cầu gọi API `/v1/decide` nhiều lần cho cùng một sự cố do cơ chế tự động thử lại (Retry). Nếu không có cơ chế chống trùng lặp, hạ tầng có thể thực thi một hành động chữa lành nhiều lần liên tiếp (ví dụ: restart pod 2 lần liên tục), gây mất ổn định nghiêm trọng hơn và lãng phí tài nguyên.
* Decision: Áp dụng cơ chế khóa phân tán Idempotency Lock sử dụng Amazon DynamoDB kết hợp với tính năng ghi có điều kiện (Conditional Writes). Mỗi quyết định lập kế hoạch chữa lành bắt buộc phải đi kèm một khóa `Idempotency-Key` (UUID v4) duy nhất. Trước khi xử lý, AI Engine thực hiện ghi khóa này vào bảng DynamoDB với điều kiện khóa chưa tồn tại và thiết lập thời gian sống (TTL) là 5 phút. Nếu khóa đã tồn tại, hệ thống lập tức từ chối yêu cầu trùng lặp và trả về mã lỗi HTTP `409 Conflict`.
* Consequence:
  * Pro: Chống trùng lặp tuyệt đối, bảo đảm nguyên tắc mọi quyết định chữa lành chỉ được thực thi duy nhất một lần (Exactly-once execution).
  * Pro: DynamoDB Conditional Writes mang lại độ trễ cực thấp (dưới 10ms) và khả năng chịu tải cao.
  * Trade-off: Yêu cầu CDOps Platform phải quản lý và truyền tải chính xác `Idempotency-Key` xuyên suốt chu trình xử lý lỗi.
* Alternatives considered:
  * Option A (Không dùng khóa, dựa vào CDOps tự kiểm soát): Bị bác bỏ vì không đảm bảo tính toàn vẹn hệ thống và dễ xảy ra xung đột trạng thái khi có độ trễ truyền tin mạng.
