# AIOps AI Engine - Hệ Thống Phát Hiện Bất Thường & Phân Tích Nguyên Nhân Gốc (RCA)

Hệ thống AIOps AI Engine được thiết kế để tự động phát hiện các hành vi bất thường trong chuỗi chỉ số hiệu năng (metrics) của hệ thống microservices và phân tích, định vị chính xác dịch vụ gốc gây ra lỗi (Service Root Cause Localization) kết hợp với các kịch bản tự phục hồi (Runbooks).

---

## 1. Hướng Dẫn Cài Đặt Môi Trường

Hệ thống chạy trên môi trường **Conda** với phiên bản Python 3.12.

### Bước 1: Kích hoạt môi trường Conda
Kích hoạt môi trường `w6-mini-project` đã được cài đặt sẵn:
```bash
conda activate w6-mini-project
```

### Bước 2: Cài đặt thư viện bổ sung (nếu chưa có)
Hệ thống sử dụng thuật toán **BOCPD** và **BARO RCA** từ thư viện `fse-baro`:
```bash
pip install fse-baro
```

### Bước 3: Tải và thiết lập bộ dữ liệu (Dataset Setup)
Chạy các lệnh sau để tự động tải bộ dữ liệu giả lập sự cố RE2 và RE3 từ Google Drive về thư mục `dataset/` và giải nén chúng:

```bash
# Cài đặt công cụ gdown để tải dữ liệu (nếu chưa có)
pip install gdown

# Tạo thư mục dataset
mkdir -p dataset

# Tải và giải nén bộ dữ liệu RE2
gdown --id 12VpUPNx_ZWebA-cICyKmQmXjF3KpLJpP -O dataset/re2.zip
unzip dataset/re2.zip -d dataset/
rm dataset/re2.zip

# Tải và giải nén bộ dữ liệu RE3
gdown --id 1cZpnaZ1ijLUBssXzCnbGVWsT1NlnXtoy -O dataset/re3.zip
unzip dataset/re3.zip -d dataset/
rm dataset/re3.zip
```

### Bước 4: Khởi tạo Nhãn Ground Truth và Kịch Bản Runbooks
Sau khi đã tải và giải nén bộ dữ liệu, chạy script sau để tự động quét thư mục dữ liệu, trích xuất thời điểm tiêm lỗi và khởi tạo các file nhãn `ground_truth.json` cũng như danh sách kịch bản tự phục hồi `runbooks.json`:

```bash
conda run -n w6-mini-project python scripts/generate_dataset_metadata.py
```

---

## 2. Khởi Chạy API Server (Chế độ Production)

API Server cung cấp cổng giao tiếp RESTful API để tích hợp trực tiếp với hệ thống giám sát thời gian thực. Cổng mặc định là `8050`.

### Lệnh khởi chạy:
```bash
conda run -n w6-mini-project python src/server.py
```

### Các Endpoint Chính:
1. **`POST /v1/detect`**: Nhận luồng metrics và logs thời gian thực.
   * **Cơ chế quét thông minh (Immediate Scanning)**: Vòng lặp quét bất thường sẽ trả về kết quả ngay lập tức khi phát hiện điểm bất thường đầu tiên, giúp giảm thiểu thời gian phục hồi (RTO) tối đa.
2. **`POST /v1/decide`**: Khớp dịch vụ bị lỗi với kịch bản tự phục hồi (Runbook) tương ứng và xuất ra kế hoạch hành động.
   * **Dịch vụ hóa (Service-Centric)**: Bỏ qua phân loại loại lỗi phức tạp, mặc định trả về `cpu` và ánh xạ trực tiếp sang kịch bản `DefaultRecoveryRunbook` để tăng tốc độ khởi động lại dịch vụ bị lỗi.

---

## 3. Chạy Đánh Giá Ngoại Tuyến (Offline Evaluation)

Kịch bản đánh giá ngoại tuyến giúp kiểm tra độ chính xác và hiệu năng của AI Engine trên tập dữ liệu sự cố giả lập gồm **90 kịch bản lỗi**.

Chạy lệnh duy nhất sau để thực hiện đánh giá hoàn chỉnh sử dụng thuật toán phát hiện bất thường BOCPD và engine phân tích nguyên nhân gốc BARO:

```bash
conda run -n w6-mini-project python scripts/evaluate.py --sample-size 90 --engine baro --use-bocpd
```

---

## 4. Các Tính Năng & Tối Ưu Nổi Bật

1. **Kiến trúc Đánh Giá Song Song (Dual-Track Architecture)**:
   * Khi bật BOCPD, hệ thống chỉ chạy phát hiện bất thường trên một phân đoạn nhỏ (Sliced Window) để đảm bảo tốc độ cực nhanh (tăng tốc **10 lần**).
   * Khi phát hiện bất thường, hệ thống tự động ánh xạ ngược chỉ mục (index) về chuỗi thời gian đầy đủ (Full Time-Series) để chạy phân tích tương quan RCA với baseline dài **600 giây** ổn định, đảm bảo độ chính xác định vị dịch vụ đạt mức tối đa.
2. **Cắt tỉa Baseline Động (Dynamic Baseline Capping)**:
   * Tự động điều chỉnh độ dài baseline dựa trên vị trí thực tế của sự cố trong các chuỗi dữ liệu ngắn:
     ```python
     baseline_len = min(EVAL_BOCPD_BASELINE_LENGTH, inject_row_idx)
     ```
   * Giúp loại bỏ hoàn toàn hiện tượng baseline bị nhiễm dữ liệu lỗi (baseline contamination), giữ Z-score luôn chính xác 100%.
3. **Bỏ qua các chỉ số dư thừa**:
   * Hệ thống tự lọc sạch và chỉ giữ lại các chỉ số SLIs quan trọng (Latency và Error rate) khi chạy phát hiện bất thường đa biến bằng BOCPD, giúp giảm số chiều dữ liệu, loại bỏ nhiễu và tăng tốc độ xử lý ma trận đồng phương sai lên **160 lần**.
