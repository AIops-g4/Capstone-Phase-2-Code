# Fault type improvements — 1A + 1B (detect only)

> **Date:** 2026-06-24  
> **Merged to:** `main` (folder `tf-3/ai/ai-engine/detect_decide`, not branch `feat/e2e_merge`)  
> **File changed:** `src/correlation_analyzer.py`  
> **Docs:** `BARO-FAULT-TYPE-FIX.md` (Cách 1), this file (1A + 1B)  
> **Not changed:** `self_healer.py`, decide / `trigger_metric` (2E **không** áp dụng)

---

## Bối cảnh

E2E benchmark (`scripts/benchmark_e2e.py`) cho thấy:

| Giai đoạn | Fault type | Runbook E2E | Runbook oracle |
|-----------|------------|-------------|----------------|
| BARO hardcode `cpu` | ~16.7% | ~16.7% | 100% |
| **Cách 1** — BARO rank metric | ~26.7% | ~26.7% | 100% |
| **1A + 1B** (hiện tại) | **40.0%** | **40.0%** | **100%** |
| 1A + 1B + **2E** (đã thử, **đã hoàn tác**) | 40.0% | **12.2%** | **12.2%** |

- **Oracle 100%** → decide / `SelfHealer` / `FAULT_RUNBOOK_MAPPING` đúng.
- Nút thắt chính: **RCA gán sai `suspected_fault_type`** → decide chọn sai runbook.
- **2E** (decide ưu tiên `trigger_metric`) làm oracle tụt vì benchmark vẫn truyền `trigger_metric` từ detect, ghi đè ground truth → **không merge**.

---

## Cách 1 — BARO rank metric (đã có trước 1A/1B)

Xem `BARO-FAULT-TYPE-FIX.md`.

Sau khi BARO chọn `best_service`, duyệt `ranks` → metric đầu tiên thuộc service đó → `_map_metric_to_service_fault()`.

---

## 1A — Sửa map tên cột RE2

**Vấn đề:** Tên cột RE2 như `checkoutservice_diskio`, `productcatalogservice_latency-50` bị map sai, nhiều case rơi về `cpu`.

**Thay đổi:** `_map_metric_to_service_fault()` trong `correlation_analyzer.py`:

1. Match **service prefix dài nhất** từ `SERVICES_LIST` (vd. `productcatalogservice`, không cắt nhầm).
2. Map suffix metric:

| Pattern trong suffix | `suspected_fault_type` |
|----------------------|-------------------------|
| `socket` | `socket` |
| `diskio`, `disk_io`, `disk*` | `disk` |
| `mem` | `mem` |
| `latency`, `delay` | `delay` |
| `error`, `loss`, `packet` | `loss` |
| `cpu` | `cpu` |
| khác | `cpu` (default) |

**Ví dụ:** `checkoutservice_diskio` → (`checkoutservice`, `disk`).

---

## 1B — Fault từ Z-score mạnh nhất trên `best_service`

**Vấn đề:** BARO rank đôi khi không trùng metric thật sự spike khi inject fault.

**Thay đổi:**

### BARO path

1. Sau khi có `best_service`, gọi `_infer_fault_for_service(df_metrics, anomaly_idx, best_service)`.
2. Tính Z-score từng metric của service đó quanh `anomaly_idx` (`_z_scores_at_anomaly`).
3. Chọn cột **Z-score cao nhất** → map qua 1A → `suspected_fault_type`.
4. Nếu Z-score ≤ `RCA_ZSCORE_THRESHOLD` → **fallback** Cách 1 (BARO rank).

### Default RCA path (Pearson + Z-score)

Sau khi chọn `best_service`, lấy metric max Z-score trên service đó → map fault (thay vì hardcode `cpu`).

### Helpers mới

- `_z_scores_at_anomaly()` — max |Z| per column trong cửa sổ anomaly.
- `_infer_fault_for_service()` — trả `(fault_type, metric_column, z_score)`.

---

## Không đổi (cố ý)

| Thành phần | Lý do |
|------------|--------|
| `self_healer.py` | Decide vẫn dùng `suspected_fault_type` từ detect |
| `trigger_metric` trong decide | 2E gây regression oracle; chỉ dùng trong API response (`engine.py`) |
| `benchmark_e2e.py` offline chain | Không truyền `trigger_metric` vào decide |

---

## Kết quả benchmark (90 runs, BARO, top-k=3)

```
Anomaly detection rate:      100.0% (90/90)
Service Top-1 accuracy:      83.3%
Service Top-3 accuracy:      95.6%
Macro-F1:                    0.878 (pass ≥ 0.85)
Fault type accuracy:         40.0%
Runbook accuracy (E2E):      40.0% (36/90)
Runbook accuracy (oracle):   100.0%
Full pipeline success:       35.6%
```

Chạy lại:

```powershell
cd tf-3\ai\ai-engine\detect_decide
python scripts\benchmark_e2e.py --sample-size 90 --engine baro --top-k 3
```

---

## Undo

```powershell
cd tf-3\ai\ai-engine\detect_decide
git checkout HEAD -- src/correlation_analyzer.py
```

Hoặc nhắn agent: **"undo 1A 1B"**.

---

## Tóm tắt pipeline

```
Metrics + logs
    → Anomaly detect (100%)
    → BARO: best_service (~83% Top-1)
    → 1B: max Z-score metric on best_service
    → 1A: map column name → fault type (40% fault accuracy)
    → Decide: suspected_fault_type → runbook (oracle 100%)
```

**1A** = dịch tên cột → loại lỗi.  
**1B** = chọn cột nào spike thật trên service đã chọn.
