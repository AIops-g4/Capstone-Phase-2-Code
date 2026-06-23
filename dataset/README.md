# RCAEval RE2 & RE3 Online Boutique Dataset & Evaluation Pipeline

Dự án này chứa toàn bộ mã nguồn cấu hình, tập lệnh thiết lập tự động tải/giải nén dữ liệu, tập lệnh phân chia tập dữ liệu và chạy đánh giá E2E cho hệ thống **Online Boutique (OB)** với 2 bộ dữ liệu:
* **RE2-OB**: Các lỗi tài nguyên hạ tầng và đường truyền mạng (CPU, Memory, Disk, Sockets, Delay, Packet Loss).
* **RE3-OB**: Các lỗi logic ở cấp độ code ứng dụng (Status Code, Exception, Infinite Loop, Crash).

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
