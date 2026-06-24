**Push-back 1: Tên signal thay đổi hoàn toàn — breaking change**

Contract cũ → contract mới:

| Cũ | Mới |
| :---- | :---- |
| istio\_request\_error\_rate | service\_error\_rate |
| istio\_request\_latency\_p95 | service\_latency\_p95 |
| container\_memory\_working\_set\_bytes | container\_resource\_usage |
| app\_log\_error\_event | application\_log\_event |
| trace\_span\_error\_event | distributed\_trace\_error\_event |

**Vấn đề:** CDO-02 đã viết docs, test cases, safety gate, và telemetry pipeline dựa theo tên cũ. Đây là **breaking change** nhưng contract không đánh version mới hay có migration notice.

**Yêu cầu:** AI team cần confirm đây là final names và CDO cần thời gian update toàn bộ mapping.

 

**Push-back 2: pattern\_type mới trong /v1/decide — CDO không biết phải làm gì**

Contract mới thêm field bắt buộc:

"pattern\_type": "urgent"   *// hoặc "deferred"*

**Vấn đề:** Contract không giải thích CDO phải xử lý khác nhau thế nào giữa urgent và deferred. Nếu deferred là "GitOps path", CDO cần biết:

* CDO có cần delay execution không?  
* CDO có cần tạo PR/commit thay vì apply trực tiếp không?  
* Safety gate logic có khác không?

**Yêu cầu:** AI cần định nghĩa rõ CDO phải làm gì với từng giá trị pattern\_type.

 

**Push-back 3: Action enum đổi tên — code CDO sẽ break**

| Cũ | Mới |
| :---- | :---- |
| ADJUST\_MEMORY\_LIMIT | PATCH\_MEMORY\_LIMIT |
| SCALE\_UP\_PODS | SCALE\_REPLICAS |
| UPDATE\_ENV\_SECRET | ROTATE\_SECRET |
| *(chưa có)* | ROLLOUT\_UNDO *(mới)* |
| *(chưa có)* | DELETE\_POD *(mới)* |

**Vấn đề:** CDO đã viết allow-list và executor logic dựa trên tên cũ. Đổi tên giữa chừng mà không có changelog là breaking change.

**Yêu cầu:** Với 2 action mới ROLLOUT\_UNDO và DELETE\_POD — AI cần mô tả rõ khi nào trả về và CDO phải handle như thế nào. DELETE\_POD đặc biệt nhạy cảm vì ảnh hưởng trực tiếp đến workload.

 

**Push-back 4: /v1/decide SLA tăng từ 500ms lên 3000ms — ảnh hưởng end-to-end**

**Vấn đề:** SLA cũ là \< 500ms, nay tăng lên \< 3000ms (tăng 6 lần). CDO thiết kế timeout và end-to-end SLO "\< 5 phút" dựa trên 500ms. Với 3000ms, tổng thời gian AI processing có thể lên đến:

detect (300ms) \+ decide (3000ms) \+ verify (500ms) \= \~3.8 giây chỉ riêng AI call

Nếu có retry, timeout, hoặc chờ dry-run K8s thêm, target \< 5 phút vẫn OK nhưng margin mỏng hơn nhiều.

**Yêu cầu:** AI xác nhận fallback rule-based (\< 500ms) sẽ được kích hoạt trong trường hợp nào để CDO có thể estimate worst-case latency.

 

**Push-back 5: verify\_policy.success\_conditions — CDO không biết parse format này**

Contract mới trả về:

"success\_conditions": \[

  "pod\_ready \== true",

  "restart\_count\_no\_increase \== true",

  "container\_memory\_usage\_pct \< 80"

\]

**Vấn đề:** Đây là một mini DSL (domain-specific language). CDO executor phải parse và evaluate từng điều kiện này — nhưng contract không định nghĩa:

* Grammar/syntax của DSL là gì?  
* Các metric name trong điều kiện lấy từ đâu (container\_memory\_usage\_pct là signal nào trong telemetry contract?)  
* Nếu CDO không parse được 1 điều kiện thì xử lý thế nào — pass hay fail?

**Yêu cầu:** AI cần publish full spec của success\_conditions DSL hoặc đơn giản hóa thành enum có sẵn.

 

**Push-back 6: CDO phải tạo IAM Role cho AI ABAC — nhưng contract không nói rõ spec**

Deployment contract mới thêm:

AI sẽ AssumeRole vào: arn:aws:iam::\*:role/tf-3-tenant-\[tenant\_id\]-role

**Vấn đề:** Contract yêu cầu CDO phải tạo IAM role này, nhưng không nói:

* Role này cần có permission gì?  
* Trust policy cần trust AI ECS task role nào?  
* Scope của role này có giới hạn chỉ trong CDO account hay cần cross-account?

**Yêu cầu:** AI cần cung cấp template IAM role policy và trust policy để CDO provision đúng.

 

**Push-back 7: cost\_cap\_exceeded: true trong decide response — CDO xử lý thế nào?**

Khi AI Bedrock vượt $50/ngày, AI trả về decide response có thêm:

"cost\_cap\_exceeded": true

**Vấn đề:** Field này không có trong JSON schema chính thức của /v1/decide response. Nếu CDO dùng additionalProperties: false để validate response, request sẽ bị reject. Ngoài ra, CDO cần biết khi nhận flag này thì:

* Action plan vẫn execute được không?  
* Có cần escalate thay vì execute không?

**Yêu cầu:** AI thêm cost\_cap\_exceeded vào schema chính thức và mô tả CDO behavior khi nhận flag này.

 

**Push-back 8: SQS biến mất khỏi telemetry contract — CDO đã thiết kế dựa theo đó**

Contract cũ nói rõ CDO emit signal qua **SQS**. Contract mới chỉ nói emit point là "Ingestion Prometheus / OTel" hoặc "OTel Collector / Fluentd" — không nhắc SQS.

**Vấn đề:** CDO-02 đã thiết kế SQS queue làm telemetry buffer trong 02\_infra\_design.md. Nếu SQS không còn là channel chính thức thì CDO cần biết telemetry gửi đi theo cơ chế nào.

**Yêu cầu:** AI xác nhận channel truyền telemetry từ CDO sang AI Engine là gì — SQS, HTTP push, hay Prometheus pull?

 

**Push-back 9: ArgoCD rollback vẫn còn trong contract — inconsistent với ECS**

Deployment contract Section 6.C vẫn ghi:

"ArgoCD tự động rollback trạng thái Kubernetes sang Git commit SHA"

**Vấn đề:** AI Engine chạy trên **ECS Fargate**, không phải Kubernetes. ArgoCD là GitOps tool cho K8s — không quản lý ECS task definition. Đây là mâu thuẫn tồn tại từ phiên bản trước, chưa được fix.

**Yêu cầu:** Sửa thành ECS service rollback (task definition version trước) hoặc giải thích rõ ArgoCD được dùng để quản lý component nào.

 

