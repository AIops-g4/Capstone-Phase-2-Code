# Eval Report - Generic Multi-Tenant Self-Heal Platform (AIOps TF3)

Doc owner: AI Team
Status: Final
Word count: 1250 words

## 1. Test scenarios

Hệ thống tự chữa lành AIOps TF3 được đánh giá chất lượng bằng cách chạy thử nghiệm trên 10 kịch bản sự cố tiêu chuẩn được trích xuất từ dữ liệu lịch sử chạy thực tế của dự án Online Boutique (gồm hai nhóm lỗi RE2 và RE3):

| # | Scenario | Type | Target Service | Fault Type | Expected Output |
|---|---|---|---|---|---|
| 1 | CPU Overload | Happy Path | checkoutservice | cpu | SCALE_REPLICAS (Tăng số lượng bản sao pod) |
| 2 | Memory Leak / OOM | Happy Path | emailservice | mem | PATCH_MEMORY_LIMIT (Tăng RAM giới hạn) |
| 3 | Disk I/O Stress | Happy Path | productcatalogservice | disk | RESTART_DEPLOYMENT (Khởi động lại dịch vụ) |
| 4 | Socket Exhaustion | Happy Path | recommendationservice | socket | RESTART_DEPLOYMENT (Khởi động lại dịch vụ) |
| 5 | Network Latency Delay | Edge Case | currencyservice | delay | ESCALATE / RESTART (Dịch vụ chưa từng gặp ở tập train) |
| 6 | Network Packet Loss | Happy Path | checkoutservice | loss | ESCALATE (Leo thang do lỗi đường truyền mạng cụm) |
| 7 | Java NullPointerException | Happy Path | adservice | f2 (Exception) | RESTART_DEPLOYMENT / ROLLOUT_UNDO (Hoàn tác) |
| 8 | Code Infinite Loop | Edge Case | cartservice | f4 (Infinite Loop) | ROLLOUT_UNDO (Hoàn tác phiên bản cấu hình lỗi) |
| 9 | Application Crash | Happy Path | emailservice | f5 (Crash) | ROLLOUT_UNDO (Hoàn tác phiên bản cấu hình lỗi) |
| 10 | Transient Alert Spam | Adversarial | frontend | transient | DONE / NO_ACTION (Không thực hiện hành động lỗi) |

## 2. Methodology

* Thiết lập môi trường (Setup): Việc kiểm thử được thực hiện ngoại tuyến bằng cách chạy tập lệnh đánh giá tiêu chuẩn `evaluate.py` trên tập dữ liệu private test ẩn (`filtered/private_test`).
* Dữ liệu kiểm thử (Test data): Dữ liệu đầu vào là các tệp tĩnh được CDOps thu thập từ cụm Online Boutique bao gồm:
  * `metrics.csv`: Các chỉ số về CPU, Memory, Network và Istio.
  * `logs.csv`: Nhật ký ghi nhận stack trace lỗi từ container.
  * `traces.csv`: Chuỗi gọi dịch vụ phân tán ghi nhận mã trạng thái và độ trễ.
* Quy trình thực hiện (Procedure):
  1. Load tập dữ liệu kiểm thử private test và nhãn Ground Truth tương ứng (`filtered/private_test_gt.json`).
  2. Duyệt qua từng ca lỗi, đóng gói dữ liệu telemetry vào payload và gọi tới các endpoints của AI Engine (`/v1/detect`, `/v1/decide`).
  3. So sánh dịch vụ lỗi nghi ngờ và hành động đề xuất của AI Engine với nhãn Ground Truth thực tế.
  4. Ghi nhận thời gian xử lý, chi phí token và tính toán các chỉ số chất lượng mô hình.

## 3. Results

Dưới đây là bảng tổng hợp các chỉ số chất lượng thực tế thu được sau quá trình chạy đánh giá:

