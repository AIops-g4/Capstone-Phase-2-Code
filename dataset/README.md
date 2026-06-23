# RCAEval RE2 & RE3 Online Boutique Dataset & Evaluation Pipeline

Dự án này chứa toàn bộ mã nguồn cấu hình, tập lệnh thiết lập tự động tải/giải nén dữ liệu, tập lệnh phân chia tập dữ liệu và chạy đánh giá E2E cho hệ thống **Online Boutique (OB)** với 2 bộ dữ liệu:
* **RE2-OB**: Các lỗi tài nguyên hạ tầng và đường truyền mạng (CPU, Memory, Disk, Sockets, Delay, Packet Loss).
* **RE3-OB**: Các lỗi logic ở cấp độ code ứng dụng (Status Code, Exception, Infinite Loop, Crash).

### 1. Bộ dữ liệu RE2-OB (Resource & Network Faults)
Bộ dữ liệu này tập trung vào các **lỗi hạ tầng, tài nguyên và mạng** xảy ra trong các microservices của Online Boutique.

* **Mục tiêu**: Đánh giá khả năng chẩn đoán lỗi tài nguyên phần cứng và các sự cố đường truyền.
* **Quy mô**:
  * **30 ca lỗi (Cases)**: Được sinh ra từ sự kết hợp của 5 dịch vụ bị tiêm lỗi và 6 loại lỗi khác nhau.
  * **90 lần chạy (Runs)**: Mỗi ca lỗi được chạy kiểm thử 3 lần độc lập để thu thập dữ liệu telemetry tĩnh.
* **Các dịch vụ bị tiêm lỗi (Faulty Services)**: `checkoutservice`, `currencyservice`, `emailservice`, `productcatalogservice`, `recommendationservice`.
* **6 loại lỗi được tiêm (Fault Types)**:
  1. `cpu`: Quá tải tài nguyên CPU.
  2. `mem`: Quá tải bộ nhớ (Memory leak / OOM).
  3. `disk`: Sự cố I/O đĩa cứng (Disk read/write stress).
  4. `socket`: Lỗi cạn kiệt sockets mở (Socket exhaustion).
  5. `delay`: Trễ truyền tải mạng (Network latency delay).
  6. `loss`: Mất gói tin truyền tải (Network packet loss).
* **Chiến lược phân chia độ khó**:
  * **Unseen Fault Service**: Toàn bộ lỗi trên dịch vụ **`currencyservice`** (18 runs) được giữ lại hoàn toàn để làm dữ liệu kiểm thử kiểm chứng độ tổng quát của mô hình (6 runs thuộc `public_test`, 12 runs thuộc `private_test`). Mô hình không được học lỗi của service này trong tập train.
  * **Hard Faults**: Các lỗi mạng phức tạp gồm `socket`, `loss` và `delay` được xếp vào nhóm lỗi khó (Hard faults) và ưu tiên phân phối vào tập dữ liệu ẩn `private_test`.

---

### 2. Bộ dữ liệu RE3-OB (Code-Level Faults)
Bộ dữ liệu này tập trung vào các **lỗi logic ứng dụng bên trong code** của các microservices Online Boutique.

* **Mục tiêu**: Đánh giá khả năng phân tích ngữ cảnh code (logs, stack traces, traces span error) để tìm ra dịch vụ có dòng code bị lỗi.
* **Quy mô**:
  * **10 ca lỗi (Cases)**.
  * **30 lần chạy (Runs)**: Mỗi ca lỗi chạy 3 lần độc lập.
* **Các dịch vụ bị tiêm lỗi (Faulty Services)**: `adservice`, `cartservice`, `currencyservice`, `emailservice`.
* **5 loại lỗi logic code được tiêm (Fault Types)**:
  1. `f1`: Lỗi Status Code (giao dịch trả về mã lỗi gRPC/HTTP).
  2. `f2`: Lỗi ném ra Exception (quăng lỗi Java/Python exception trong logs).
  3. `f3`: Lỗi chức năng / Logic sai lệch (Missing function).
  4. `f4`: Lỗi vòng lặp vô hạn (Infinite loop gây treo luồng xử lý).
  5. `f5`: Lỗi crash ứng dụng đột ngột (Unhandled exception).
