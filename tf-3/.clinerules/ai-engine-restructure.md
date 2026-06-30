## Brief overview

- Project-specific guidelines for restructuring and developing the AIOps AI Engine under `ai/ai-engine`.
- The AI Engine should be organized to support step-by-step benchmarking of the self-healing loop, rather than mixing all endpoint capabilities into one implementation.
- The primary target structure is three benchmark stages:
  - `detect`: only anomaly detection and RCA.
  - `detect_decide`: detection plus action-plan decision.
  - `detect_decide_verify`: full detect, decide, and verify workflow.

## AI Engine structure

- Keep the top-level AI Engine workspace centered around staged endpoint capability:
  - `ai/ai-engine/detect/` for the `/v1/detect` benchmark stage.
  - `ai/ai-engine/detect_decide/` for `/v1/detect` + `/v1/decide` benchmarking.
  - `ai/ai-engine/detect_decide_verify/` for `/v1/detect` + `/v1/decide` + `/v1/verify` end-to-end benchmarking.
- Avoid treating older mixed implementations as the source of truth without checking whether they belong to one of the stages above.
- Shared small metadata such as `ground_truth.json` and `runbooks.json` can remain in `ai/ai-engine/dataset/` when it helps keep benchmark inputs consistent across stages.

## Endpoint staging rules

- In `detect`, expose and benchmark only the detection/RCA capability; do not include decision or verification endpoint logic unless explicitly requested.
- In `detect_decide`, include detection and decision logic, including runbook selection/action-plan generation, but avoid verification concerns unless explicitly required for compatibility tests.
- In `detect_decide_verify`, include the complete closed-loop flow: detect, decide, and verify.
- When moving code between stages, keep each stage runnable and testable on its own.

## Detect-stage algorithm preference

- For the `detect` endpoint/stage, remove or avoid alternative anomaly/RCA methods when restructuring unless the user asks to preserve them.
- The intended detect-only method stack is:
  - BOCPD for anomaly/change-point detection.
  - BARO for root-cause analysis.
- Avoid retaining Isolation Forest, EWMA, RRCF, or other detection methods in the `detect` stage after the restructure, except as temporary migration code clearly marked for removal.

## Contract alignment

- Keep implementation aligned with `ai/contracts/ai-api-contract.md`, `ai/contracts/telemetry-contract.md`, and `ai/contracts/deployment-contract.md`.
- AI Engine should remain the decision-making component only; do not add code that directly mutates Kubernetes resources.
- Prefer returning action plans for CDO/executor components to apply.
- When changing endpoint schemas, verify against the frozen contract before making breaking changes.

## Benchmarking workflow

- Structure scripts and tests so each stage can be benchmarked independently:
  - detect-only benchmark for anomaly detection/RCA accuracy and latency.
  - detect+decide benchmark for runbook/action-plan quality and latency.
  - detect+decide+verify benchmark for end-to-end self-healing loop behavior.
- Keep benchmark scripts named clearly for their stage, for example `benchmark_detect.py`, `benchmark_decide.py`, or `benchmark_e2e.py` when applicable.
- Prefer deterministic shared datasets and ground truth labels so metrics are comparable across stages.

## Communication and implementation style

- Communicate plans and analysis in Vietnamese when the user asks in Vietnamese.
- Before large restructures, summarize the intended folder moves, deletion scope, and compatibility risks.
- Do not delete legacy code or alternative algorithms without first confirming the intended stage and benchmark impact, unless the user has explicitly requested removal.
- For large codebase changes, proceed incrementally and keep each stage working before moving to the next.