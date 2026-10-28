# S4 — semantic regression and auditor observability

## Scope

This change adds local regression coverage and measurements only. It does not change Writer/Critic prompts, scoring thresholds, normal pipeline call policy, or audit enablement defaults.

## Semantic regression fixtures

`test_semantic_regression_cases_feed_auditor_evidence_into_repair` covers four missing repair-handling cases using fixed Critic reports:

- event/quantity contradiction (`continuity`)
- semantic redundancy (`redundancy`)
- didactic dialogue (`lecture`)
- scene-summary/event mismatch (`clarity`)

These fixtures prove that reported defects are routed into repair feedback and that a subsequent passing report is handled correctly. They do **not** prove the live model will independently detect these defects. Detection still needs a small human-reviewed live sample.

Existing tests for near-duplicate lines, speaker/character consistency, required participants, and concept-as-plot-tool remain the coverage for those existing checks; this change does not duplicate them.

## Auditor metrics

Each attempt can carry an `auditor_metrics` object for the Critic, continuity auditor, safety auditor, early safety audit, and educational-accuracy auditor when invoked. Fields include `duration_ms`, observed `llm_calls`, `tokens_in`, `tokens_out`, `issues_found`, line/category issue keys, and cost status.

- Missing token usage is `null`, not `0`.
- `estimated_cost_usd` remains `null` unless a dated price for the exact provider/model is configured. No price table is configured by this change.
- Token budget enforcement remains separate from reported usage. If usage is unavailable, the existing conservative estimated reservation is used for the budget; it is not written as actual token usage.
- Calls/responses visible to the client are counted, including JSON-repair and truncation calls that return a response. Provider SDK transport retries that are internal to the SDK are not exposed by the current interface and may not be counted exactly.
- `auditor_issue_overlap` is an approximation based on exact `(line_id, category)` keys. It is not proof that two reports refer to the same semantic defect.

Old JSONL run records remain readable; new fields are additive.

## Validation boundary

Offline tests use fixed fixtures/FakeLLM and do not call an external LLM API. Run a small human-reviewed live sample before making claims about detection quality or changing any prompts, thresholds, or audit policies.

## Follow-up regression coverage

`tests/unit/test_s4_regression_v8.py` adds a human-authored candidate regression from the i2 story: the ending line `s06_l06` may restate the learning point already made in `s06_l02`. The test verifies the evidence lines exist and that a reported issue scopes repair to the affected scene plus its continuity neighbour. This is a candidate for human review, not a deterministic assertion that semantic redundancy has been proven.

Auditor metrics now separate `reported_issue_count`, `validation_rejections`, `failed_validation_check_count`, and the failed-check details. `issues_found` is an operational aggregate of reported entries plus failed validation checks; it may double-count a defect reflected in both and must not be interpreted as semantic unique defects. Existing token-budget behavior, prompts, acceptance thresholds, and normal auditor call policy are unchanged.

## Optional repair feedback trace

Set `S4_RUN_LOG_REPAIR_DETAILS=true` to include `repair_feedback_for_next`, `repair_scope`, and a
compact `repair_validation_snapshot` (failed and blocker/major checks, with structured check data)
in each attempt row. This is disabled by default because feedback can contain lesson/story text.
It does not store the full prompt and does not alter prompts, thresholds, or retry behavior. The
existing `scope` field remains available in ordinary attempt logs; `repair_scope` is included in
the opt-in trace beside the feedback and validation snapshot for easier per-retry inspection.
