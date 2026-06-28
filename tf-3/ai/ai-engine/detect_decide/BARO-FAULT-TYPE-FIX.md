# BARO fault-type inference fix (Cách 1)

> **Date:** 2026-06-27  
> **File changed:** `src/correlation_analyzer.py`  
> **Scope:** BARO RCA path only (default Pearson+Z-score path unchanged)

---

## Vấn đề

`benchmark_e2e.py` cho thấy:

| Metric | Trước fix |
|--------|-----------|
| Service Top-1 | ~83% |
| **Fault type** | **~16.7%** |
| Runbook E2E | **~16.7%** |
| Runbook oracle | 100% |

- **Oracle 100%** → decide / `SelfHealer` / `FAULT_RUNBOOK_MAPPING` **đúng**.
- **E2E thấp** → RCA (detect) gần như luôn trả `suspected_fault_type = "cpu"` → decide luôn chọn `CPUSaturationRecoveryRunbook`.

Nguyên nhân trong BARO path (`RootCauseAnalyzer.analyze()`):

```python
# TRƯỚC (hardcode)
suspected_fault_type = "cpu"
```

Helper `_map_metric_to_service_fault()` **đã có** (đọc tên cột metric: `*_mem` → `mem`, `*_latency` → `delay`, …) nhưng **không được dùng** khi gán fault.

---

## Thay đổi

### File: `src/correlation_analyzer.py` — BARO branch

**Sau khi BARO xếp hạng metrics (`ranks`) và chọn `best_service`:**

1. Duyệt `ranks` theo thứ tự BARO.
2. Lấy **metric đầu tiên** thuộc `best_service`.
3. Gọi `_map_metric_to_service_fault(metric)` → `suspected_fault_type` (`cpu`, `mem`, `delay`, …).
4. Nếu không khớp service nào → fallback map từ `ranks[0]`, cuối cùng `"cpu"`.
5. Cập nhật `reasoning` để ghi metric nguồn (debug / Jira).

```python
# SAU (infer từ BARO metric rank)
for ranked_metric in ranks:
    svc, fault = self._map_metric_to_service_fault(ranked_metric)
    if svc == best_service:
        suspected_fault_type = fault
        top_metric = ranked_metric
        break
```

### Không đổi

- Default RCA path (Pearson + Z-score) — vẫn default `cpu` ở path đó (Cách 2, chưa làm).
- `self_healer.py`, `runbook_catalog.py`, `benchmark_e2e.py`.
- API contract `/v1/detect`, `/v1/decide`.

---

## Mapping metric → fault (đã có sẵn)

| Pattern trong tên metric | `suspected_fault_type` |
|------------------------|-------------------------|
| `*cpu*` | `cpu` |
| `*mem*` | `mem` |
| `*latency*` | `delay` |
| `*error*` | `loss` |
| `*disk*` | `disk` |
| `*socket*` | `socket` |
| khác | `cpu` (default) |

Ví dụ: `checkoutservice_mem` → service `checkoutservice`, fault `mem`.

---

## Cách verify sau fix

```powershell
cd detect_decide

# E2E: detect → decide (metric quan trọng nhất)
python scripts\benchmark_e2e.py --sample-size 90 --engine baro --top-k 3

# Detect only (so sánh Top-1 / F1 — không đổi nhiều)
python scripts\evaluate.py --sample-size 90 --engine baro --use-bocpd --top-k 3

# Decide only (oracle — vẫn ~100%)
python scripts\benchmark_decide.py
```

Kỳ vọng:

- `fault_type_accuracy_on_detected` **tăng** (sau BARO fault fix)
- `runbook_accuracy_e2e` **tăng theo** fault type
- `runbook_accuracy_oracle_fault` **~100%** (không đổi)
- `macro_precision` / `macro_f1` **khớp** `evaluate.py` (cùng `labels=unique_true_classes`)

Report JSON: `benchmark_report_e2e.json`

---

## Ai sở hữu phần nào

| Component | Owner / ghi chú |
|-----------|------------------|
| `correlation_analyzer.py` (RCA) | Detect — Duck Huy team |
| `self_healer.py` (decide) | AIO decide |
| `benchmark_e2e.py` | E2E metric cho cả pipeline |

---

## Follow-up (chưa làm)

- **Cách 2:** default RCA path — infer fault từ `max_z_col` thay vì hardcode `cpu`.
- RE3 faults `f1`–`f5`: metric naming có thể cần rule riêng trong `_map_metric_to_service_fault`.