* **Chiến lược phân chia độ khó**:
  * **Unseen Fault Service**: Dịch vụ **`currencyservice`** (với lỗi `f1`, gồm 3 runs) được cô lập dành riêng cho tập test.
  * **Hard Faults**: Các lỗi `f3` (lỗi logic khó nhận biết) và `f5` (lỗi crash không bắt được exception thông thường) được coi là lỗi khó và ưu tiên đưa vào `private_test`.

---

### 3. Cấu trúc dữ liệu thu thập của mỗi ca lỗi (Per-run Data Structure)
Trong mỗi thư mục chạy của cả RE2-OB và RE3-OB, CDO cung cấp 3 file dữ liệu tĩnh:
1. **`metrics.csv`**: Chứa các chỉ số đo lường tài nguyên CPU, Memory, Sockets, Network drops, và các chỉ số Istio (request total, error total, latency p95).
2. **`logs.csv`**: Logs stdout thu thập được từ các container (rất quan trọng để mô hình AI quét stack traces của lỗi RE3).
3. **`traces.csv`**: Ghi nhận toàn bộ cuộc gọi RPC/HTTP giữa các services, bao gồm mã trạng thái (`statusCode`) và độ trễ (`duration_ms`) của từng span.

### 4. Số lượng services 
1. `adservice`
2. `cartservice`
3. `checkoutservice`
4. `currencyservice`
5. `emailservice`
6. `frontend`
7. `frontendservice`
8. `paymentservice`
9. `productcatalogservice`
10. `recommendationservice`

*(Lưu ý: Các dịch vụ có lỗi được inject trực tiếp trong RE2 và RE3 bao gồm: `adservice`, `cartservice`, `checkoutservice`, `currencyservice`, `emailservice`, `productcatalogservice`, và `recommendationservice`)*

---

## HƯỚNG DẪN KHỞI CHẠY NHANH (QUICK START)

Đối với người mới bắt đầu, bạn chỉ cần thực hiện đúng **3 lệnh** sau để cài đặt và chạy E2E toàn bộ pipeline:

```bash
# Bước 1: Cài đặt các thư viện cần thiết (nếu chưa có)
pip install gdown jsonschema

# Bước 2 Chạy tập lệnh cài đặt tự động
python3 setup_dataset.py
```

---

## CHI TIẾT CÁC BƯỚC VẬN HÀNH

### Bước 1: Cài đặt dữ liệu tự động (`setup_dataset.py`)
Khi bạn chạy lệnh `python3 setup_dataset.py`, tập lệnh sẽ tự động thực hiện:
1. **Kiểm tra file nén**: Tìm kiếm các file zip của tập dữ liệu trong thư mục `compress/` (`RE2-OB.zip` và `RE3-OB.zip`).
   * *Mẹo*: Nếu các file zip đã có sẵn cục bộ, tập lệnh sẽ **bỏ qua bước tải từ Google Drive** để tránh chạm giới hạn băng thông (Google Drive Quota Limit) và tiến hành giải nén ngay lập tức.
2. **Giải nén thông minh**: Giải nén dữ liệu vào thư mục `raw/RE2-OB` và `raw/RE3-OB`.
3. **Tự động Phân chia dữ liệu (Autosplit)**: Gọi tập lệnh `split_dataset.py` để phân chia tập dữ liệu thành các tập `train`, `val`, `public_test`, và `private_test` dưới cấu trúc symlink tương đối tại thư mục `filtered/` và sinh các file nhãn Ground Truth (`train_gt.json`, v.v.).

### Bước 2: Đánh giá mô hình kiểm chứng (Verify Evaluation Pipeline)
Để đảm bảo toàn bộ pipeline hoạt động bình thường, bạn có thể chạy thử nghiệm đánh giá bằng file dự đoán giả lập (mock predictions) đi kèm:

```bash
python3 test-pipeline/evaluate.py \
  -p test-pipeline/mock_predictions.json \
  -g filtered/private_test_gt.json \
  -o test-pipeline/mock_metrics.json
```

**Tham số dòng lệnh của `evaluate.py`**:
* `-p`, `--predictions`: Đường dẫn file dự đoán JSON của mô hình AI (Bắt buộc).
* `-g`, `--ground-truth`: Đường dẫn file nhãn thực tế Ground Truth JSON (Bắt buộc).
* `-o`, `--output`: Đường dẫn lưu file kết quả chỉ số metrics (Mặc định: `metrics.json`).
* `-t`, `--threshold`: Ngưỡng nhị phân hóa điểm số (Mặc định: `0.5` hoặc cấu hình trong `.env`).
* `-s`, `--schemes`: Đường dẫn file schema JSON để validate I/O (Mặc định: `test-pipeline/SCHEMES.json`).

