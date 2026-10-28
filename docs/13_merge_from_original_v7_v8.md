# Combined project: original + V7 Fixed + V8 Repair Trace

This package starts from the original `story_writer-main.zip` layout, overlays the maintained V7 Fixed implementation, then adds the V8 targeted regression and repair-trace changes.

Included useful V7 layers: input preflight, scene-scoped repair and repair memory, continuity/safety/educational-accuracy review, strict evidence validation, usage/auditor observability, and regression fixtures. V8 adds the i2 targeted regression and opt-in raw/processed plus repair-feedback traces. Existing prompts, thresholds, and retry policy are those of V8 Repair Trace; this merge does not introduce new prompt or threshold changes.

Preserved from the original archive: `s4_pipeline_review.ipynb`, and its `test.json` sample moved to `tests/fixtures/s4/original_test_input.json`. The original files remain available alongside the V7/V8 tests and accepted examples.

No `.env`, API keys, Python environments, or cache directories are included.
