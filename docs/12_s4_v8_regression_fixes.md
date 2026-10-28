# S4 V8 — targeted regression and observability fixes

## Preserved behavior

This version is based on V7 Fixed. It does not change Writer/Critic prompts, acceptance thresholds, audit enablement defaults, or the normal call policy. It preserves explicit rejection of invalid audit line references, null for missing usage, dated-price requirements for cost estimation, and the existing token-budget reservation behavior.

## Changes

1. Updated continuity-audit test fixtures to include `reviewed_interaction_ids`, matching the active report contract.
2. Updated the invalid-line-reference test to assert explicit rejection instead of the older behavior that ignored the invalid reference.
3. Added an i2 story regression fixture and tests for a candidate semantic-redundancy issue in the ending. These tests verify evidence IDs and local repair scope; they do not assert that an LLM automatically detects semantic redundancy.
4. Expanded per-auditor metrics so a validation-only rejection is visible even when the report's issue list is empty. Metrics now distinguish `reported_issue_count`, `validation_rejections`, `failed_validation_check_count`, and failed-check details. `issues_found` is their operational aggregate, not a count of unique semantic defects.

## Validation

- Offline full suite: 263 passed, using temporary import stubs for unavailable provider SDK packages. The stubs prohibit network calls; no LLM API was called.
- `compileall`: passed.
- ZIP integrity: checked after packaging.

The import stubs were created outside the project for test collection only and are not included in the archive.

5. Added opt-in raw-vs-processed attempt snapshots for debugging. Each attempt now keeps the parsed Writer output before recap filling, emotion normalization, and frozen-scene restoration. Set `S4_RUN_LOG_FULL_SCRIPTS=true` for a diagnostic run to include `writer_raw_script` and `processed_script` in each attempt row in `logs/s4_runs.jsonl`. The default remains off because these fields increase log size and can contain lesson/story content. This captures parsed JSON structures, not the provider's exact raw response bytes or prompt.
