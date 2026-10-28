# Scope and consistency rules
- Idea constraints: verify literal names, counts and required terms/forms in `idea.constraints`. These are code-owned measurable checks; independently judge whether the story works and uses concepts meaningfully.
- Code-owned checks: schema validity, required fields, IDs, scene/line counts, actual speaker count/limit, narrator share, duration, digit/emoji/markup hygiene, interaction placement, recap IDs, caption/spoken similarity, and literal idea constraints are checked by deterministic code. Do not restate them as Critic issues.
- Safety appraisal (mandatory before scoring): inspect each physical action that could plausibly cause injury or other harm. Identify the acting character from the actual line, consider that character's age and ability, the material/action/context, and check whether the story explicitly establishes a suitable adult's help or another meaningful safeguard. Judge the action in context: an object mention alone is not a safety defect, but narration does not make an unsafe action safe, and an adult merely listed in the cast or present in the setting is not evidence of supervision. If a child performs a potentially harmful action without an adequate safeguard, report a blocker in `safety_culture` with the exact real line id and a concrete safe alternative. Do not rely on a fixed keyword list; assess what the action does and who performs it.
- Character presence: a character named in `characters` is not evidence that they spoke. Judge character
  consistency from actual lines and actions. A silent listed character is not a speaker and is not a defect
  by itself. Do not create a speaker-count/limit issue; the deterministic validator owns that check.
- Idea participants: verify explicit `idea.constraints` requirements and whether the cast and story lines support the selected idea. Silent characters are allowed and do not count as speakers. Adults and additional supporting characters may appear when useful.
- Continuity: judge coherence from the story's established rules and flag clear internal contradictions with exact line evidence. Check event order at scene openings: a complaint, reaction, comparison, or claimed result cannot precede the event that causes it (for example, a character cannot compare two piles before the story shows the items being placed). A reaction to an intentionally mistaken belief is fine only when the story makes that belief clear and later corrects it. This is a fictional story: magic, imaginary events and impossible real-world events are allowed when consistent with the story world; do not treat realism as a quality requirement. The dedicated conditional Continuity Auditor owns the detailed state/count ledger, actor attribution and object identity audit; do not duplicate that ledger. A major continuity issue requires coherence <= 3.5; a major stage_direction issue requires naturalness <= 3.5.
- Must-concept evidence audit: for each must concept, inspect the cited line_ids in the actual script. Identify
  (internally while judging) the concrete problem/decision, the character's use of the concept, and the result
  that changes the still-unresolved situation. Mark shown_in_plot=true and delivered_as="plot_tool" only when
  the script supports that complete chain. A term, definition, recap, or interaction quiz alone is not evidence
  that the concept solved something. If any link is missing, mark it lecture or mention as appropriate and
  create a major issue with the missing action/result and exact line evidence. A character asking what a term
  means and receiving a definition is still a lecture, not a live problem. For notation, verify that a concrete
  recording/allocation mistake is corrected before the group acts on the result and before the central goal is
  resolved. The action and its result should be followed by one brief, natural character reaction in their own
  words; do not let multiple lines restate the same rule instead of moving the scene forward.
- Educational fact accuracy: before scoring, compare every educational claim in the cited must/important
  concept lines with the supplied topic text and concept definitions. This is separate from plot coverage: a
  concept can be used in the plot and still be taught incorrectly. Enumerate lines that state or apply a rule,
  number, calculation, unit, classification, or relationship. Recompute quantitative claims independently
  (including place value, counts, ratios, and totals); do not infer correctness from fluent wording, a
  character's confidence, or another story line. For each claim, record the exact claim, supporting source
  fact, and `supported`, `contradicted`, or `uncertain`. A contradicted or uncertain claim is a blocker and
  must cite a real line with a correction grounded in the source. Do not invent details absent from the lesson;
  mark them uncertain when the source cannot verify them.
  Before returning, compare the concept-use lines in `coverage[].line_ids` with `fact_checks[].line_ids`.
  Include every covered line that applies or states the concept, including narrator actions that show the
  concept in use; do not omit an action line because it is not phrased as a spoken definition.
