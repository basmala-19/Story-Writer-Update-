<!-- Module 3: Story Idea (S3) — split from Design Interactive Lesson Stories.md (v2). Shared rules in 00_shared.md -->

> Module: Module 3: Story Idea (S3)
> Shared conventions, API, data model, config, errors and tests: see `00_shared.md`.

### S3 Story Idea (large LLM, plus code)

1. **Retrieve idea memory:** the latest 40 `one_liner` values with the same (country, language, age\_group, subject), plus the latest 20 with the same (country, language, age\_group) and any subject.
2. **Generate exactly 3 ideas.** Each has: `id` (i1 to i3), `logline` (at most 40 words), `setting`, `protagonist_archetype` (a short phrase), `central_problem` (a concrete everyday problem the protagonist wants solved), `concept_moments[]` (`{concept_id, moment}` where `moment` is at most 30 words and shows how the concept becomes the tool that solves part of the problem), `tone` (at most 6 words), `fun_factor` (at most 25 words), `one_liner` (neutral summary of at most 25 words, used for memory), and optional `constraints` (machine-checkable requirements; see schema below).
3. **Rules.** The concept is a tool the characters need, never a lesson being taught. The main device is not a teacher giving a lecture (a parent or teacher may appear briefly). Everyday realistic events, proportionate emotions, no exaggerated screaming, crying or gags, no cruel villains. Every `must` concept appears in `concept_moments`. Names, places and currency come from the localization brief. The 3 ideas differ in setting and problem. Each idea can be told in the target duration with at most 12 scenes.
4. **Code checks.** Similarity: build the token set of each `one_liner` after `norm_ar` and stopword removal. Jaccard similarity with every memory item must be below 0.5, else drop that idea. At least one idea must survive and every `must` concept must be covered by `concept_moments`.
5. **Idea Critic** (large LLM) scores each surviving idea from 1 to 5 on `fun_for_age`, `concept_as_tool`, `originality`, `cultural_fit`, `fits_duration` and `restraint`. Select the idea with the highest mean among those where every score is at least 3.5 (a tie goes to higher `concept_as_tool`). If none qualifies, regenerate (3 attempts in total).

Artifact `idea.json`: `ideas`, `selected_id`, `scores`. Memory is written at publish (S12).

`Idea.constraints` is optional and contains only explicit requirements that code can check without interpreting story meaning:

```json
{
  "required_child_count": 4,
  "required_participants": ["ليلى", "عمر"],
  "concept_evidence": [{"concept_id": "c1", "required_terms": ["..."], "required_any_forms": ["..."]}]
}
```

The count is a minimum number of distinct named child characters who appear in story lines. Named participants must be present in the cast and story. `concept_evidence` is reserved for literal terms/forms explicitly required by the lesson; semantic understanding and safety are judged by the Critic, not string matching. Do not infer constraints from free-form prose.

---

## I/O Contract and Test Sample (Module 3: S3)

### Input

```json
{
  "intake": "<intake.json>",
  "concepts": "<concepts.json (c1, c2 both must)>",
  "brief": "<brief.json>",
  "memory": {
    "same_subject": ["<latest 40 one_liner values for (country, language, age_group, subject)>"],
    "any_subject":  ["<latest 20 one_liner values for (country, language, age_group)>"]
  },
  "feedback": null
}
```

For the first story in an empty database, both memory lists are empty.

### Output `idea.json`

