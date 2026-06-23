# RCAEval RE3 Dataset Setup & Evaluation Pipeline

This repository contains the dataset setup script, dataset splitting logic, and the evaluation pipeline for the **RE3 (Code-Level Faults)** benchmark from the paper: 
*"Pham - 2025 - RCAEval A Benchmark for Root Cause Analysis of Microservice Systems with Telemetry Data"*.

---


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

## 1. Project Directory Structure

```text
dataset/
├── .env                          # Centralized configuration (IDs, paths, seeds, thresholds)
├── README.md                     # This documentation file
├── setup_dataset.py              # Download and extraction automation script
├── compress/                     # Downloaded ZIP archives (RE3-OB.zip, RE3-SS.zip, RE3-TT.zip)
├── raw/                          # Unzipped raw telemetry data grouped by microservice system
│   ├── RE3-OB/                   # Online Boutique cases
│   ├── RE3-SS/                   # Sock Shop cases
│   └── RE3-TT/                   # Train Ticket cases
├── filtered/                     # Symbolic links representing dataset splits and Ground Truths
│   ├── train/                    # Train split directories
│   ├── val/                      # Validation split directories
│   ├── public_test/              # Public test split directories
│   ├── private_test/             # Private test split directories
│   ├── train_gt.json             # Train ground truth labels & active services
│   ├── val_gt.json               # Val ground truth labels & active services
│   ├── public_test_gt.json       # Public test ground truth labels & active services
│   └── private_test_gt.json      # Private test ground truth labels & active services
└── test-pipeline/                # Pipeline codes and schemas
    ├── SCHEMES.json              # Conformance schemas for validation
    ├── utils.py                  # Shared helpers (dotenv loader)
    ├── split_dataset.py          # Stratified data splitter (seed-reproducible)
    └── evaluate.py               # Evaluation calculator (F1, Precision, Recall, PR-AUC)
```

---

## 2. Installation & Prerequisites

Make sure you use the `capstone` Conda environment and install the required dependencies:

```bash
# Activate conda environment
conda activate capstone

# Install required packages
pip install gdown jsonschema
```

---

## 3. Configuration (`.env`)

All configurations, file IDs, and directories are centralized in the `.env` file at the root of the workspace.

```env
# Google Drive File IDs for RE3 dataset zips
GDRIVE_ID_OB=1cZpnaZ1ijLUBssXzCnbGVWsT1NlnXtoy
GDRIVE_ID_SS=1sLrZFyJi-5Q1oEIN8ERuapIJIx1KFTEV
GDRIVE_ID_TT=1SRB4kTNRWtSIJAp96kz1iMEHY2ToytVG

# Dataset Splitting Hyperparameters
RANDOM_SEED=42

# Evaluation Pipeline Hyperparameters
DEFAULT_THRESHOLD=0.5

# Workspace Folder Paths
COMPRESS_DIR=compress
RAW_DIR=raw
FILTERED_DIR=filtered
SCHEMES_PATH=test-pipeline/SCHEMES.json
```

---

## 4. How to Run the Pipeline

### Step 1: Download & Extract Dataset
Execute the setup script from the root directory. This downloads the zip files via `gdown`, extracts them safely to `raw/`, and automatically triggers `split_dataset.py`:

```bash
python3 setup_dataset.py
```

### Step 2: Split the Dataset (Manual Trigger)
If you modify `.env` (e.g., change `RANDOM_SEED`) and want to rebuild splits without redownloading:

```bash
python3 test-pipeline/split_dataset.py
```

### Step 3: Run Evaluation
To compare your model predictions against the `private_test` ground truth, execute:

```bash
python3 test-pipeline/evaluate.py \
  --predictions path/to/your_predictions.json \
  --ground-truth filtered/private_test_gt.json \
  --output test-pipeline/metrics.json
```

**Options**:
* `-p`, `--predictions`: Path to predictions JSON (Required).
* `-g`, `--ground-truth`: Path to ground truth JSON (Required).
* `-o`, `--output`: Path to write metrics JSON output (Default: `metrics.json`).
* `-t`, `--threshold`: Float threshold to binarize scores (Default: Loaded from `.env` or `0.5`).
* `-s`, `--schemes`: Path to validation schema file (Default: Loaded from `.env` or `test-pipeline/SCHEMES.json`).

---

## 5. Schema Specifications (`SCHEMES.json`)

To ensure standard interfaces, all inputs and outputs of the evaluation pipeline conform to the JSON schemas defined in `test-pipeline/SCHEMES.json`.

### A. Predictions Schema (INPUT)
Your RCA model must output predictions matching this format. It maps each case ID to the predicted anomaly scores for active services.

```json
{
  "RE3-OB/adservice_f3/1": {
    "adservice": 0.95,
    "frontend": 0.12,
    "emailservice": 0.02
  },
  "RE3-SS/carts_f4/2": {
    "carts": 0.88,
    "payment": 0.45
  }
}
```
* **Constraint**: Score values must be floats between `0.0` and `1.0`.

### B. Ground Truth Schema (INPUT)
Automatically generated by `split_dataset.py`. Each case lists the root cause service, the fault type injected, the system, and candidate services active during the run.

```json
{
  "RE3-OB/adservice_f3/1": {
    "root_cause_service": "adservice",
    "fault_type": "f3",
    "system": "OB",
    "candidate_services": [
      "adservice",
      "cartservice",
      "checkoutservice",
      "currencyservice",
      "emailservice",
      "frontend",
      "paymentservice",
      "productcatalogservice",
      "recommendationservice",
      "redis",
      "shippingservice"
    ]
  }
}
```

### C. Metrics Schema (OUTPUT)
Generated by `evaluate.py`. Reports both threshold-based binarized metrics, Top-1 prediction metrics (AC@1 simulation), and overall PR-AUC (Average Precision).

```json
{
  "evaluation_mode": "private_test",
  "num_cases": 18,
  "threshold_based_metrics": {
    "threshold": 0.5,
    "precision": 0.8571,
    "recall": 0.6667,
    "f1_score": 0.7500
  },
  "top1_based_metrics": {
    "precision": 0.8889,
    "recall": 0.8889,
    "f1_score": 0.8889
  },
  "pr_auc": 0.6361
}
```
