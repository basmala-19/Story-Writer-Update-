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


## Evaluation consistency and cost controls (2026-10-27)
- Removed the hidden second Critic call for borderline 3.5 scores. The unchanged per-dimension and mean thresholds apply to every score; scores below 4.0 always fail. Every sub-threshold score needs a category-matched issue that cites a real line. Evidence improves auditability and never waives a threshold.
- Safety is a semantic Critic judgment in `safety_culture`; deterministic code validates that issue references point to real lines. No tool-name/keyword safety blocker is used.
- Idea requirements live under `idea.constraints` in the Module 3 schema. Code checks only explicit measurable counts, names and literal required terms/forms; it does not parse free-form idea prose for hidden requirements.
- The explanatory-line limit follows S4 spec 04 at 25%; code and active evaluation docs match. The separate 0.20 constant is the duration tolerance.
- The continuity audit runs for an otherwise passing candidate, when the Critic reports a continuity issue, or when the only Critic blocker is missing fact-check line coverage (where the independent accuracy supplement also runs). It is not charged on every failed draft.
- Prompts use explicit local includes so role instructions can be maintained as focused modules. Detailed state/count ledgers belong to the conditional Continuity Auditor; the Critic retains concise coherence checks. Prompt hashes and comparison artifacts include every transitive prompt module.
- The comparison script reports repeat results and provenance. Expensive live comparisons require a confirmed model/provider and token budget; no benchmark result is implied by the script's existence.

## Safety audit (2026-10-28)
- Evidence behind it: in the 24-run comparison (`reports/20261027_203334_0a2ed3`) the assistant review found children using a
  knife/scissors without an adult in both versions, and the Critic passed those stories. Moving safety to the Critic alone
  (previous entry) removed the keyword blocker but did not replace its protection.
- A dedicated Safety Auditor (`prompts/s4_safety.md`, `SafetyReport`) lists every hazardous action as
  `{line_id, handler, hazard, safeguard, safeguard_line_id}`. It never decides pass/fail and it uses no word list.
- Code (`evaluate_safety`) decides: an adult handler (from the script's own cast ages) passes; `adult_helping` needs a real line
  in the same scene within 2 lines that is spoken by, or names, an adult character; `child_safe_tool` needs a real line in the same
  scene; `none`, a missing/invented citation, an unknown or `unspecified` handler (treated as a child) fail as a blocker.
  Entries citing a line that does not exist are ignored, like unverifiable continuity issues.
- The auditor runs on every code-valid draft by default (`S4_SAFETY_AUDIT=1`); `0` disables it. Its risk tier is
  contextual: a category label alone does not make an action a blocker. Unsafeguarded minor risks are warnings;
  serious risks and incomplete/unavailable audits remain blocking.
- It fails CLOSED. A truncated or schema-broken audit (after retries) records `safety_audit_unavailable` (major) and the draft is
  not accepted. Known cost: that failure cites no scene, so the next attempt may rewrite the whole script even though the draft
  was not unsafe. Provider outages already stop the run earlier (`ProviderFatalError`).
- Limits: the auditor is still an LLM. Code verifies that its citations are real and that the claimed adult exists in the cast; it
  cannot verify that a `child_safe_tool` line really describes a harmless tool. Not yet measured on real runs.

## Safety audit: risk tiers, early feedback, recap guidance (V6.5)

Matrix evidence (gemini-3.5-flash-lite, 4 cases): with the safety audit on, acceptance was 0/4; the
two audit rejections appeared on attempt 3, after attempts 1-2 had been spent on code checks, so the
writer never had a chance to repair them. Reading the cited lines: one was a child holding a knife
with no adult present (correct rejection); the other was a child cutting fabric with scissors while an
adult only commented from across the room (arguable).

- **Risk tiers.** Each hazard now carries `risk`: `serious` (an injury that needs care, or a line that
  itself stresses the danger) or `minor` (an ordinary task a child of that age does alone, worst case
  a scratch). Code blocks only unsafeguarded `serious` hazards; unsafeguarded `minor` ones become a
  `safety_minor` warning that is still shown to the writer. The auditor judges by action, material and
  the handler's age, never by object/category alone; code does not automatically promote a risk by category.
  No word list was added. A missing `risk` means `serious`,
  and "when unsure, use serious" is in the prompt, so the change can only relax cases the auditor
  explicitly rates minor.
- **Early safety.** A schema-valid draft that failed code checks is also audited (`S4_SAFETY_EARLY=0`
  switches this off). Real findings are fed back with the code failures; an audit that could not run
  adds nothing, because the draft is being rejected anyway. Cost: one capped call (`S4_SAFETY_MAX_TOKENS`)
  per code-failed attempt.
- **Recap guidance.** Three of the four blocking Critic failures in the matrix were the recap line
  restating a definition. Writer rule 11 now describes the recap as the character's own experience (what
  they expected or tried, what happened, what they will do next time), and a blocker/major issue on a
  `recap_line_ids` line adds a recap-specific repair guide.
