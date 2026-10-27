# S4 Story Writer + Critic: evaluation protocol

## Purpose

Separate structural correctness from story quality, and avoid changing prompts merely to make the
Critic pass. Keep inputs, model/provider settings, prompts, raw outputs, validation results, latency, and
usage/cost together for every measured run.

## Evaluation layers

1. **Code checks (deterministic):** schema, IDs, scene/line counts, actual speakers, spoken-text hygiene,
   duration, concept IDs/coverage, interaction placement, recap, and display/spoken similarity. These are
   not delegated to the LLM judge.
2. **Critic checks (model-graded):** nine scores, must/important concept coverage, explanatory-line ratio,
   and actionable issues. The Critic must count only line-level speakers and apply the 20% explanatory
   line definition in `s4_critic.md`.
3. **Human review (calibration):** review a fixed sample of scripts and label each issue as blocker/major/minor,
   whether each must concept is a plot tool, and whether explanatory lines were counted consistently.

## Initial regression set

Keep these fixed inputs unchanged when comparing a prompt/model revision:

- **i1 — pizza sharing:** fraction is a part of an equal whole; numerator/denominator; fair shares; misconception
  about denominator; continuity of cutting and allocation; character-count edge case with a silent named child.
- **i2 — school cake/charity market:** alternate plot using the same lesson; check whether changing the idea
  actually improves story quality, whether all intended children receive a clear share, and whether dialogue
  assigns roles consistently.
- **Defect variants:** same script with (a) a listed silent character, (b) one extra actual speaker, (c) a line that
  defines the concept like a textbook, (d) a concept term used naturally during a decision, (e) an action described
  as unnatural character dialogue, and (f) a malformed `emotion` value. Each variant should have a human-checked
  expected result.

Do not silently edit the golden input while comparing versions. Store new failures as new cases rather than
replacing old cases.

## Pass criteria

- All deterministic blockers pass.
- Every must concept is used as a plot tool before final resolution; mere mention/definition is insufficient.
- Critic scores: each dimension >= 4.0 and mean >= 4.2, with no blocker/major issue.
- Critic explanatory-line ratio <= 0.20.
- Important concept coverage >= 80%.
- A human reviewer checks every newly introduced failure and a sample of passing outputs before treating a
  prompt/model change as an improvement. A score increase alone is not sufficient.

## Experiment procedure

1. Run `uv run pytest -q` before any live LLM call.
2. Save a baseline of i1 and i2 including prompt files/version, model, temperature, token limits, outputs,
   code checks, Critic report, latency and token/cost data if available.
3. Change one factor at a time. Use identical inputs and settings for before/after comparisons.
4. Repeat the same model/input at least three times when estimating judge or generation stability; report each
   result, not only the best result.
5. Manually label at least 20 critic judgments before tuning thresholds. Compare human and model labels using
   percent agreement and Cohen's kappa when there are enough labels/classes to make it meaningful.
6. Do not run expensive generation experiments until tests, API configuration, and token budget are confirmed.
7. Keep a change only when it improves human-reviewed quality without regressions in hard checks.

## Known limits

The current i1/i2 cases are a small diagnostic set, not statistically representative. Three repeats are useful
for detecting instability but do not prove general quality. Expand the set with varied ages, languages, durations,
concept types, ambiguous inputs, and unanswerable/unsupported lesson cases before making broad claims.

## Continuity repair hardening (2026-10-26)

- The Writer prompt now requires a chronological state/count audit before returning a script: state before an
  action, action, resulting state, remaining-count arithmetic, explicit recipient allocations, and causal order.
- The Critic prompt explicitly checks those same continuity properties and is asked to anchor defects to exact
  scene/line IDs with a corrected state/order in `fix_hint`.
- When a Critic issue mentions chronology, counts, pieces, or distribution, retry feedback adds a targeted repair
  checklist and preserves the cited issue locations. The existing repair strategy still asks the Writer to make
  the smallest edits and preserve unrelated working lines.
- Three regression tests cover targeted continuity feedback, avoiding irrelevant continuity hints, and inclusion
  of the state/count audit in repair prompts.

**Important limitation:** narrative arithmetic is not yet a fully deterministic code check. The current script
schema stores free-form dialogue and scene summaries, not a structured event/state ledger. Automatically asserting
that a line such as “three pieces remain” is correct would require reliable extraction of actions and quantities;
a naive keyword/number matcher would create false positives across languages and inflections. Until the schema
contains explicit state transitions, those semantic contradictions remain Critic/human-review checks. Do not
claim this patch mathematically proves all story continuity.


## Prompt consistency hardening (2026-10-27)

- Clarified the writer's dialogue/action boundary: physical actions and visible results belong in narrator lines; character lines should contain actual speech.
- Reworded required terminology guidance so terms appear inside a story decision rather than a definition-shaped sentence. This resolves the tension between “no lecturing” and “say the terms.”
- Made humor guidance non-forcing: a natural funny beat is preferred, but a confusing joke is worse than warm, clear interaction.
- Narrowed the Critic's remit away from deterministic structural checks, especially speaker-limit claims, because code already owns those checks and a false major issue can reject an otherwise valid attempt. The Critic remains responsible for semantic character consistency and story quality.

These changes improve prompt consistency but do not prove semantic quality. Prompt-contract tests only verify that the instructions remain present; quality claims still require fixed-input outputs and human-reviewed evaluation.
