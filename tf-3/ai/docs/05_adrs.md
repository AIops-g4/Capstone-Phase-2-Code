# Architecture Decision Records - Generic Multi-Tenant Self-Heal Platform (AIOps TF3)

Doc owner: AI Team
Status: Active Log
Word count: 1200 words

Tài liệu này ghi nhận lại các quyết định kiến trúc quan trọng trong quá trình thiết kế và phát triển hệ thống tự chữa lành AIOps TF3, bao gồm bối cảnh, các lựa chọn thay thế được xem xét, quyết định cuối cùng và các hệ quả đi kèm.

---

## ADR-001 - Phân tách Vai trò Quyết định và Thực thi (Brain vs Hands)

- **Status**: Accepted
- **Context**: Khi xây dựng một giải pháp tự chữa lành tự động cho cụm Kubernetes (EKS), có một mối lo ngại lớn về bảo mật hạ tầng và rủi ro vận hành. Nếu AI Engine trực tiếp thực thi các hành động sửa lỗi lên cụm, nó sẽ yêu cầu quyền hạn rất cao (`eks:*` hoặc quyền admin cụm) và tiếp xúc trực tiếp với API server của Kubernetes, tạo ra một bề mặt tấn công cực kỳ nguy hiểm nếu hệ thống AI bị xâm nhập.
- **Decision**: Chốt phân tách hoàn toàn trách nhiệm giữa AI Engine và CDOps Platform. AI Engine chỉ đóng vai trò là bộ não phân tích dữ liệu telemetry và trả về kế hoạch hành động tối ưu dưới dạng dữ liệu cấu trúc (Brain). Quyền sửa đổi hạ tầng và gọi Kubernetes API thuộc về CDOps Platform (Hands). CDOps Platform có trách nhiệm kiểm tra tính an toàn, xác thực vùng ảnh hưởng (Blast Radius) của kế hoạch trước khi thực thi.
- **Consequence**:
  * Pro: Tối đa hóa tính an toàn bảo mật. IAM Role gắn qua IRSA (IAM Roles for Service Accounts) của AI Engine Pod trên EKS hoàn toàn không có quyền can thiệp Kubernetes API, tuân thủ nghiêm ngặt nguyên tắc đặc quyền tối thiểu (Least Privilege). CDOps Platform đóng vai trò chốt chặn an toàn cuối cùng.
  * Pro: Dễ dàng kiểm toán độc lập các hành động can thiệp hạ tầng.
  * Trade-off: Tăng thêm độ trễ truyền thông giữa hai hệ thống và yêu cầu định nghĩa giao thức API cực kỳ chặt chẽ ở hai đầu.
- **Alternatives considered**:
  * Option A (AI Engine trực tiếp thực thi): Bị bác bỏ vì vi phạm nghiêm trọng các quy định về an toàn thông tin của SOC2 và gây rủi ro mất an toàn cho cụm máy chủ sản xuất.

---

## ADR-002 - Lựa chọn Mô hình Ngôn ngữ Lớn (LLM) và Nhà cung cấp

- **Status**: Accepted
- **Context**: Hệ thống tự chữa lành cần một mô hình AI có khả năng phân tích logic tốt để đọc hiểu logs, traces hệ thống, đối chiếu với danh mục Runbook và sinh ra cấu hình JSON hợp lệ. Đồng thời, mô hình phải có độ trễ cực thấp và chi phí vận hành tối ưu để phù hợp với tần suất gọi lớn của chu trình tự chữa lành.
- **Decision**: Lựa chọn mô hình Claude 3 Haiku (`anthropic.claude-3-haiku-20240307-v1:0`) được cung cấp dưới dạng dịch vụ serverless quản trị hoàn toàn trên AWS Bedrock làm baseline cho môi trường sản xuất. Tuy nhiên, để đảm bảo tính linh hoạt tối đa trong kiểm thử và phát triển, codebase của AI Engine được thiết kế hỗ trợ cấu hình đa mô hình/đa provider (OpenAI `gpt-4o`, Anthropic direct API, và AWS Bedrock client mặc định là Claude 3.5 Sonnet) có thể tinh chỉnh thông qua biến môi trường `LLM_PROVIDER` và `LLM_MODEL`.
- **Consequence**:
  * Pro: Tốc độ phản hồi cực nhanh khi chọn Haiku (p99 của API decide dưới 3 giây).
  * Pro: Codebase có tính tương thích cao, dễ dàng thử nghiệm nhiều LLM khác nhau mà không cần sửa đổi mã nguồn.
  * Pro: Tích hợp sẵn sàng với các dịch vụ bảo mật của AWS (KMS, CloudTrail, Bedrock Guardrails).
  * Trade-off: Việc quản lý cấu hình các provider khác nhau tăng độ phức tạp nhỏ cho file config ứng dụng.
- **Alternatives considered**:
  * Option A (Chỉ hỗ trợ cứng Claude 3 Haiku): Bị bác bỏ vì cản trở việc kiểm thử và tối ưu hoá chất lượng lập kế hoạch bằng các mô hình mạnh hơn (như Claude 3.5 Sonnet) trong tương lai.

---

## ADR-003 - Cơ chế Quản trị Chi phí LLM và Chế độ Dự phòng Rule-Based

