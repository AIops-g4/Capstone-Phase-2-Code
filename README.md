### 1. Quy luật đặt tên Nhánh (Branch Naming)

Hệ thống nhánh sử dụng ký tự thường, các từ cách nhau bằng dấu gạch ngang (`-`), ngoại trừ tiền tố danh mục. Cấu trúc chung cho các nhánh hỗ trợ là `<tiền_tố>/<tên_chức_năng_hoặc_mã_tác_vụ>`.

**Nhánh chính (Core Branches)**

* **main** (hoặc **master**): Lưu trữ mã nguồn ổn định, đã qua kiểm thử và sẵn sàng triển khai lên Production. Không commit trực tiếp lên nhánh này.


* **develop**: Nhánh tích hợp chính cho quá trình phát triển.



**Nhánh chức năng và xử lý sự cố (Supporting Branches)**

* **AI/<tên-chức-năng>**: Áp dụng bắt buộc đối với các nhánh mà phần lớn hoặc toàn bộ mã nguồn được tạo ra bởi AI. Ví dụ: `AI/auth-service`.


* **feat/<tên-tính-năng>**: Dành cho việc phát triển một tính năng mới thủ công. Ví dụ: `feat/payment-gateway`.


* **fix/<tên-lỗi>**: Dành cho việc sửa các lỗi phát hiện trong quá trình kiểm thử hoặc trên Staging/UAT. Ví dụ: `fix/login-session-timeout`.


* **hotfix/<tên-sự-cố>**: Dành cho việc sửa khẩn cấp các lỗi nghiêm trọng xuất hiện trực tiếp trên Production. Ví dụ: `hotfix/security-vulnerability-v1`.


* **refactor/<mục-tiêu>**: Dành cho việc tái cấu trúc mã nguồn để tối ưu cấu trúc, nâng cao khả năng bảo trì nhưng không làm thay đổi hành vi bên ngoài. Ví dụ: `refactor/clean-database-service`.


* **docs/<tài-liệu>**: Dành cho việc cập nhật hoặc bổ sung tài liệu hướng dẫn, API documentation. Ví dụ: `docs/update-installation-guide`.



---

### 2. Các thẻ Tag khi Commit (Commit Message Tags)

Dự án áp dụng tiêu chuẩn Conventional Commits với cấu trúc: `<tag>(<phạm_vi_ảnh_hưởng>): <mô_tả_ngắn_gọn>`. Danh sách các thẻ tag được quy định như sau:

1. **feat**: Thêm một tính năng mới cho hệ thống.


2. **fix**: Sửa một lỗi kỹ thuật hoặc logic trong mã nguồn.


3. **docs**: Thay đổi chỉ liên quan đến tài liệu, không ảnh hưởng đến mã nguồn chạy.


4. **style**: Thay đổi về định dạng code như khoảng trắng, dấu chấm phẩy, định dạng hiển thị, không làm thay đổi logic xử lý.


5. **refactor**: Thay đổi mã nguồn nhưng không thuộc nhóm sửa lỗi (fix) cũng không thuộc nhóm thêm tính năng (feat).


6. **perf**: Thay đổi mã nguồn nhằm mục đích tăng hiệu năng, tốc độ xử lý hoặc giảm tiêu hao tài nguyên.


7. **test**: Thêm các bài kiểm thử mới hoặc sửa đổi các bài kiểm thử hiện có.


8. **chore**: Các thay đổi đối với quy trình xây dựng dự án, cấu hình công cụ phụ trợ hoặc thư viện bên ngoài mà không làm thay đổi source code cốt lõi.

10. **ci**: Thay đổi cấu hình liên quan đến quy trình tích hợp và triển khai tự động (CI/CD).

## Dataset 
https://zenodo.org/records/14590730?preview_file=RE3-OB.zip



