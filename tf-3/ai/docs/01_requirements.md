# Requirements - Generic Multi-Tenant Self-Heal Platform (AIOps TF3)

Doc owner: AI Team Lead
Status: Final
Word count: 1150 words

## 1. Khách hàng nói

Đội ngũ vận hành hạ tầng CDO Platform cần một giải pháp tự động hóa toàn bộ chu trình phát hiện và khắc phục sự cố (Self-Healing Loop) nhằm giảm thiểu thời gian phục hồi dịch vụ (RTO) xuống dưới 5 phút cho các ứng dụng chạy trên nền tảng microservices. Hệ thống tự chữa lành này phải hoạt động độc lập với quyền kiểm soát hạ tầng trực tiếp của AI, trong đó AI đóng vai trò làm bộ não phân tích và đưa ra kế hoạch hành động, còn CDO Platform đóng vai trò làm bàn tay thực thi để đảm bảo an toàn tuyệt đối. Đồng thời, giải pháp phải tuân thủ nghiêm ngặt chứng nhận SOC2 về bảo vệ dữ liệu nhạy cảm, có cơ chế kiểm soát chi phí gọi mô hình ngôn ngữ lớn (LLM), chống trùng lặp xử lý khi có bão yêu cầu thử lại (retry storm), và hỗ trợ cô lập hoàn toàn giữa các khách hàng khác nhau trong mô hình đa thuê bao (multi-tenant).

## 2. Outcomes mong muốn

Dựa trên yêu cầu của khách hàng, hệ thống AIOps TF3 hướng đến các kết quả cụ thể sau:

* Phát hiện bất thường tự động: Tự động tiếp nhận dòng dữ liệu giám sát (telemetry) liên tục từ các microservices, thực hiện phân tích thời gian thực để phát hiện các dấu hiệu lỗi về tài nguyên hạ tầng, mạng và logic ứng dụng.
* Lập kế hoạch tự chữa lành tối ưu: Khi phát hiện bất thường, đối chiếu ngữ cảnh lỗi với thư viện Runbook để sinh ra kịch bản khắc phục tuần tự (Action Plan) phù hợp. Kế hoạch này phải phân loại rõ ràng giữa hai luồng xử lý: luồng khẩn cấp (urgent - vá trực tiếp lên Kubernetes API) và luồng đồng bộ cấu hình (deferred - GitOps thông qua commit hoặc Pull Request trên Git repository) nhằm tránh hiện tượng lệch trạng thái (state drift) của hạ tầng.
* Xác thực kết quả phục hồi: Sau khi CDO Platform thực hiện hành động khắc phục, hệ thống phải phân tích dữ liệu telemetry sau sự kiện để xác nhận dịch vụ đã phục hồi hoàn toàn, phát hiện các lỗi suy thoái (regression) phát sinh và đưa ra chỉ dẫn hành động tiếp theo (hoàn thành, thử lại, hoàn tác hoặc leo thang lên kỹ sư trực ban).
* Phân lập đa thuê bao và bảo mật tuyệt đối: Đảm bảo dữ liệu telemetry và ngữ cảnh của từng tenant được cô lập hoàn toàn ở mức logic và quyền truy cập tài nguyên (S3, DynamoDB), không để xảy ra hiện tượng rò rỉ dữ liệu chéo.
* Kiểm soát chi phí và rủi ro vận hành: Thiết lập các chốt chặn an toàn về chi phí gọi LLM Bedrock và giới hạn vùng ảnh hưởng (Blast Radius) của các hành động tự động để bảo vệ tính toàn vẹn của cụm máy chủ.

## 3. Success criteria (measurable)

Các chỉ số thành công của dự án được đo lường cụ thể thông qua bảng sau:

| Metric | Target | How to measure |
|---|---|---|
| p99 Latency - Detect | Duoi 300 ms | Đo lường thời gian từ lúc nhận request POST /v1/detect đến khi trả về response |
| p99 Latency - Decide (LLM) | Duoi 3000 ms | Đo lường thời gian xử lý của POST /v1/decide khi gọi AWS Bedrock |
| p99 Latency - Decide (Fallback) | Duoi 500 ms | Đo lường thời gian xử lý của POST /v1/decide ở chế độ rule-based |
| p99 Latency - Verify | Duoi 500 ms | Đo lường thời gian từ lúc nhận request POST /v1/verify đến khi trả về response |
| Service Availability | Dat 99.9% | Tỷ lệ thời gian hoạt động ổn định của các API endpoints của AI Engine |
| AI Detection Precision | Tren hoặc bang 0.85 | Tỷ lệ phát hiện đúng bất thường trên tổng số trường hợp dự đoán có bất thường |
| AI Detection Recall | Tren hoặc bang 0.80 | Tỷ lệ phát hiện được bất thường trên tổng số trường hợp bất thường thực tế |
| F1-Score | Tren hoặc bang 0.82 | Trung bình điều hòa giữa Precision và Recall của mô hình phát hiện lỗi |
| Recovery Time Objective (RTO) | Duoi 5 phut | Tổng thời gian phát hiện, lập kế hoạch, thực thi và xác thực tự chữa lành |

## 4. Constraints

