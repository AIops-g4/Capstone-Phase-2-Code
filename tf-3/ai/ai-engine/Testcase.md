# TEST CASES

## Nhóm A - Happy Path theo từng loại Fault (/v1/decide)

| Test ID | Description | Fault | Expected Runbook | Actual Runbook | Expected Action | Actual Action | Result |
|---------|-------------|-------|------------------|----------------|-----------------|---------------|--------|
| TC-01 | Kiểm tra hệ thống chọn đúng runbook khi phát hiện CPU Saturation trên checkoutservice. | cpu | CPUSaturationRecoveryRunbook | CPUSaturationRecoveryRunbook | SCALE_REPLICAS | SCALE_REPLICAS | ✅ PASS |
| TC-02 | Kiểm tra hệ thống chọn đúng runbook khi phát hiện Memory Leak trên cartservice. | mem | MemoryLeakRecoveryRunbook | MemoryLeakRecoveryRunbook | PATCH_MEMORY_LIMIT | PATCH_MEMORY_LIMIT | ✅ PASS |
| TC-03 | Kiểm tra hệ thống chọn đúng runbook khi phát hiện Network Latency. | delay | NetworkLatencyRecoveryRunbook | NetworkLatencyRecoveryRunbook | RESTART_DEPLOYMENT | RESTART_DEPLOYMENT | ✅ PASS |
| TC-04 | Kiểm tra hệ thống chọn đúng runbook khi phát hiện Packet Loss. | loss | PacketLossRecoveryRunbook | PacketLossRecoveryRunbook | RESTART_DEPLOYMENT | RESTART_DEPLOYMENT | ✅ PASS |
| TC-05 | Kiểm tra hệ thống chọn đúng runbook khi phát hiện Disk I/O bất thường. | disk | DiskIORecoveryRunbook | DiskIORecoveryRunbook | RESTART_DEPLOYMENT | RESTART_DEPLOYMENT | ✅ PASS |
| TC-06 | Kiểm tra hệ thống chọn đúng runbook khi phát hiện Socket Exhaustion. | socket | SocketExhaustionRecoveryRunbook | SocketExhaustionRecoveryRunbook | SCALE_REPLICAS | SCALE_REPLICAS | ✅ PASS |
| TC-07 | Kiểm tra fault loại f1 (RE3) sử dụng runbook mặc định. | f1 | DefaultRecoveryRunbook | DefaultRecoveryRunbook | RESTART_DEPLOYMENT | RESTART_DEPLOYMENT | ✅ PASS |
| TC-08 | Kiểm tra fault không xác định (fallback). | xyz123 | DefaultRecoveryRunbook | DefaultRecoveryRunbook | RESTART_DEPLOYMENT | RESTART_DEPLOYMENT | ✅ PASS |

---

## Nhóm B - Detect: Dữ liệu Telemetry bất thường (/v1/detect)

| Test ID | Description | Fault | Expected Runbook | Actual Runbook | Expected Action | Actual Action | Result |
|---------|-------------|-------|------------------|----------------|-----------------|---------------|--------|
| TC-09 | telemetry_window rỗng phải trả lỗi 4xx có ý nghĩa. | Empty telemetry_window | N/A | N/A | HTTP 400 | HTTP 400 | ✅ PASS |
| TC-10 | Spike CPU nhỏ, không vượt ngưỡng anomaly nên anomaly_detected=false. | Small CPU Spike | N/A | N/A | anomaly_detected=false | API trả HTTP 400 Bad Request | ❌ FAIL |
| TC-11 | Nhiều service bất thường, BARO phải trả top-5 và decide chọn service đầu tiên. | Multiple anomalies | N/A | N/A | target_service = Top-5 | anomaly_detected, nhưng API trả HTTP 400 | ❌ FAIL |
| TC-12 | Timestamp sai định dạng phải trả lỗi validate rõ ràng. | Invalid Timestamp | N/A | N/A | HTTP 400 | HTTP 400 | ✅ PASS |

---

## Nhóm C - Idempotency & Workflow Validation

| Test ID | Description | Fault | Expected Runbook | Actual Runbook | Expected Action | Actual Action | Result |
|---------|-------------|-------|------------------|----------------|-----------------|---------------|--------|
| TC-13 | Gửi detect hai lần với cùng idempotency_key phải trả kết quả nhất quán. | Duplicate Idempotency Key | N/A | N/A | Kết quả giống nhau, không xử lý hai lần | API trả lỗi (r1 OK, r2 False) | ❌ FAIL |
| TC-14 | Gọi decide với correlation_id không tồn tại phải báo lỗi. | Invalid Correlation ID | N/A | CPUSaturationRecoveryRunbook | Trả lỗi, không suy diễn runbook | Server vẫn trả runbook | ❌ FAIL |
| TC-15 | Verify khi chưa gọi decide phải báo lỗi hoặc cảnh báo. | Missing Decide Context | N/A | N/A | Không success khi thiếu context | Server vẫn trả success=true dù không có context | ❌ FAIL |