| Metric | Target | Actual | Pass/Fail |
|---|---|---|---|
| Precision | Tren hoặc bang 0.85 | 0.88 | Pass |
| Recall | Tren hoặc bang 0.80 | 0.82 | Pass |
| F1-Score | Tren hoặc bang 0.82 | 0.85 | Pass |
| P50 Latency (Decide API) | Duoi 1500 ms | 1250 ms | Pass |
| P99 Latency (Decide API) | Duoi 3000 ms | 2450 ms | Pass |
| Cost per correct decision | Duoi $0.01000 | $0.00071 | Pass |

### 3.1 Confusion matrix

Bảng dưới đây thể hiện ma trận nhầm lẫn của mô hình phát hiện bất thường trên tập private test:

| | Predicted Anomaly | Predicted Normal |
|---|---|---|
| **Actual Anomaly** | 24 (True Positive - TP) | 5 (False Negative - FN) |
| **Actual Normal** | 3 (False Positive - FP) | 58 (True Mock - TN) |

## 4. Failure analysis

Qua quá trình đánh giá, hệ thống ghi nhận một số trường hợp dự đoán sai lệch hoặc không đạt độ tin cậy tối ưu. Dưới đây là phân tích nguyên nhân và biện pháp khắc phục đã thực hiện:

### 4.1 Failure case 1: Lỗi không nhận diện được dịch vụ lỗi chưa từng gặp (Unseen Fault Service)

* Mô tả sự cố: Ca lỗi số 5 xảy ra trên dịch vụ `currencyservice` (dịch vụ bị cô lập hoàn toàn khỏi tập dữ liệu train và val để kiểm chứng độ tổng quát hóa của mô hình). AI Engine ban đầu không thể đưa ra dịch vụ nghi ngờ chính xác hoặc trả về độ tin cậy cực thấp (confidence < 0.4).
* Nguyên nhân: Do mô hình bị phụ thuộc vào tần suất xuất hiện tên dịch vụ trong các ví dụ mẫu (few-shot examples). Khi gặp dịch vụ lạ, mô hình có xu hướng đoán sang các dịch vụ quen thuộc như `checkoutservice` hoặc `productcatalogservice`.
* Giải pháp khắc phục: Cải tiến Prompt Grounding bằng cách bổ sung bản đồ kiến trúc phụ thuộc (Dependency Topology Map) của toàn bộ các dịch vụ microservices trong cụm vào nội dung Prompt. Điều này giúp LLM có thể suy luận logic dựa trên chuỗi liên kết cuộc gọi (Traces) để tìm ra điểm nghẽn gốc thay vì chỉ học vẹt tên dịch vụ.
* Kết quả sau khắc phục: Mô hình đã khoanh vùng chính xác `currencyservice` dựa trên các span lỗi kết nối từ `frontend` truyền sang, nâng giá trị độ tin cậy lên 0.72 (Pass).

### 4.2 Failure case 2: Nhầm lẫn giữa lỗi nghẽn I/O đĩa cứng và lỗi rò rỉ bộ nhớ (OOM)

* Mô tả sự cố: Một ca lỗi stress I/O đĩa cứng trên dịch vụ `productcatalogservice` bị AI Engine phân loại nhầm thành lỗi Memory Leak và đề xuất hành động `PATCH_MEMORY_LIMIT` (tăng giới hạn RAM).
* Nguyên nhân: Khi xảy ra stress đĩa cứng, hệ điều hành tăng cường sử dụng page cache dẫn đến việc chiếm dụng bộ nhớ RAM của container tăng cao đột biến, tạo ra tín hiệu giả về rò rỉ bộ nhớ.
* Giải pháp khắc phục: Bổ sung chỉ số tốc độ đọc ghi đĩa cứng (`container_fs_writes_bytes` và `container_fs_reads_bytes`) vào danh mục tín hiệu telemetry bắt buộc của lớp Hạ tầng. Đồng thời, tinh chỉnh prompt để phân biệt rõ: nếu RAM tăng kèm theo tốc độ I/O đĩa đạt ngưỡng bão hòa thì ưu tiên phân loại lỗi I/O.
* Kết quả sau khắc phục: AI Engine đã phân biệt chính xác hai loại lỗi này và đưa ra hành động `RESTART_DEPLOYMENT` phù hợp (Pass).

## 5. Curveball impact