---

## CẤU TRÚC THƯ MỤC DỰ ÁN

```text
dataset/
├── .env                          # File cấu hình tập trung (Google Drive IDs, Seeds, Paths)
├── README.md                     # Tài liệu hướng dẫn này
├── setup_dataset.py              # Tập lệnh cài đặt và giải nén tự động E2E
├── compress/                     # Nơi chứa các file ZIP tải về (RE2-OB.zip, RE3-OB.zip)
├── raw/                          # Thư mục chứa dữ liệu thô sau khi giải nén
│   ├── RE2-OB/                   # 90 runs lỗi tài nguyên & mạng Online Boutique
│   └── RE3-OB/                   # 30 runs lỗi logic code Online Boutique
├── filtered/                     # Các splits dữ liệu (chứa symlink tương đối đến thư mục raw)
│   ├── train/                    # 60 runs huấn luyện (RE2: 44, RE3: 16)
│   ├── val/                      # 15 runs kiểm thử (RE2: 11, RE3: 4)
│   ├── public_test/              # 16 runs test công khai (RE2: 12, RE3: 4)
│   ├── private_test/             # 29 runs test ẩn (RE2: 23, RE3: 6)
│   ├── train_gt.json             # Nhãn Ground Truth tập huấn luyện
│   ├── val_gt.json               # Nhãn Ground Truth tập kiểm thử
│   ├── public_test_gt.json       # Nhãn Ground Truth test công khai
│   └── private_test_gt.json      # Nhãn Ground Truth test ẩn
└── test-pipeline/                # Mã nguồn kiểm thử và schemas kiểm tra định dạng
    ├── SCHEMES.json              # File JSON Schema định nghĩa định dạng I/O chuẩn
    ├── utils.py                  # Các hàm tiện ích dùng chung
    ├── split_dataset.py          # Tập lệnh chia tập dữ liệu (stratified split)
    └── evaluate.py               # Tập lệnh đánh giá kết quả (PR-AUC, F1, Precision, Recall)
```

---

## CẤU HÌNH HỆ THỐNG (`.env`)

Mọi đường dẫn, tham số ngẫu nhiên (random seed) hay ID tải về đều được quản lý tập trung tại file `.env` ở thư mục gốc:

```env
# Google Drive IDs của các tệp nén Online Boutique
GDRIVE_ID_RE3_OB=1cZpnaZ1ijLUBssXzCnbGVWsT1NlnXtoy
GDRIVE_ID_RE2_OB=12VpUPNx_ZWebA-cICyKmQmXjF3KpLJpP

# Tham số ngẫu nhiên giúp tái lập kết quả phân chia (seed-reproducible)
RANDOM_SEED=42

# Ngưỡng nhị phân hóa điểm số mặc định cho đánh giá
DEFAULT_THRESHOLD=0.5

# Cấu hình các đường dẫn thư mục làm việc
COMPRESS_DIR=compress
RAW_DIR=raw
FILTERED_DIR=filtered
SCHEMES_PATH=test-pipeline/SCHEMES.json
```

---

## CÁC LƯU Ý KHI PHÁT TRIỂN & CHẠY OFFLINE
1. **Chèn Tenant ID**: Dữ liệu thô CSV không chứa thông tin tenant. CDO Preprocessor khi chạy offline sẽ tự động chèn trường `"tenant_id": "tnt-re2-simulation"` hoặc `"tnt-re3-simulation"` vào payload.
2. **Quy chuẩn tên Signal**: Toàn bộ tín hiệu khi đóng gói gửi sang AI Engine phải được chuẩn hóa từ định dạng thô của CSV sang snake_case tiêu chuẩn Prometheus (ví dụ: `adservice_container-memory...` $\rightarrow$ `container_memory_working_set_bytes` kèm label `service: "adservice"`).
3. **Cô lập kiểm thử (Unseen Faults)**: Lỗi trên **`currencyservice`** hoàn toàn không xuất hiện ở tập `train` và `val`. Nó chỉ nằm ở tập `test` để kiểm tra độ nhạy của mô hình đối với dịch vụ lỗi chưa từng gặp.