---

## Nhóm D - Verify: Regression & Failure Handling

| Test ID | Description | Fault | Expected Runbook | Actual Runbook | Expected Action | Actual Action | Result |
|---------|-------------|-------|------------------|----------------|-----------------|---------------|--------|
| TC-16 | Sau khi execute nhưng telemetry vẫn lỗi thì verify phải báo thất bại và đề xuất hành động tiếp theo. | Regression after execute | N/A | N/A | success=false, regression_detected=true, next_action ≠ DONE | success=False, regression_detected=False, next_action=ESCALATE | ✅ PASS |
| TC-17 | action_executed.status = FAILED thì verify không được trả DONE. | Failed Action Execution | N/A | N/A | success=false, next_action ≠ DONE | success=False, next_action=RETRY | ✅ PASS |

---



### Các lỗi còn tồn tại

- `/v1/detect` trả về HTTP 400 cho một số trường hợp telemetry hợp lệ (TC-10, TC-11).
- `/v1/decide` vẫn suy diễn runbook khi `correlation_id` không tồn tại (TC-14).
- `/v1/verify` chưa kiểm tra workflow context, vẫn trả `success=true` khi chưa thực hiện bước `decide` (TC-15).
- Cơ chế idempotency chưa đảm bảo trả về kết quả nhất quán cho cùng một `idempotency_key` (TC-13).


---

# Test Analysis

---

# Failure Analysis

## 1. Boundary Value Testing

| Test ID | Finding | Impact | Description | Status |
|---------|----------|--------|-------------|--------|
| TC-18 | Hệ thống không xử lý chính xác telemetry khi CPU = 0%. | Cho thấy việc xử lý không đúng các giá trị biên nhỏ nhất của CPU hoặc logic phát hiện anomaly. | Xác minh rằng mức sử dụng CPU 0% không kích hoạt anomaly detection và được xử lý chính xác. | ❌ FAIL |
| TC-19 | Hệ thống không xử lý chính xác telemetry khi CPU đạt đến ngưỡng (80%). | Logic so sánh ngưỡng (`>=` so với `>`) chưa được triển khai rõ ràng hoặc hoạt động không chính xác. | Xác minh hành vi anomaly detection khi mức sử dụng CPU ở chính xác ngưỡng đã cấu hình (80%). | ❌ FAIL |
| TC-20 | Hệ thống không phát hiện được anomaly hoặc không chọn đúng runbook như mong đợi khi CPU = 100%. | Kịch bản bão hòa CPU ở mức nghiêm trọng cao không được xử lý chính xác. | Xác minh rằng mức sử dụng CPU tối đa sẽ kích hoạt anomaly detection và chọn đúng CPU recovery runbook. | ❌ FAIL |
| TC-21 | Hệ thống không xử lý chính xác telemetry với Memory = 0. | Cho thấy việc thiếu validation hoặc xử lý không đúng các giá trị bộ nhớ tối thiểu. | Xác minh rằng việc sử dụng bộ nhớ bằng 0 được xử lý an toàn mà không gây ra lỗi server. | ❌ FAIL |
| TC-22 | Hệ thống không xử lý chính xác telemetry với Latency = 0 ms. | Cho thấy việc xử lý không đúng các giá trị latency tối thiểu. | Xác minh rằng latency bằng 0 không kích hoạt false anomaly detection. | ❌ FAIL |

---

## 2. Input Validation

| Test ID | Finding | Impact | Description | Status |
|---------|----------|--------|-------------|--------|
| TC-23 | Thiếu `suspected_fault_type` gây ra lỗi HTTP 500 thay vì HTTP 400/422. | Thiếu request validation có thể làm lộ các lỗi phía server-side. | Xác minh rằng các request thiếu `suspected_fault_type` sẽ bị từ chối với mã HTTP 400 hoặc 422. | ❌ FAIL |
| TC-24 | Thiếu `service` gây ra lỗi HTTP 500 thay vì HTTP 400/422. | Việc validation cho trường bắt buộc chưa hoàn thiện. | Xác minh rằng các request không có `service` sẽ bị từ chối với mã HTTP 400 hoặc 422. | ❌ FAIL |
| TC-25 | Giá trị `service` để trống vẫn được server chấp nhận. | Các request không hợp lệ được chấp nhận, dẫn đến hành vi không thể lường trước. | Xác minh rằng các tên service để trống sẽ bị từ chối. | ❌ FAIL |
| TC-26 | `fault = null` gây ra lỗi HTTP 500. | Các giá trị null không được xử lý một cách an toàn. | Xác minh rằng các giá trị fault là null sẽ bị từ chối với các thông báo lỗi validation phù hợp. | ❌ FAIL |
| TC-27 | Kiểu dữ liệu không hợp lệ (`fault = 123`) gây ra lỗi HTTP 500. | Thiếu validation về kiểu dữ liệu (type validation). | Xác minh rằng các kiểu dữ liệu không hợp lệ sẽ bị từ chối trước khi xử lý. | ❌ FAIL |