- **Speaker limit.** The check data now lists each speaker's line ids; the repair hint names the lines to
  rewrite or hand to an allowed speaker, next to the character to silence.

Not changed: acceptance thresholds, fail-closed behaviour of the safety audit for code-valid drafts,
the 0.25 explanatory-line limit. Not done: calibration of the auditor against human-labelled stories
(needs a labelled set), and detection of third-person narration by characters whose line does not name
the speaker (the Critic's `stage_direction` category covers it semantically).

## Independent educational accuracy audit

- The Critic now records source-based factual checks, but a Critic's self-report alone is not sufficient
  evidence that lesson facts are correct. Add a separate `Educational Accuracy Auditor` call after Critic
  acceptance, with no Writer feedback or Critic scores in its input.
- The auditor must enumerate concept-linked educational claims, cite real script lines, quote the lesson or
  concept source verbatim, independently recompute quantitative relationships, and return `supported`,
  `contradicted`, or `uncertain` with reasoning. Code verifies real line IDs, source-quote presence, required
  concept coverage, and coverage of lines the Critic identified.
- Contradicted, uncertain, incomplete, malformed, or unavailable audits fail closed and feed a source-based
  repair into the remaining S4 attempts. If the Critic's only blocking failure is that it omitted some
  educational line IDs from its fact-check inventory, the independent auditor reviews every concept-linked
  line in Critic coverage (plus any explicitly cited fact-check lines). A complete, source-supported audit
  can fill that evidence gap; it cannot override a contradicted/uncertain Critic claim, invalid reference,
  missing concept review, or any other Critic failure. The original Critic check records that it was
  supplemented. No score or acceptance threshold is relaxed. This adds one model call only when a draft
  otherwise passes the Critic or has this evidence-omission-only failure; default cap is 8,000 tokens.
  `S4_FACT_ACCURACY_AUDIT=0` is a documented
  opt-out that must not be used for educational publication.
- When all attempts fail, candidate selection compares observed blocker/major findings before code-validity;
  code-validity is a tie-breaker. This lets a later draft with one mechanical repair left outrank an earlier
  draft with several major audit defects, while avoiding a code-invalid winner when the failures are otherwise equal.
- Each repair attempt receives a compact history of every hard check seen so far, its detail, and whether
  the latest attempt passed it, still fails it, or did not recheck it. The writer is told to preserve fixed
  constraints while repairing current failures.
- Limits: semantic correctness is still model-judged; code can verify quotes and line references but cannot
  prove that an inference from the quoted source is logically valid. Human review and calibration against
  labelled examples remain necessary before claiming a zero-error system.

## Story repair refinements (2026-10-28)
- Writer guidance now requires the triggering event to be shown before a dependent reaction, complaint or comparison. Critic and continuity prompts check the same causal order.
- Continuity and safety repair feedback includes the current narrator-line budget. A premature action claim can become a plan/question; when narrator evidence is necessary, feedback prefers combining it with existing narration to avoid fixing one blocker by creating another.
- Required participants and literal concept evidence must match `idea.constraints`; a silent required child can participate through visible narrator-described actions, but another speaker should not repeatedly report that child's unseen speech or thoughts.
- Safety remains enabled by default. Severity follows the action, material, context and child's age; a category label alone does not promote a minor risk to a blocker. When a serious action is essential, feedback keeps the child's problem-solving role and transfers only the hazardous handling to an adult.
