# Rules
0. IDEA CONSTRAINTS: obey explicit `idea.constraints`. Include each `required_participants` name exactly in `characters` and mention that same person by name in the story; never substitute a different person. Meet `required_child_count` with distinct named child characters who appear in story lines; adults do not count. Include literal `concept_evidence` terms/forms in the story text; when a required form is specified, do not assume a paraphrase satisfies it. These checks are measurable and do not replace story quality.
1. Follow the selected idea. Use each concept as a tool in the plot, as described in its concept_moments.
2. 5 to 12 scenes. Each scene has one purpose and changes something (new information, a decision, a result).
3. 3 to 9 lines per scene (every scene, including the last one). Words per line (text_display) at most the age limit. The estimated spoken
   duration must be within +/-20% of the target, where estimate = (words of text_spoken / wps) + 0.3 s per
   line, summed over all lines. Use the wps given in the input.
4. Narrator lines are at most 35% of all lines. Characters speak in the dialect and register of
   speech_style. The narrator is plain and warm.
5. NO LECTURING. No line may define a concept in textbook wording. Explanation comes from a character who
   needs to understand, in their own words, with an example from their situation. Keep explanatory lines at
   or below 25% of all lines. An explanatory line is one whose main purpose is to state or explain a rule;
   a line that advances the action and naturally mentions a concept is not automatically explanatory.
6. SHOW, DO NOT SUMMARIZE. A character uses the concept in an action, sees a result that changes the plot, then
   gives one short, natural line in their own words reacting to that result. Do not replace the action/result with
   a definition or a summary.
7. RESTRAINT. Reactions are proportionate. No shouting, no repeated exclamations, no slapstick, no sarcasm
   or mockery (strictest for ages 6-8).
8. Names, currency, places and foods come from the localization brief. No real brands, public figures,
   politics or religion as plot. Count actual speakers from the `speaker` fields in `lines`: at most
   max_speakers - 1 distinct character keys may speak, because the narrator is the extra speaker. You may
   still list silent characters in `characters` when they are part of the selected idea; they must never
   receive dialogue. If the required cast is larger than the speaking limit, keep the required cast and
   make enough supporting participants silent to meet the limit; check distinct speaker keys before returning.
   Let a silent child take part naturally through a visible action or reaction in a narrator line. Do not make
   a speaker repeatedly say what the silent child "says", "wants" or "thinks" on their behalf; show only what
   the narrator can observe, or let the other characters speak for themselves.
   Do not add characters who never appear in the story. Every named person who acts or is
    addressed in the story must be in `characters` (silent is fine); do not let an unlisted person act. Preserve
    the selected idea's essential named participants in the story. A numeric group requirement is machine-checked only when it is explicit in `idea.constraints.required_child_count`; do not infer participant counts from quantities used in the lesson. Silent participants are allowed, and adults or useful supporting characters may also appear.
9. Follow the topic's logical order. Do NOT teach later_preview content. Assume assumed_prior_knowledge.
10. Interactions: 2 to 4 per story (1 to 3 if the target duration is under 150 s). Each comes after the
    concept it checks has appeared in the plot (that concept is in concepts_covered of its scene or an
    earlier one), at least 20 seconds (estimated) after the previous one (in practice: different scenes, 8+ lines apart), and never in the first or last
    scene. after_line must be a line of the same scene. Each has a concept_id.
11. The last scene (3+ lines like any other scene) ends with a recap of 1 to 2 lines (character or narrator) restating what the character
    learned in their own words: what they expected or tried, what happened when they used the idea, what
    they will do next time. It is never a definition, a list of terms, or a repeat of the line before it.
    List their ids in recap_line_ids. Every "must" concept must appear in the
    concepts_covered of at least one scene.
12. text_display is the caption: correct punctuation, digits as required by display_digits. text_spoken is
    what the TTS reads: numbers and symbols written as words, NO digits, NO Latin letters (for Arabic),
    NO emoji, diacritics only on ambiguous words. text_spoken must stay close to text_display: copy it
    word for word and change ONLY numbers and symbols into words ("3/4" -> "ثلاثة أرباع"); never paraphrase
    or add words.
13. Dialogue versus action: `text_spoken` and `text_display` are the exact words spoken by the selected speaker, not stage directions. Physical actions, gestures, movement, object handling, and visible results belong in a narrator line. For safety, use age-appropriate judgment for the characters and setting; avoid dangerous actions or show clearly how responsible adult help keeps them safe. A character may refer to an action in natural speech, but must not narrate their own gesture as if reading stage directions (for example, do not make a character say “I point to the number and put my finger here”). This also applies to completed first-person physical actions when Arabic omits “I” (for example, a speaker saying “وكتبت القياس هنا” to report writing it). Do not use parentheses or bracketed action directions in any line. Example: WRONG: Salma says, “I hold the ruler and measure the page.” BETTER: Narrator: “Salma held the ruler against the page.” Salma: “The page reaches past the ruler!” Spoken plans and reactions are still dialogue; move only the physical action to the narrator.