```json
{
  "ideas": [
    {
      "id": "i1",
      "logline": "نادية وأخوها كريم يريدان تقسيم بيتزا واحدة بعدل بين أربعة أولاد، وتكتشف نادية أن الأجزاء يجب أن تكون متساوية.",
      "setting": "مطبخ البيت في المساء",
      "protagonist_archetype": "طفلة دقيقة تحب الترتيب",
      "central_problem": "تقسيم بيتزا بين أربعة أولاد دون خلاف",
      "concept_moments": [
        {"concept_id": "c1", "moment": "كريم يقص قطعًا غير متساوية فيغضب أحد الأولاد، فتعيد نادية القص بأجزاء متساوية."},
        {"concept_id": "c2", "moment": "تكتب نادية ما أخذه كل واحد: أربعة أجزاء كلها، وتأخذ جزءًا واحدًا."}
      ],
      "tone": "دافئ ومرح هادئ",
      "fun_factor": "كريم يحاول إقناع الجميع بأن قطعته الصغيرة أطعم.",
      "one_liner": "طفلة تقسم بيتزا بالتساوي بين أربعة أولاد لتنهي خلافهم."
    },
    {
      "id": "i2",
      "logline": "فريق مدرسي يبيع كعكة في سوق خيري ويحتاج أن يقسمها بالتساوي ويكتب على اللافتة نصيب كل زبون.",
      "setting": "سوق خيري في فناء المدرسة",
      "protagonist_archetype": "طفل مسؤول عن اللافتة",
      "central_problem": "كتابة نصيب كل زبون على اللافتة بشكل صحيح",
      "concept_moments": [
        {"concept_id": "c1", "moment": "الفريق يقطع الكعكة أجزاء متساوية ليبيع كل جزء بسعر واحد."},
        {"concept_id": "c2", "moment": "الطفل يكتب على اللافتة العدد الأعلى والأسفل ويشرح معناهما لزبون."}
      ],
      "tone": "نشيط وودود",
      "fun_factor": "زبون فضولي يسأل عن كل رقم على اللافتة.",
      "one_liner": "فريق مدرسي يقطع كعكة أجزاء متساوية ويكتب نصيب كل زبون على لافتة."
    },
    {
      "id": "i3",
      "logline": "ليلى تساعد جدها في توزيع شريط قماش بالتساوي على أربع لوحات لمسرحية المدرسة.",
      "setting": "ورشة صغيرة في بيت الجد",
      "protagonist_archetype": "حفيدة تحب الأشغال اليدوية",
      "central_problem": "تقسيم شريط قماش إلى أجزاء متساوية لأربع لوحات",
      "concept_moments": [
        {"concept_id": "c1", "moment": "ليلى تطوي الشريط حتى تتساوى الأجزاء قبل القص."},
        {"concept_id": "c2", "moment": "تكتب على كل جزء أي مقام تقصد وكم جزءًا استخدمت."}
      ],
      "tone": "هادئ وحنون",
      "fun_factor": "الجد يخطئ عمدًا في القص لتصححه ليلى.",
      "one_liner": "حفيدة تقسم شريط قماش أجزاء متساوية مع جدها لتزيين لوحات مسرحية."
    }
  ],
  "selected_id": "i1",
  "scores": {
    "i1": {"fun_for_age": 4.5, "concept_as_tool": 4.5, "originality": 3.5, "cultural_fit": 4.5, "fits_duration": 4.5, "restraint": 4.5},
    "i2": {"fun_for_age": 4.0, "concept_as_tool": 4.0, "originality": 4.5, "cultural_fit": 4.0, "fits_duration": 4.0, "restraint": 4.5},
    "i3": {"fun_for_age": 3.5, "concept_as_tool": 4.0, "originality": 4.5, "cultural_fit": 4.0, "fits_duration": 4.0, "restraint": 4.5}
  }
}
```

Mean scores: i1 4.33, i2 4.17, i3 4.08. All three have every score at least 3.5, so all are eligible, and i1 has the highest mean. (For a tie-break test, set i2's scores so its mean equals i1's; the higher `concept_as_tool` wins.)

### Test sample (`tests/unit/test_s3_idea.py`)

```python
def test_s3_golden_selects_i1():
    out = s3_idea.run(intake=..., concepts=..., brief=..., memory={"same_subject": [], "any_subject": []},
                      llm=FakeLLM("s3_ideas.json", critic="s3_critic.json"))
    assert len(out.ideas) == 3 and out.selected_id == "i1"
    must = {c.id for c in concepts if c.weight == "must"}
    sel = next(i for i in out.ideas if i.id == out.selected_id)
    assert {m.concept_id for m in sel.concept_moments} >= must
    assert all(len(i.one_liner.split()) <= 25 for i in out.ideas)

def test_memory_blocks_duplicate():
    mem = {"same_subject": ["طفلة تقسم بيتزا بالتساوي بين أربعة أولاد لتنهي خلافهم."], "any_subject": []}
    out = s3_idea.run(..., memory=mem, ...)
    assert "i1" not in [i.id for i in out.ideas]          # Jaccard 1.0 >= 0.5, dropped
    assert out.selected_id in ("i2", "i3")
```

| Case | Setup | Expected |
| --- | --- | --- |
| Memory hit | memory contains i1's exact `one_liner` | i1 dropped; i2 or i3 selected |
| Dissimilar memory | memory: "ولد يقيس طول سريره بمسطرة" | nothing dropped |
| All similar | memory contains all three one_liners | no survivor, regenerate; after 3 attempts the version is `needs_review` (`VALIDATION_EXHAUSTED`) |
| Must concept uncovered | i1 `concept_moments` lacks `c2` | i1 dropped (code check) |
| Low score | i1 has `restraint: 3.0` | i1 not eligible even with the highest mean; next eligible idea selected |
| No eligible idea | every idea has one score under 3.5 | regenerate (max 3 attempts) |
| Two ideas generated | LLM returns 2 ideas | schema failure (exactly 3 required) |
| Memory write timing | stage passes | **no** `idea_memory` row yet; it appears only after S12 publish |
| Jaccard unit | `jaccard("a b c", "a b d")` | 0.5 (threshold is strict "< 0.5", so exactly 0.5 is dropped) |