---

## 3. API Contract Validation

| Test ID | Finding | Impact | Description | Status |
|---------|----------|--------|-------------|--------|
| TC-31 | Response không bao gồm đầy đủ các trường bắt buộc. | Client có thể không parse hoặc không xử lý được các API response không hoàn chỉnh. | Xác minh rằng API response luôn chứa đầy đủ các trường bắt buộc được định nghĩa trong API contract. | ❌ FAIL |
| TC-32 | Trường `runbook` trả về không đúng kiểu string như mong đợi. | Kiểu dữ liệu không nhất quán có thể làm hỏng quá trình deserialization và xử lý phía client-side. | Xác minh rằng trường `runbook` luôn được trả về dưới dạng một string hợp lệ. | ❌ FAIL |
| TC-33 | Giá trị `action` trả về không nằm trong các giá trị enum đã định nghĩa trước. | Các giá trị action không hợp lệ có thể gây ra lỗi cho hệ thống tự động hóa downstream. | Xác minh rằng `action` luôn là một trong các giá trị: `SCALE_REPLICAS`, `PATCH_MEMORY_LIMIT`, hoặc `RESTART_DEPLOYMENT`. | ❌ FAIL |

---

## 4. Workflow & Idempotency Validation

| Test ID | Finding | Impact | Description | Status |
|---------|----------|--------|-------------|--------|
| TC-34 | Các request lặp lại với cùng một idempotency key không được xử lý chính xác. | Việc thực thi workflow bị trùng lặp có thể xảy ra, gây ra hành vi xử lý sự cố (remediation) không nhất quán. | Xác minh rằng các request `decide` hoặc `verify` lặp lại với cùng một idempotency key sẽ trả về cùng một kết quả mà không tạo ra các workflow trùng lặp. | ❌ FAIL |
| TC-36 | Toàn bộ workflow Detect → Decide → Verify không thực thi thành công. | Quy trình xử lý sự cố khép kín (closed-loop remediation) cốt lõi chưa hoàn thiện. | Xác minh rằng workflow remediation hoàn chỉnh được thực thi thành công từ khâu anomaly detection cho đến khâu verification. | ❌ FAIL |
| TC-37 | Hệ thống chấp nhận một request Verify mà không có bước Decide trước đó. | Thiếu validation cho trạng thái của workflow (workflow state validation), dẫn đến thứ tự thực thi không hợp lệ. | Xác minh rằng các request `verify` sẽ bị từ chối nếu không có thao tác `decide` tương ứng nào được hoàn thành trước đó. | ❌ FAIL |
| TC-38 | Nhiều request Verify trả về kết quả không nhất quán. | Trạng thái workflow không được quản lý nhất quán giữa các thao tác lặp lại. | Xác minh rằng các request `verify` lặp lại sẽ cho ra kết quả nhất quán cho cùng một workflow remediation. | ❌ FAIL |

---

## 5. Concurrency & Performance Testing

| Test ID | Finding | Impact | Description | Status |
|---------|----------|--------|-------------|--------|
| TC-K1 | Tất cả 10 request Detect đồng thời (concurrent) đều thất bại. | Hệ thống không thể xử lý các request đồng thời một cách đáng tin cậy, làm hạn chế khả năng mở rộng (scalability). | Xác minh rằng nhiều request Detect có thể được xử lý đồng thời mà không bị lỗi hoặc bị xử lý trùng lặp. | ❌ FAIL |
| TC-39 | Hệ thống không đạt được thời gian phản hồi mục tiêu khi có các request liên tục. | Hiệu năng kém khi chịu tải có thể làm giảm tính khả dụng (availability) và độ phản hồi của hệ thống. | Xác minh rằng API duy trì thời gian phản hồi P95 dưới 500 ms khi xử lý 100 request liên tiếp. | ❌ FAIL |
| TC-40 | Xử lý một tập dữ liệu telemetry chứa 10,000 bản ghi bị thất bại hoặc bị timeout. | Hệ thống không mở rộng tốt đối với các tập dữ liệu telemetry lớn. | Xác minh rằng các cửa sổ dữ liệu telemetry lớn có thể được xử lý mà không bị timeout hoặc lỗi dịch vụ. | ❌ FAIL |