- **Status**: Accepted
- **Context**: Sử dụng LLM trong chu trình tự chữa lành tự động tiềm ẩn rủi ro bùng nổ chi phí (runaway cost) ngoài kiểm soát nếu hệ thống rơi vào vòng lặp lỗi vô hạn (looping) hoặc bị tấn công từ chối dịch vụ (DoS) bằng cách gửi liên tục các prompt telemetry giả mạo.
- **Decision**: Thiết lập ngân sách Bedrock $50/ngày cho mỗi Tenant. Cơ chế giới hạn chi phí hàng ngày được quản lý tại platform layer / API Gateway và trả về flag `cost_cap_exceeded: False` mặc định trong AI Engine container. Đồng thời xây dựng cơ chế tự động chuyển đổi sang chế độ dự phòng Rule-Based (chạy cây quyết định tĩnh nội bộ, không gọi LLM Bedrock) khi xảy ra một trong các điều kiện: vượt hạn mức chi phí hàng ngày (được báo từ platform), AWS Bedrock bị lỗi kết nối/rate limit/timeout (được bẫy qua try-catch ngoại lệ trực tiếp xung quanh client calls), hoặc lỗi phân tích cấu trúc phản hồi.
- **Consequence**:
  * Pro: Đảm bảo an toàn tài chính tuyệt đối cho các Tenant, ngăn chặn hoàn toàn rủi ro bùng nổ chi phí ngoài ý muốn.
  * Pro: Tăng cường tính sẵn sàng cao của hệ thống. Kế hoạch hành động chữa lành vẫn được sinh ra ngay cả khi dịch vụ LLM Bedrock bị sập hoàn toàn nhờ khối try-catch an toàn trong code.
  * Pro: Chế độ dự phòng Rule-Based có độ trễ cực thấp (dưới 500ms).
  * Trade-off: Cần đồng bộ trạng thái cost cap giữa platform layer và AI Engine.
- **Alternatives considered**:
  * Option A (Không có try-catch fallback tĩnh trong container): Bị bác bỏ vì nếu LLM gặp sự cố, toàn bộ chu trình tự chữa lành sẽ bị ngắt quãng hoàn toàn.

---

## ADR-004 - Cơ chế Cô lập Dữ liệu Đa thuê bao qua AWS STS và Session Tags

- **Status**: Accepted
- **Context**: AI Engine được triển khai dưới dạng một dịch vụ backend dùng chung (shared service) phục vụ đồng thời nhiều Tenants (khách hàng) khác nhau. Yêu cầu đặt ra là phải đảm bảo tính cô lập và bảo mật dữ liệu tuyệt đối giữa các Tenants, không để xảy ra hiện tượng rò rỉ dữ liệu chéo (cross-tenant data bleed) trên các tài nguyên lưu trữ chung như S3 hay DynamoDB.
- **Decision**: Thiết lập cơ chế kiểm soát truy cập dựa trên thuộc tính (ABAC) thông qua AWS STS AssumeRole động. Trong thiết kế hệ thống, CDOps Platform / API Gateway chịu trách nhiệm thực hiện cuộc gọi `AssumeRole` đến IAM Role dành riêng cho từng tenant (`arn:aws:iam::*:role/tf-3-tenant-[tenant_id]-role`) kết hợp với Session Tags `"TenantID": "[tenant_id]"` trước khi định tuyến cuộc gọi vào AI Engine. AI Engine container thực hiện xác thực và phân lập logic dữ liệu trong memory request theo mã tenant truyền vào, đồng thời trả về mock status "connected" đối với các check tích hợp.
- **Consequence**:
  * Pro: Đảm bảo cô lập dữ liệu tuyệt đối ở mức hạ tầng AWS, hạn chế bề mặt tấn công của container AI Engine.
  * Pro: Đáp ứng hoàn hảo các tiêu chí khắt khe về bảo mật dữ liệu đa thuê bao của chứng nhận SOC2 Type II.
  * Trade-off: Tách biệt logic quản lý IAM Role ra khỏi ứng dụng, đòi hỏi platform layer/gateway cấu hình đồng bộ.
- **Alternatives considered**:
  * Option A (AI Engine trực tiếp gọi STS AssumeRole ở mức logic code ứng dụng): Bị bác bỏ vì yêu cầu container AI Engine phải giữ credential có quyền rất rộng để assume các role khác, vi phạm nguyên lý Least Privilege.

---

## ADR-005 - Sử dụng Khóa Idempotency Phân tán để Chống Trùng lặp Lệnh

- **Status**: Accepted
- **Context**: Trong môi trường phân tán hoặc khi xảy ra sự cố nghẽn mạng, CDOps Platform có thể gửi yêu cầu gọi API `/v1/decide` nhiều lần cho cùng một sự cố do cơ chế tự động thử lại (Retry). Nếu không có cơ chế chống trùng lặp, hạ tầng có thể thực thi một hành động chữa lành nhiều lần liên tiếp (ví dụ: restart pod 2 lần liên tục), gây mất ổn định nghiêm trọng hơn và lãng phí tài nguyên.
- **Decision**: Áp dụng cơ chế khóa phân tán Idempotency Lock sử dụng Amazon DynamoDB kết hợp với tính năng ghi có điều kiện (Conditional Writes). Để tối giản logic trong container AI Engine, API Gateway hoặc platform layer điều phối chịu trách nhiệm thực hiện ghi khóa `Idempotency-Key` vào DynamoDB với điều kiện chưa tồn tại (TTL 5 phút). AI Engine thực hiện parse, validate tính hợp lệ của key (UUID v4) ở mức contract API đầu vào và tích hợp mock status để trả về response chuẩn xác.
- **Consequence**:
  * Pro: Đảm bảo nguyên lý chống trùng lặp Exactly-once execution ở lớp Gateway/Platform trước khi xử lý logic LLM phức tạp.
  * Pro: Tránh quá tải cho AI Engine khi có bão retry.
  * Trade-off: AI Engine phụ thuộc vào platform layer để khoá conditional write thực sự được thực thi.
- **Alternatives considered**:
  * Option A (Tự triển khai thư viện boto3 DynamoDB Client trong AI Engine): Bị bác bỏ vì tăng gánh nặng tích hợp và bảo mật IAM của container trong giai đoạn này.
