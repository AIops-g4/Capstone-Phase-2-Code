## Brief overview

- Project-specific guidelines for keeping `ai/ai-engine` benchmark stages clean, consistent, and reproducible.
- These rules apply when editing environment variables, benchmark datasets, formatting code, and running stage benchmarks.

## Environment defaults

- Treat BOCPD and BARO as the default algorithm stack for detect-stage work.
- Remove or avoid unused environment toggles for alternative detect algorithms when they are no longer part of the active benchmark stage.
- Keep `.env` files minimal and stage-specific; only include variables that are actually read by the current stage code.

## Dataset and benchmark assets

- Prefer one shared benchmark data location under `ai/ai-engine/dataset/` for JSON metadata and reusable benchmark assets.
- Keep small deterministic files such as `ground_truth.json`, `runbooks.json`, dependency graphs, and benchmark request fixtures together when they are reused across stages.
- When moving dataset or JSON files, update all code paths and scripts instead of duplicating stale copies across stage folders.

## Code style consistency

- Format Python code consistently across `detect`, `detect_decide`, and `detect_decide_verify` before benchmarking.
- Prefer readable, modular files over large mixed endpoint implementations.
- Keep import ordering, whitespace, and function layout consistent enough that stage diffs are easy to review.

## Benchmark workflow

- Run benchmarks by stage in this order when requested:
  - detect benchmark for `/v1/detect` anomaly detection and RCA.
  - detect_decide benchmark for `/v1/detect` + `/v1/decide` action-plan generation.
  - detect_decide_verify benchmark for full E2E `/v1/detect` + `/v1/decide` + `/v1/verify`.
- Report benchmark commands, pass/fail status, and important runtime blockers such as missing datasets or dependencies.
- Do not hide failed benchmark results; summarize the failure and the next concrete fix.