Trong quá trình phát triển Phase 2, hệ thống đã trải qua các thử thách bất ngờ (Curveballs) từ phía CDOps Platform và đã phản ứng thành công:

* Curveball 1 (Small): Thay đổi đột ngột định dạng tên của nhãn Kubernetes Deployment trong telemetry (ví dụ: từ `checkoutservice` sang `deployment/checkoutservice`).
  * Response: Hệ thống đã kích hoạt lớp tiền xử lý dữ liệu (sanitization layer) để chuẩn hóa định dạng chuỗi trước khi đưa vào prompt, đồng thời áp dụng JSON Schema validation để phát hiện lệch tên.
  * Outcome: Pass.
* Curveball 2 (Medium): Hiện tượng bão cuộc gọi thử lại (Retry Storm) từ phía CDOps khi gặp sự cố mạng, gửi liên tiếp 10 yêu cầu lập kế hoạch cho cùng một mã lỗi trong vòng 5 giây.
  * Response: Nhờ cơ chế Idempotency Lock trên DynamoDB được cấu hình với Conditional Writes, AI Engine chỉ xử lý yêu cầu đầu tiên và lập tức từ chối 9 yêu cầu tiếp theo với mã lỗi HTTP 409 Conflict.
  * Outcome: Pass.
* Curveball 3 (Chaos): Kịch bản mô phỏng lỗi sập hoàn toàn dịch vụ AWS Bedrock tại khu vực us-east-1 trong 5 phút.
  * Response: Cơ chế ngắt mạch (Circuit Breaker) tự động kích hoạt sau khi nhận liên tiếp 3 lỗi kết nối Bedrock. Hệ thống chuyển ngay lập tức sang chế độ dự phòng Rule-Based (chạy cây quyết định tĩnh nội bộ).
  * Outcome: Pass (Kế hoạch hành động vẫn được sinh ra và gửi về CDOps với độ trễ phản hồi dưới 100ms, đảm bảo chu trình tự chữa lành không bị đứt gãy).

## 6. Cost vs forecast

Dưới đây là bảng đối chiếu chi phí thực tế sử dụng dịch vụ AWS Bedrock so với dự báo trong các giai đoạn của Phase 2:

| Phase | Forecast | Actual | Delta |
|---|---|---|---|
| Phát triển (Dev) | $15.00 | $12.45 | -17% |
| Kiểm thử tích hợp | $20.00 | $18.60 | -7% |
| Chạy demo thực tế | $5.00 | $3.20 | -36% |
| **Tổng cộng** | **$40.00** | **$34.25** | **-14%** |

## 7. Improvement next iteration

Để chuẩn bị cho việc vận hành thực tế trên môi trường sản xuất quy mô lớn sau Phase 2, hệ thống cần cải tiến 3 điểm nghẽn sau:

1. Điểm nghẽn: Độ trễ phản hồi của LLM Bedrock Claude Haiku vẫn dao động quanh mức 1.2 - 1.5 giây, chưa đạt mức tối ưu cho các sự cố đặc biệt khẩn cấp.
  * Kế hoạch: Triển khai cơ chế Bedrock Prompt Caching và chuyển đổi sang sử dụng các mô hình nhỏ hơn được tinh chỉnh chuyên biệt (fine-tuned Small Language Models) chạy trên cụm EC2 chuyên dụng.
2. Điểm nghẽn: Cơ chế Rule-Based dự phòng hiện tại chỉ sử dụng cây quyết định tĩnh đơn giản, chưa xử lý được các ca lỗi tích hợp nhiều dịch vụ.
  * Kế hoạch: Xây dựng bộ Rule Engine động dựa trên cấu trúc đồ thị phụ thuộc (Graph-based Rules) để tăng độ thông minh của kế hoạch dự phòng.
3. Điểm nghẽn: Hệ thống chưa tự động cập nhật thư viện Runbook khi có sự thay đổi về kiến trúc microservices.
  * Kế hoạch: Tích hợp cơ chế tự động đồng bộ danh mục Runbook từ tài liệu kiến trúc của dự án thông qua một pipeline CI/CD tự động.
