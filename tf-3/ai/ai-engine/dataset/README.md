# Shared dataset (detect + decide)

Large files are gitignored. After clone:

```powershell
cd tf-3/ai/ai-engine/decide
python scripts/setup_dataset.py
```

Or see `detect/README.md` for manual RE2/RE3 download.

Committed here (small metadata only):
- `ground_truth.json` — run labels for benchmark/eval
- `runbooks.json` — runbook catalog

Not in git: `RE3-OB/`, `*.zip`, raw `*.csv` telemetry.