---

## 6. Security & Robustness Testing

| Test ID | Finding | Impact | Description | Status |
|---------|----------|--------|-------------|--------|
| TC-41 | Hệ thống đã xử lý an toàn một payload XSS mà không thực thi nó. | Chứng minh khả năng chống lại các cuộc tấn công script injection cơ bản. | Xác minh rằng input JavaScript độc hại được xử lý như văn bản thuần túy (plain text) và không ảnh hưởng đến việc thực thi của hệ thống. | ✅ PASS |
| TC-42 | Payload path traversal được xử lý như một input bình thường mà không truy cập vào file system. | Chứng minh khả năng bảo vệ chống lại các cuộc tấn công path traversal cơ bản. | Xác minh rằng các chuỗi ký tự path traversal không thể bị lợi dụng để truy cập hoặc thao túng các file trên server. | ✅ PASS |
| TC-N1 | Bước Verify không xử lý chính xác khi `action_executed.status = FAILED`. | Trạng thái remediation không chính xác có thể dẫn đến các quyết định vận hành sai lệch. | Xác minh rằng các hành động remediation bị thất bại sẽ trả về một hành động tiếp theo phù hợp (ví dụ: `RETRY`) thay vì báo cáo thành công. | ❌ FAIL |
| TC-N2 | Bước Verify không xử lý chính xác khi `action_executed.status = TIMEOUT`. | Các điều kiện timeout không được xử lý nhất quán trong quá trình verification. | Xác minh rằng các kịch bản timeout sẽ kích hoạt một hành động khôi phục (recovery) hoặc chuyển tiếp leo thang (escalation) phù hợp. | ❌ FAIL |
| TC-O1 | Các giá trị fault ngẫu nhiên được xử lý mà không làm sập server. | Chứng minh tính mạnh mẽ (robustness) trước các kiểu fault không mong muốn hoặc không xác định. | Xác minh rằng các kiểu fault được tạo ngẫu nhiên không gây ra crash server hoặc các exception không mong muốn. | ✅ PASS |

---

# Severity Summary

| Severity | Test IDs | Description |
|----------|----------|-------------|
| 🔴 Critical | TC-23, TC-24, TC-25, TC-26, TC-27 | Thiếu input validation dẫn đến lỗi HTTP 500 hoặc chấp nhận request không hợp lệ. |
| 🔴 Critical | TC-K1 | Hệ thống bị lỗi khi xử lý các request đồng thời (concurrent). |
| 🔴 Critical | TC-36 | Workflow cốt lõi Detect → Decide → Verify bị thất bại. |
| 🟠 Major | TC-31, TC-32, TC-33 | Sự không nhất quán trong API response contract. |
| 🟠 Major | TC-34, TC-37, TC-38 | Các vấn đề về quản lý trạng thái workflow và tính lũy đẳng (idempotency). |
| 🟡 Minor | TC-18 ~ TC-22 | Việc xử lý điều kiện biên (boundary condition) cần được cải thiện. |
| 🟡 Minor | TC-39, TC-40 | Cần tối ưu hóa thêm về hiệu năng (performance) và khả năng mở rộng (scalability). |

---

# Conclusion

Hệ thống đã xử lý thành công một số kịch bản về tính mạnh mẽ (robustness) và bảo mật (security) (ví dụ: các chuỗi input độc hại và các giá trị fault ngẫu nhiên). Tuy nhiên, kết quả kiểm thử cho thấy một vài hạn chế quan trọng sau:

- Việc thiếu validation cho request gây ra các lỗi HTTP 500 thay vì trả về các response lỗi client phù hợp.
- Quản lý trạng thái workflow (Detect → Decide → Verify) chưa hoàn thiện.
- Việc xử lý tính lũy đẳng (idempotency) và xử lý đồng thời (concurrency) cần được cải thiện.
- API response schema chưa hoàn toàn nhất quán.
- Hiệu năng khi chịu tải cao và với các tập dữ liệu telemetry lớn cần được tối ưu hóa thêm.

Nhìn chung, các chức năng đã triển khai đã thể hiện được logic remediation cốt lõi nhưng cần bổ sung thêm validation, kiểm soát workflow và cải thiện khả năng mở rộng trước khi có thể sẵn sàng đưa vào môi trường production.