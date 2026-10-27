# DECISIONS (Module 4: S4 Story Writer and Critic)

Choices made where the spec was ambiguous or silent (spec 14.3: simplest option consistent with D1-D15).

## Scope
- Only S4 is implemented, plus the minimum shared pieces it needs: Pydantic models, `norm_ar`, prompt helpers,
  the `LLMClient` protocol and `FakeLLM`. Real LLM adapters (M1), the orchestrator, DB recording and S5+ are not here.
- `s4_writer.run()` returns the validated `Script`. After 3 failed attempts it raises `ValidationExhausted`
  (`code = "VALIDATION_EXHAUSTED"`, `.issues` in the API shape, `.last_script`). The orchestrator must turn that into
  a `needs_review` version and mark S5 onward `skipped`. `on_attempt` hands each attempt (script, code validation, critic
  report, feedback) to the orchestrator so it can write `stage_runs`. `compute_input_hash()` gives the cache key.
- Upstream inputs (intake, concepts, brief, idea) use tolerant models (`extra="allow"`) declaring only what S4 reads.
  Modules 1-3 own the full models; replace these when they land.

## Validation
- **Critic only runs when code validation passed.** A script that already fails blockers would waste a large-model call.
  The code failures are fed back to the writer instead.
- **Critic `major` issues fail the pass check**, not only `blocker` ones. Module 4 says "no blocker issue", but 5.0 / D8 say a
  stage with an unresolved major never passes. The stricter reading is used (it does not loosen anything).
- "At least 80% of `important` concepts covered" = share of important concepts whose critic coverage entry has
  `shown_in_plot: true`. No important concepts = vacuously satisfied.
- Malformed critic JSON gets one extra critic call; if still invalid the attempt fails with a `critic_schema` (major) check.
- Speaker limit: distinct non-narrator speakers <= `max_speakers - 1` (rule 8 and the mutation table).
- Words per line are counted on `text_display` (the caption), as in the spec's test. `text_spoken` expands digits into words.
- Interaction time = estimated end time of its `after_line` (cumulative `words / wps + 0.3 s` over all earlier lines).
  Interaction count is 2-4 for targets >= 150 s and 1-3 below (D7). `after_line` must be a line of the interaction's own scene,
  and the checked concept must be in `concepts_covered` of that scene or an earlier one.
- Digit detection uses Unicode `\d`, so Arabic-Indic digits in `text_spoken` are also blockers.
- Added blocker `no_markup`: any string with HTML-tag-like text (`<script`, `</x>`, `<!`) fails. A bare `<` as in "3 < 4" is allowed.
  This enforces "the LLM never writes JavaScript" on the script itself.
- Added minor `display_digit_style`: Arabic captions should follow `DISPLAY_DIGITS`.
- `emotion` is limited to the expression names of the character kit (Module 5): neutral, happy, surprised, worried, thinking,
  laughing. The key `narrator` is reserved and cannot be a character key.
- Lesson text is wrapped by `wrap_lesson_text`, which also neutralises `<lesson_text>` / `</lesson_text>` inside the text so it
  cannot close the block early.

## Tests / fixtures
- The golden fixture (`tests/fixtures/llm/s4_script.json`) is a full 5-scene, 27-line script (estimated 131 s, 3 interactions:
  `tap_reveal` in s02, `choose_answer` in s03, `drag_match` in s04). It follows the spec's excerpt, with adjustments: the pizza is
  only *marked* in s02 so that it can be re-divided in s03, the four friends have their own big pizza (the brothers have a second
  small one), and the drag_match sits after `s04_l05`.
- Spec mutation "Delete all lines of s03 -> c1/c2 no longer in concepts_covered": in the golden, s04/s05 also cover the concepts, so
  deleting one scene's lines cannot remove them from the script. Two tests cover it: deleting the lines of s03 fails
  `lines_per_scene`; deleting s03 and stripping `c2` from every other scene fails `must_concepts_covered`.
- "Two interactions 10 s apart" is built by moving `s02_i1` to the end of s02 and `s03_i1` to `s03_l01` (about 5 s apart).
- `FakeLLM` routes by the schema's `ROUTE` attribute (`Script` -> `writer`, `CriticReport` -> `critic`), accepts a list per route
  (one entry per call, last repeats) and records calls in `.calls`.
- `brief.json` fixture satisfies the S1 minimum counts (20 names per gender, 8+ places, 6+ foods, 3+ transport, 4+ holidays).