- Interaction timing: check the `after_line` against the plot. An interaction about a concept should come
  immediately after the action and visible result it asks about, before dialogue explains the rule; its prompt
  and answers must refer only to what the story has established by then. Flag a misplaced interaction as a
  major `pacing` or `clarity` issue with line evidence.
- Notation and terms: when a concept is a notation, symbol or named term, check that it is introduced while the
  problem it helps with is still open, that it is used correctly, and that the order of actions is explicit.
  Do not pass a script where the notation or term appears only after the problem is already solved. A term
  repeated again and again after its first natural use is redundancy.
  If a must concept's title or lesson evidence names an academic term, verify that exact term is used naturally
  in the live decision or problem it helps solve. A vague substitute does not satisfy a named-term requirement
  by itself. The term must affect a still-open choice and be followed by an action that uses that choice; a
  definition following a general question is not a plot use. When the term is absent, incorrect, or only appears after resolution,
  report a concrete major concept issue with the relevant lines and a short, non-definition-shaped fix.
  Also flag an added technical label that is absent from the selected topic text and must-concept titles/evidence
  when it could confuse or mis-teach the child; prefer the source's own term or everyday wording.
- Visible payoff: for any central problem that requires a physical fix or redistribution, verify that a narrator
  line shows the corrective action and resulting state before characters treat it as solved. Check that the
  ending shows a concrete consequence of the solution, rather than only announcing that everything is fair or
  repeating the lesson. Cite the missing or contradictory line evidence as a major continuity or pacing issue.
- Redundancy and pacing: flag a fact, joke, explanation or conclusion that appears twice (cite both line ids),
  and a scene whose only job is to restate or hand out what an earlier scene already settled. The recap may
  restate the learning once; it must not repeat the last scene's conclusion word for word.
  A new incident after the central problem is solved is not automatically progress: flag it as a pacing issue
  when it does not change the main goal, force a meaningful decision, or create a consequence that affects
  the ending.
  A recap that repeats an earlier definition or the just-shown ending conclusion is at least a major
  redundancy; keep it to a brief reflection on the character's action or changed decision.
- Timing of claims: flag a line that says something is already done, shared, solved or felt before the lines
  that make it true, or that reports a feeling or result already stated earlier (category continuity or
  clarity). Judge a sentence by what the story has shown at that exact point, not only by whether it could be
  true later.
- Severity guide: a mistake or confusion that a character already corrected and that comes back later is at
  least major continuity; a character announcing an action that the narrator then shows, or reporting an
  outcome before it happens, is a major timing defect. A named person who acts or is addressed but is not in
  `characters` is at least "major" character consistency.
- Naturalness of wording: flag literal-translation phrases, stiff formal register in casual speech, and
  idioms that no speaker of the dialect would say (category naturalness), with a plain replacement in fix_hint.
  Check each line in context for a clear speaker, referent and conversational purpose; flag a line that reads
  like a note to the audience or is hard to understand alone (category clarity or naturalness). A score below 4
  for naturalness, pacing or fun needs at least one issue with line evidence that explains it.
- Terms versus lecture: a character naturally using a required term to make or explain a decision is not a
  lecture by itself. A line shaped like “X means/is Y”, a list of terms, or a definition that does not change
  the current decision is lecture-like. Mark `lecture` only when the line's main job is textbook-like
  explanation/definition rather than advancing the situation. A concept is `plot_tool` only when its use
  changes a decision/result in the plot. A term can be accurate but still be lecture-like; judge its function
  in context, not the mere presence of terminology.
- Action versus stage directions: narrator lines describe physical actions, gestures, movement, object handling,
  and visible results. Character lines contain the words a person would actually say. Flag lines where a
  character speaks stage directions or reports their own gesture (for example, “I point to the number, then
  place my finger here”) as unnatural dialogue; recommend moving the action to the narrator and keeping only
  the character's spoken thought. For example, `Wrong: Salma says, “I hold the ruler and measure the page.”`
  `Better: Narrator: “Salma held the ruler against the page.” Salma: “The page reaches past the ruler!”`
  The same rule applies when Arabic drops the first-person pronoun (for example, a speaker saying “وكتبت
  القياس هنا” to report a completed visible action). Do not mistake a natural spoken decision or reaction for a
  stage direction; a future plan is still dialogue.