Dự án tự chữa lành AIOps TF3 phải tuân thủ các ràng buộc kỹ thuật và vận hành sau:

* Budget: Giới hạn ngân sách thử nghiệm dịch vụ AWS Bedrock và hạ tầng EKS sandbox.
* Timeline: Dự án phải hoàn thành xây dựng, kiểm thử tích hợp và đóng băng mã nguồn theo đúng lịch trình quy định của Phase 2.
* Tooling: Triển khai hoàn toàn trên hạ tầng đám mây AWS (EKS, DynamoDB, S3, Secrets Manager, Bedrock), không sử dụng các giải pháp multi-cloud hoặc các dịch vụ LLM bên ngoài AWS trong môi trường sản xuất.
* Compliance (SOC2 Type II):
  * Dữ liệu telemetry gửi sang AI Engine tuyệt đối không được chứa thông tin nhận dạng cá nhân (PII) như email, số điện thoại, mật khẩu, hoặc connection string.
  * Nhật ký kiểm toán hoạt động (Audit Trail) phải được lưu trữ bất biến (WORM) trên S3 với thời gian giữ tối thiểu 90 ngày.
  * Phân tách quyền hạn chặt chẽ (Least Privilege) ở mức IAM Roles thông qua IRSA (IAM Roles for Service Accounts) trên EKS.

## 5. Out of scope

Các hạng mục sau đây nằm ngoài phạm vi thực hiện của dự án AIOps TF3 để tránh phình to phạm vi (scope creep):

* Multi-cloud and Cross-region DR: Hệ thống không hỗ trợ chạy dự phòng đa đám mây hoặc phân tán dữ liệu, xử lý thảm họa trên nhiều vùng địa lý (multi-region) của AWS.
* Self-training / Custom Fine-tuning: AI Engine sử dụng trực tiếp các mô hình nền tảng (Foundation Models) có sẵn trên AWS Bedrock thông qua prompt engineering và đối chiếu runbook, không thực hiện huấn luyện lại hoặc tinh chỉnh (fine-tune) trọng số mô hình riêng.
* Direct Infrastructure Execution: AI Engine tuyệt đối không trực tiếp gọi Kubernetes API để thực thi hành động sửa lỗi lên cụm EKS. Quyền thực thi và kiểm tra an toàn hạ tầng thuộc về CDO Platform.
* Automated Code Bug Fixing: Hệ thống không tự động sửa lỗi logic trong mã nguồn của ứng dụng (bug fixing ở mức viết lại code), chỉ thực hiện các hành động cấu hình, tài nguyên và vòng đời container (như restart, scale, patch memory, rotate secret).

## 6. Non-functional requirements

* SLO Platform: Độ trễ phản hồi p99 của API detect dưới 300ms, decide dưới 3000ms (khi gọi LLM) và verify dưới 500ms. Khả năng sẵn sàng hệ thống đạt 99.9%.
* Multi-tenant Scale: Hệ thống thiết kế dạng shared backend hỗ trợ đồng thời ít nhất 2 tenants độc lập (cdo-1 và cdo-2) trên cùng một EKS Deployment mà không bị lẫn lộn dữ liệu nhờ cơ chế định tuyến theo header `X-Tenant-Id` và AssumeRole động theo Tenant ID.
* Security Baseline:
  * Toàn bộ kết nối API nội bộ trong cụm EKS sử dụng Local Trust (mTLS tùy chọn) kết hợp Kubernetes Network Policies.
  * Mọi secret, credential và API key phải được lưu trữ trong AWS Secrets Manager và cấu hình chính sách xoay vòng tự động (rotation policy).
  * Sử dụng DynamoDB conditional writes để thiết lập khóa chống trùng lặp (Idempotency Lock) với thời gian sống (TTL) là 5 phút cho mỗi Idempotency-Key.
  * Ghi nhật ký kiểm toán bất biến lên S3 Object Lock ở Compliance Mode trong vòng 90 ngày.
* Cost Target: Thiết lập hạn mức chi phí (Cost Cap) tối đa cho dịch vụ Bedrock LLM là $50/ngày trên mỗi Tenant để tránh rủi ro phát sinh chi phí đột biến khi hệ thống rơi vào vòng lặp lỗi vô hạn.

## 7. Open questions

* Q1: Trách nhiệm lọc dữ liệu nhạy cảm (PII scrubbing) trong telemetry và stack trace thuộc về bên nào?
  * Resolved: Trách nhiệm lọc thuộc về CDO Platform ở lớp thu thập dữ liệu (Ingestion Layer) trước khi đẩy telemetry sang AI Engine qua API. AI Engine sẽ kiểm tra và từ chối (HTTP 400 Bad Request) các payload không vượt qua schema validation.
* Q2: Cơ chế xử lý khi LLM Bedrock bị quá tải tần suất gọi (Rate Limiting - HTTP 429) hoặc gặp sự cố dịch vụ (HTTP 5xx)?
  * Resolved: AI Engine sẽ tự động kích hoạt cơ chế ngắt mạch (Circuit Breaker) và chuyển sang chế độ dự phòng Rule-Based (chạy cây quyết định tĩnh nội bộ) để đảm bảo trả về kế hoạch hành động an toàn cho CDO Platform với độ trễ phản hồi dưới 500ms.