14. Interactions must match the story. Use only objects, pieces and numbers that already appeared in the
    lines before `after_line`; never introduce a new quantity or item (for example half pieces when the
    story only has quarters) inside an interaction.
15. STORY ARC: match the age guidance in the input. Open with a concrete goal and a small obstacle or mistaken
    plan; let the child affected by it speak for themselves. Give each main character a clear role and voice.
    Every scene must change the situation or move the goal forward. Do not explain the lesson in the opening.
16. USE THE CONCEPTS: every must concept is a tool that changes a choice or action before the central problem
    is solved. If the concept has a misconception, let a character voice it, show the consequence, then correct it
    through an action. For notation, make a real recording or allocation mistake matter while the goal is open;
    the corrected notation must guide what the characters do next. A question followed by a definition is not use.
    Interactions ask about an action or result the child just saw, never for a definition. Put each interaction
    immediately after the action and visible result it checks, before any line that explains the rule.
17. TERMS WITHOUT A LECTURE: use each academic term required by the lesson or concept title/evidence once,
    naturally, in the decision it helps resolve. The next action shows what the term does. Do not define it, list
    terms, substitute only a vague label, or add technical terms absent from the selected lesson. Keep explanation
    within Rule 5's 25% limit; let the plot carry the meaning.
18. ACTION AND CONTINUITY: narrate physical actions and visible results before characters claim they happened.
    Establish the event before any reaction, complaint or comparison that depends on it; a character cannot
    say a pile is larger before the story shows the items being placed. Check this order at scene openings too.
    Keep the actor, object, location and quantities consistent from line to line. The narrator establishes who did
    each important action; a character must not later claim someone else's action as their own. When something
    goes wrong, show how it is fixed. Distinguish a mistaken plan from an action that already happened: if the
    plan was only proposed, make clear it was stopped before changing the scene's state; if the action happened,
    show the consequence and an explicit repair. Never erase or reset a previously shown state without an action.
    Track object identity and condition as well: a cup of water cannot become ice in a later scene without a
    narrator line showing it freeze; never silently switch an object's identity. A repair must be physically
    plausible and preserve material/counts: after cutting, breaking, using up or spilling something, do not
    imply it became whole/unused again just by gathering or pressing it. Show a workable repair that fits the
    material, or clearly introduce and use a replacement; account for the altered pieces and the final result.
    STATE-AND-COUNT AUDIT: before returning JSON, trace object counts and
    shares; recalculate the starting count minus items actually taken and simplify unclear arithmetic. When
    repairing feedback, fix the cited issue and preserve unrelated lines that work.
19. NATURAL VOICE: use the input's speech style and words a child of this age would actually say. Keep dialogue
    short, direct and between characters, not to the audience. Avoid textbook questions, moral slogans, meta lines
    and literal translations (except the required recap). Every line must be understandable in its story context
    and sound like something that character would say. In simple MSA, prefer common spoken-like phrasing over formal constructions. For
    ages 6-8 use one simple concrete problem; for 9-11 use clear cause and effect with a mild challenge; for
    ages 12-14 use relatable social situations and light irony; for ages 15-18 use realistic stakes and subtle
    humor, never a childish tone.
20. WARMTH AND HUMOR: when the idea offers a natural humor hook, use one small, kind beat that grows from a
    character's plan or misunderstanding and lands without mockery. Do not force a joke or interrupt the plot.
21. SAFETY: actions must be appropriate for the characters’ ages and setting. Do not depict dangerous conduct as harmless. Prefer having a named adult character perform a hazardous action in a narrator line; if needed, add an adult as a silent supporting character so the speaker count stays unchanged. If that action is essential to the plot, keep the event, the child's decision and the learning result; transfer only the hazardous handling to the adult. The adult must not take over the problem-solving or teaching role. If the action is not essential, replace it with a clearly harmless action that preserves the story result. Never use the hazard line itself as proof of a safeguard, and never imply help merely because an adult is present or listed in the cast.
22. ENDING: show the solution being used and one concrete consequence. Do not add an unrelated incident or repeat
    settled material. Finish with the required 1-2 line recap in the character's own words about their action or
    changed choice, not a definition or a repeat of the ending.

# Final check
Run one whole-script check for the hard constraints above and preserve every unrelated line that already works.
