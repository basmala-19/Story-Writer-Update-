# S4 general prompt fixes — 2026-10-27

## Scope
This patch addresses reusable instruction conflicts in the S4 Writer/Critic. It does not change story-specific content, model/provider settings, scoring thresholds, or advisory-mode behavior.

## Changes
1. **Dialogue vs physical action:** writer instructions now explicitly put observable physical actions and results in narrator lines, while character lines remain spoken dialogue.
2. **Required terminology vs lecturing:** the writer must use required terms in a decision or response that advances the current problem, not as a definition-shaped sentence or term list.
3. **Humor quality:** the prompt prefers a natural, kind funny beat but explicitly prioritizes clear, warm interaction over a forced/confusing joke.
4. **Critic responsibility boundaries:** deterministic checks (including actual speaker count/limit) are explicitly owned by code. The Critic should focus on semantic quality and should not invent duplicate structural issues.
5. **Regression coverage:** tests check that these prompt contracts remain in place.

## Validation performed in this environment
- `python -m compileall -q src tests` passed.
- `python -m pytest tests/unit/test_s4_script.py tests/unit/test_usage_log.py -q`: 95 passed.
- The full suite could not be collected here because this environment cannot download the locked `google-genai` dependency (network/DNS unavailable). Run `uv sync --extra dev` and `uv run pytest -q` in the user's configured environment before adopting the patch.

## Known limits
Prompt text tests only verify instruction presence, not that a model obeys the instructions. A quality claim requires comparing the same fixed inputs/model settings and human-reviewing outputs. No live LLM calls were made for this patch.

---

# V6 — topic-neutral fixes (2026-10-27)

V5 fixed the pizza story with pizza-specific text (character names in a keyword list, "four pieces, one taken
leaves three", fraction notation advice). V6 removes all of it and fixes the same defect classes for any story.

## What changed
1. **Critic issues carry a `category`** (`continuity, redundancy, lecture, naturalness, stage_direction, clarity,
   character, concept, pacing, humor, safety_culture, other`). Retry feedback picks a generic repair guide per
   category (`CATEGORY_REPAIR`) instead of guessing from keywords (the old marker list matched words like
   "before", "after", "number" and a character's name). Issues without a category get no guessed guide.
2. **New deterministic checks** (major): `no_stage_markup` (parentheses/brackets/asterisks in a line) and
   `near_duplicate_lines` (two non-recap lines of >= 6 tokens with >= 0.80 token similarity).
3. **`critic_evidence`** (major): a must concept marked `plot_tool` must cite at least one line that exists.
4. **Writer prompt**: topic examples removed; duplicate dialogue/action wording merged (rules 13/15/19); a short
   "self-review" block (repeats, timing of claims, natural wording, one reading, scene purpose); required terms are
   said once, when needed.
5. **Critic prompt**: categories, English issue text, redundancy / timing-of-claims / naturalness checks, and a rule
   that a score below 4 for naturalness, pacing or fun must be backed by a cited line.
6. **Acceptance**: `run_with_outcome()` returns `(script, S4Outcome)`; status is `accepted` or `needs_review`
   (advisory fallback). `run()` still returns only the script. `run_s4.py` exits with code 2 on `needs_review`.
   Failed attempts are ranked by severity (blockers > must-concept failure > major issues > other failed rules >
   mean score). When attempts run out without advisory mode, `ValidationExhausted.last_script` is the best
   attempt, not just the last one.
7. Schema errors print the rejected value (e.g. the bad `emotion`).

## Not changed (deliberately)
Thresholds (min score 4.0, mean 4.2), model/provider, advisory flag semantics. `CRITIC_MAX_EXPLANATORY` is 0.20 in
V5 while the Module 4 spec says 0.25: decide which is intended and update the other.

## Validation
`python -m pytest -q tests` -> 110 passed (offline, FakeLLM). No live LLM run was made, so no quality claim:
compare i1/i2 before/after with the protocol in `S4_EVALUATION_PROTOCOL.md`.

## V6.1 — findings from the first real V6 run
- `emotion` values outside the allowed list are mapped to the nearest allowed one (unknown -> neutral) before
  validation; the changes are listed in `code_validation["normalized"]`. Attempt 1 of the run was lost to `relieved`.
- Writer budget states the narrator cap in lines (12 of 36). Attempt 2 failed at 39%.
- New minor check `interaction_answer_leak`: the correct answer must not already be in the 3 lines before it.
- Critic severity guide: a corrected confusion that returns, a repeated explanation, a confusing joke and an unlisted
  person who acts are at least major. Writer: a named actor must be in `characters`; a character does not announce
  what the narrator is about to show.
- Critic default output budget 8000 tokens, automatic retry on truncation.
- Regression fixture: `tests/fixtures/s4/real_run_v6_attempt3_script.json`.
