"""Validators for stage S4.

`validate_script` implements the "Code validation (all blockers)" list of Module 4.
`evaluate_critic` implements the Story Critic "Pass" rule.
Both return `{passed, checks: [{name, passed, severity, detail}], scores?}` (spec 5.0).
"""
from __future__ import annotations

import itertools
import re
from difflib import SequenceMatcher
from typing import Any

from pydantic import ValidationError

from story_service.pipeline.models import (
    SCORE_KEYS,
    AgeSpec,
    Concepts,
    CriticReport,
    EducationalAccuracyReport,
    SafetyReport,
    Script,
    IdeaConstraints,
)
from story_service.pipeline.text_utils import estimate_line_seconds, norm_ar, word_count

MIN_SCENES, MAX_SCENES = 5, 12
MIN_LINES, MAX_LINES = 3, 9
NARRATOR_MAX_SHARE = 0.35
DURATION_TOLERANCE = 0.20
MIN_INTERACTION_GAP_SEC = 20.0
MIN_SIMILARITY = 0.6
CRITIC_MIN_SCORE = 4.0
CRITIC_MIN_MEAN = 4.2
CRITIC_MAX_EXPLANATORY = 0.25
IMPORTANT_MIN_RATIO = 0.80
NEAR_DUPLICATE_RATIO = 0.80   # token-sequence similarity of two lines that counts as the same line
NEAR_DUPLICATE_MIN_TOKENS = 6  # shorter lines ("Okay, let's go") may legitimately repeat

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]"
)
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"\d")  # Unicode-aware: also matches Arabic-Indic digits
_ARABIC_INDIC = re.compile("[٠-٩۰-۹]")
_WESTERN = re.compile("[0-9]")
_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z!]")
# Stage directions written inside a line: "(she smiles)", "[points]", "*laughs*".
_STAGE_MARKUP = re.compile(r"[()\[\]{}*]")
_PHYSICAL_ACTION_AR = re.compile(
    r"(?:أخذ|اخذ|أمسك|امسك|حمل|وضع|وضعت|مد|فتح|أغلق|اغلق|طوى|قص|قطع|"
    r"نقل|حرّك|حرك|رفع|أدار|ادار|أشار|اشار|لمس|سحب|دفع|صب|"
    r"أخذت|امسكت|حملت|وضعت|فتحت|طوت|قصت|قطعت|نقلت|رفعت|أشارت|اشارت|"
    r"مسكت|سحبت|دفعت|صبت)"
)
_PHYSICAL_ACTION_EN = re.compile(
    r"\b(?:take|takes|took|hold|holds|held|carry|carries|carried|put|puts|"
    r"place|places|placed|open|opens|opened|close|closes|closed|fold|folds|"
    r"folded|cut|cuts|move|moves|moved|lift|lifts|lifted|point|points|pointed)\b",
    re.IGNORECASE,
)
_COMPLETED_PHYSICAL_ACTIONS_AR = {
    "اخذت", "امسكت", "مسكت", "حملت", "وضعت", "مددت", "فتحت", "اغلقت", "طويت",
    "قصصت", "قطعت", "نقلت", "رفعت", "ادرت", "اشرت", "لمست", "سحبت", "دفعت",
    "صببت", "كتبت", "رسمت", "احضرت", "جمعت", "رتبت", "قسمت", "وزعت", "اعدت",
    "مسحت", "ثبتت", "التقطت", "قست", "لففت", "حركت", "ادخلت", "اخرجت",
}
_FIRST_PERSON_AR = {"انا", "نحن", "احنا"}


def _check(name: str, passed: bool, detail: str = "", severity: str = "blocker") -> dict[str, Any]:
    return {"name": name, "passed": passed, "severity": severity, "detail": detail}


def _result(checks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    passed = not any(not c["passed"] and c["severity"] in ("blocker", "major") for c in checks)
    return {"passed": passed, "checks": checks, **extra}


def failed_checks(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in result["checks"] if not c["passed"]]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _tokens_without_digits(text: str) -> list[str]:
    out = []
    for tok in norm_ar(text).split():
        tok = _DIGIT.sub("", tok)
        if tok:
            out.append(tok)
    return out


def spoken_display_ratio(display: str, spoken: str) -> float:
    """Similarity of the caption and its spoken form, ignoring spelled-out numbers.

    The caption may hold digits ("3/4") that the spoken text writes as words ("ثلاثة أرباع").
    Digits are dropped from the caption, so up to 3 extra spoken words per caption digit token
    are forgiven instead of being counted as a mismatch.
    """
    raw = norm_ar(display).split()
    a = [tok for tok in (_DIGIT.sub("", t) for t in raw) if tok]
    digit_tokens = sum(1 for t in raw if _DIGIT.search(t))
    b = _tokens_without_digits(spoken)
    if not a and not b:
        return 1.0
    matched = sum(m.size for m in SequenceMatcher(None, a, b).get_matching_blocks())
    forgiven = min(max(len(b) - matched, 0), 3 * digit_tokens)
    denom = len(a) + len(b) - forgiven
    return 2 * matched / denom if denom else 1.0


def near_duplicate_lines(script: Script) -> list[tuple[str, str, float]]:
    """Pairs of lines that say (almost) the same thing. Deterministic, language-agnostic.

    Only lines of at least NEAR_DUPLICATE_MIN_TOKENS tokens are compared, and the recap lines are
    skipped because restating the learning there is intended.
    """
    recap = set(script.recap_line_ids)
    items = []
    for scene in script.scenes:
        for line in scene.lines:
            if line.id in recap:
                continue
            toks = _tokens_without_digits(line.text_display)
            if len(toks) >= NEAR_DUPLICATE_MIN_TOKENS:
                items.append((line.id, toks))
    out = []
    for (ida, a), (idb, b) in itertools.combinations(items, 2):
        r = SequenceMatcher(None, a, b).ratio()
        if r >= NEAR_DUPLICATE_RATIO:
            out.append((ida, idb, r))
    return out


def interaction_times(script: Script, wps: dict[str, float], language: str) -> dict[str, float]:
    """Estimated start time (s) of each interaction = end of its `after_line`."""
    t, end_of_line = 0.0, {}
    for scene in script.scenes:
        for line in scene.lines:
            t += estimate_line_seconds(line.text_spoken, language, wps)
            end_of_line[line.id] = t
    return {
        s.interaction.id: end_of_line[s.interaction.after_line]
        for s in script.scenes
        if s.interaction and s.interaction.after_line in end_of_line
    }


def estimated_duration(script: Script, wps: dict[str, float], language: str) -> float:
    return sum(
        estimate_line_seconds(l.text_spoken, language, wps) for s in script.scenes for l in s.lines
    )


def _all_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_strings(v)


def _speaker_self_stage_directions(script: Script) -> list[str]:
    """Catch explicit third-person self-narration of physical action in dialogue.

    This intentionally requires both the speaker's own name and an action verb. It avoids
    flagging ordinary first-person decisions such as “I'll put it here,” which belong in dialogue.
    """
    by_key = {character.key: character.name for character in script.characters}
    problems: list[str] = []
    for scene in script.scenes:
        for line in scene.lines:
            if line.speaker == "narrator" or line.speaker not in by_key:
                continue
            name = by_key[line.speaker].strip()
            text = f"{line.text_display} {line.text_spoken}"
            named_action = bool(name) and bool(
                re.search(re.escape(name), text, re.IGNORECASE)
                and (_PHYSICAL_ACTION_AR.search(text) or _PHYSICAL_ACTION_EN.search(text))
            )
            tokens = norm_ar(text).split()
            first_person_action = any(
                token in _FIRST_PERSON_AR and any(t in _COMPLETED_PHYSICAL_ACTIONS_AR for t in tokens[i + 1:i + 5])
                for i, token in enumerate(tokens)
            )
            # Arabic often drops “I”. A completed, physical verb at the start of a spoken clause
            # (e.g. “وكتبت المقام...”) is therefore treated as self-action unless another named
            # character is explicitly the actor. Future plans such as “سأكتب” remain dialogue.
            for i, token in enumerate(tokens):
                verb = token[1:] if token.startswith(("و", "ف")) else token
                clause_start = i == 0 or token.startswith(("و", "ف")) or tokens[i - 1] == "ثم"
                other_actor = any(
                    norm_ar(other_name) in tokens[i + 1:i + 4]
                    for key, other_name in by_key.items()
                    if key != line.speaker and other_name.strip()
                )
                if clause_start and verb in _COMPLETED_PHYSICAL_ACTIONS_AR and not other_actor:
                    first_person_action = True
                    break
            if named_action or first_person_action:
                problems.append(f"{line.id}: speaker reports a physical action in dialogue")
    return problems


def _compact_ar(text: str) -> str:
    """Normalize Arabic/number strings for literal contract checks, ignoring spacing/punctuation."""
    return re.sub(r"\s+", "", norm_ar(text))


def _idea_constraints_checks(
    script: Script, constraints: IdeaConstraints, known_concepts: set[str]
) -> list[dict[str, Any]]:
    lines = [line for scene in script.scenes for line in scene.lines]
    story_text = _compact_ar(" ".join(f"{line.text_display} {line.text_spoken}" for line in lines))
    by_name = {_compact_ar(c.name): c for c in script.characters if c.name}
    speaking_keys = {line.speaker for line in lines}
    missing = []
    for name in constraints.required_participants:
        key = _compact_ar(name)
        character = by_name.get(key)
        if not character or (key not in story_text and character.key not in speaking_keys):
            missing.append(name)
    checks = []
    if constraints.required_participants:
        checks.append(_check("idea_required_participants", not missing,
                             f"missing named participants: {missing}", severity="blocker"))
    if constraints.required_child_count is not None:
        present = [c for c in script.characters if c.age_years < 18 and c.name and
                   (_compact_ar(c.name) in story_text or c.key in speaking_keys)]
        actual = len({c.key for c in present})
        check = _check("idea_required_child_count", actual >= constraints.required_child_count,
                       f"idea requires at least {constraints.required_child_count} distinct named child participants; {actual} appear",
                       severity="blocker")
        check["data"] = {"required": constraints.required_child_count, "actual": actual}
        checks.append(check)
    evidence_issues = []
    for evidence in constraints.concept_evidence:
        if evidence.concept_id not in known_concepts:
            evidence_issues.append(f"{evidence.concept_id}: unknown concept")
            continue
        missing_terms = [term for term in evidence.required_terms if _compact_ar(term) not in story_text]
        if missing_terms:
            evidence_issues.append(f"{evidence.concept_id}: missing required terms {missing_terms}")
        if evidence.required_any_forms and not any(_compact_ar(form) in story_text for form in evidence.required_any_forms):
            evidence_issues.append(f"{evidence.concept_id}: no required form appears")
    if constraints.concept_evidence:
        checks.append(_check("idea_concept_evidence", not evidence_issues,
                             "; ".join(evidence_issues[:10]), severity="blocker"))
    return checks


# --------------------------------------------------------------------------- #
# script validation
# --------------------------------------------------------------------------- #
def validate_script(
    script: Script,
    *,
    concepts: Concepts,
    age_spec: AgeSpec,
    language: str,
    wps: dict[str, float],
    display_digits: str = "western",
    idea_constraints: IdeaConstraints | dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if isinstance(idea_constraints, dict):
        idea_constraints = IdeaConstraints.model_validate(idea_constraints)
    scenes = script.scenes
    lines = [l for s in scenes for l in s.lines]
    known_concepts = set(concepts.ids())

    # scene and line counts
    checks.append(
        _check(
            "scene_count",
            MIN_SCENES <= len(scenes) <= MAX_SCENES,
            f"{len(scenes)} scenes (need {MIN_SCENES}-{MAX_SCENES})",
        )
    )
    bad = [f"{s.id}:{len(s.lines)}" for s in scenes if not MIN_LINES <= len(s.lines) <= MAX_LINES]
    checks.append(
        _check("lines_per_scene", not bad, f"scenes outside {MIN_LINES}-{MAX_LINES} lines: {bad}")
    )

    # ids unique and sequential
    seq_problems = []
    for i, s in enumerate(scenes, 1):
        if s.id != f"s{i:02d}":
            seq_problems.append(f"scene #{i} is '{s.id}', expected 's{i:02d}'")
        for j, l in enumerate(s.lines, 1):
            if l.id != f"{s.id}_l{j:02d}":
                seq_problems.append(f"line #{j} of {s.id} is '{l.id}', expected '{s.id}_l{j:02d}'")
    checks.append(_check("ids_sequential", not seq_problems, "; ".join(seq_problems[:5])))

    keys = [c.key for c in script.characters]
    dup_keys = sorted({k for k in keys if keys.count(k) > 1})
    checks.append(
        _check(
            "character_keys",
            not dup_keys and "narrator" not in keys,
            f"duplicate keys {dup_keys}" if dup_keys else "'narrator' is reserved",
        )
    )

    # speakers
    allowed = set(keys) | {"narrator"}
    unknown = sorted({l.speaker for l in lines} - allowed)
    checks.append(_check("speakers_known", not unknown, f"unknown speakers: {unknown}"))
    non_narrator = {l.speaker for l in lines if l.speaker != "narrator"}
    limit = age_spec.max_speakers - 1
    speaker_check = _check(
        "speaker_limit",
        len(non_narrator) <= limit,
        f"{len(non_narrator)} speaking characters (max {limit} plus narrator)",
    )
    if len(non_narrator) > limit:
        characters_by_key = {c.key: c for c in script.characters}
        counts = {key: sum(line.speaker == key for line in lines) for key in non_narrator}
        speaker_check["data"] = {
            "limit": limit,
            "actual": len(non_narrator),
            "speakers": [
                {"key": key,
                 "name": characters_by_key[key].name if key in characters_by_key else key,
                 "role": characters_by_key[key].role if key in characters_by_key else "supporting",
                 "line_count": counts[key],
                 "line_ids": [l.id for l in lines if l.speaker == key][:10]}
                for key in sorted(non_narrator)
            ],
        }
    checks.append(speaker_check)
    if idea_constraints is not None:
        checks.extend(_idea_constraints_checks(script, idea_constraints, known_concepts))
    self_stage_directions = _speaker_self_stage_directions(script)
    checks.append(
        _check(
            "dialogue_self_stage_direction",
            not self_stage_directions,
            "; ".join(self_stage_directions[:5]),
            severity="major",
        )
    )

    # words per line (caption text)
    too_long = [
        f"{l.id}:{word_count(l.text_display)}"
        for l in lines
        if word_count(l.text_display) > age_spec.max_words_per_line
    ]
    checks.append(
        _check(
            "words_per_line",
            not too_long,
            f"lines over {age_spec.max_words_per_line} words: {too_long}",
        )
    )

    # spoken text hygiene
    digits = [l.id for l in lines if _DIGIT.search(l.text_spoken)]
    checks.append(_check("spoken_no_digits", not digits, f"digits in text_spoken: {digits}"))
    emoji = [l.id for l in lines if _EMOJI.search(l.text_spoken) or _EMOJI.search(l.text_display)]
    checks.append(_check("no_emoji", not emoji, f"emoji in: {emoji}"))
    if language == "ar":
        latin = [l.id for l in lines if _LATIN.search(l.text_spoken)]
        checks.append(_check("spoken_no_latin", not latin, f"Latin letters in text_spoken: {latin}"))

    # LLM must never smuggle markup into the bundle
    markup = [s for s in _all_strings(script.model_dump()) if _HTML_TAG.search(s)]
    checks.append(
        _check("no_markup", not markup, f"HTML-like markup found in {len(markup)} string(s)")
    )

    # stage directions inside the spoken/caption text (writer rule: actions belong to the narrator)
    staged = [l.id for l in lines if _STAGE_MARKUP.search(l.text_display)]
    checks.append(
        _check(
            "no_stage_markup",
            not staged,
            f"parentheses/brackets/asterisks in text_display (stage directions): {staged}",
            severity="major",
        )
    )

    # the same line told twice (pacing/redundancy). Recap lines are meant to restate the learning.
    dupes = near_duplicate_lines(script)
    checks.append(
        _check(
            "near_duplicate_lines",
            not dupes,
            "near-identical lines (say it once, then move the plot on): "
            + "; ".join(f"{a} ~ {b} ({r:.2f})" for a, b, r in dupes[:4]),
            severity="major",
        )
    )

    # duration
    est = estimated_duration(script, wps, language)
    lo = age_spec.target_duration_sec * (1 - DURATION_TOLERANCE)
    hi = age_spec.target_duration_sec * (1 + DURATION_TOLERANCE)
    checks.append(
        _check(
            "duration",
            lo <= est <= hi,
            f"estimated {est:.0f}s, target {age_spec.target_duration_sec}s "
            f"(allowed {lo:.0f}-{hi:.0f}s, {100 * (est / age_spec.target_duration_sec - 1):+.0f}%)",
        )
    )

    # concepts
    covered = {c for s in scenes for c in s.concepts_covered}
    unknown_c = sorted(covered - known_concepts)
    checks.append(_check("concepts_known", not unknown_c, f"unknown concept ids: {unknown_c}"))
    missing = sorted(set(concepts.ids("must")) - covered)
    checks.append(
        _check("must_concepts_covered", not missing, f"'must' concepts not in any scene: {missing}")
    )

    # interactions
    inter = [(i, s) for i, s in enumerate(scenes) if s.interaction is not None]
    lo_n, hi_n = (2, 4) if age_spec.target_duration_sec >= 150 else (1, 3)
    checks.append(
        _check(
            "interaction_count",
            lo_n <= len(inter) <= hi_n,
            f"{len(inter)} interactions (need {lo_n}-{hi_n})",
        )
    )
    ids = [s.interaction.id for _, s in inter]
    checks.append(
        _check(
            "interaction_ids_unique",
            len(ids) == len(set(ids)),
            f"duplicate interaction ids: {sorted({i for i in ids if ids.count(i) > 1})}",
        )
    )
    pos_problems: list[str] = []
    covered_so_far: set[str] = set()
    for idx, s in enumerate(scenes):
        covered_so_far |= set(s.concepts_covered)
        it = s.interaction
        if it is None:
            continue
        if idx == 0 or idx == len(scenes) - 1:
            pos_problems.append(f"{it.id} is in the {'first' if idx == 0 else 'last'} scene")
        if it.after_line not in {l.id for l in s.lines}:
            pos_problems.append(f"{it.id}.after_line '{it.after_line}' is not a line of {s.id}")
        if it.concept_id not in known_concepts:
            pos_problems.append(f"{it.id}.concept_id '{it.concept_id}' is unknown")
        elif it.concept_id not in covered_so_far:
            pos_problems.append(
                f"{it.id} checks {it.concept_id} before it appeared in the plot"
            )
    checks.append(_check("interaction_position", not pos_problems, "; ".join(pos_problems)))

    # the correct answer must not already be on screen word for word
    leaks = []
    by_line = {l.id: l for sc in scenes for l in sc.lines}
    order = [l.id for sc in scenes for l in sc.lines]
    for sc in scenes:
        it = sc.interaction
        if it is None or it.after_line not in by_line:
            continue
        correct = []
        if it.type == "choose_answer":
            correct = [o.text for o in it.options if o.correct]
        elif it.type == "predict":
            correct = [o.text for o in it.options if o.id == it.expected_option_id]
        pos = order.index(it.after_line)
        recent = " ".join(norm_ar(by_line[i].text_display) for i in order[max(0, pos - 2): pos + 1])
        for text in correct:
            n = norm_ar(text)
            if len(n) >= 3 and (len(n.split()) >= 2 or _DIGIT.search(n)) and n in recent:
                leaks.append(f"{it.id}: correct answer '{text}' is already stated in the last lines")
    checks.append(
        _check(
            "interaction_answer_leak", not leaks, "; ".join(leaks), severity="minor"
        )
    )

    times = interaction_times(script, wps, language)
    ordered = [times[i] for i in ids if i in times]
    gaps = [b - a for a, b in itertools.pairwise(ordered)]
    short = [f"{g:.0f}s" for g in gaps if g < MIN_INTERACTION_GAP_SEC]
    checks.append(
        _check(
            "interaction_gap",
            not short,
            f"interactions closer than {MIN_INTERACTION_GAP_SEC:.0f}s: {short}",
        )
    )

    # recap
    last_ids = {l.id for l in scenes[-1].lines} if scenes else set()
    recap = script.recap_line_ids
    checks.append(
        _check(
            "recap",
            1 <= len(recap) <= 2 and set(recap) <= last_ids,
            f"recap_line_ids={recap}; must be 1-2 ids from the last scene {sorted(last_ids)}",
        )
    )

    # narrator share
    n_narr = sum(l.speaker == "narrator" for l in lines)
    share = (n_narr / len(lines)) if lines else 0.0
    cap = int(NARRATOR_MAX_SHARE * len(lines) + 1e-9)
    chk = _check(
        "narrator_share",
        share <= NARRATOR_MAX_SHARE,
        f"narrator share {share:.0%} (max {NARRATOR_MAX_SHARE:.0%}): {n_narr} of {len(lines)} "
        f"lines are narrator lines, at most {cap} allowed",
    )
    chk["data"] = {"narrator": n_narr, "total": len(lines), "cap": cap}
    checks.append(chk)

    # spoken vs display similarity
    low = [
        f"{l.id}:{r:.2f}"
        for l in lines
        if (r := spoken_display_ratio(l.text_display, l.text_spoken)) < MIN_SIMILARITY
    ]
    by_id = {l.id: l for l in lines}
    shown = "; ".join(
        f"{i.split(':')[0]} display='{by_id[i.split(':')[0]].text_display[:70]}' "
        f"spoken='{by_id[i.split(':')[0]].text_spoken[:70]}'"
        for i in low[:3]
    )
    checks.append(
        _check(
            "spoken_display_similarity",
            not low,
            f"text_spoken too different from text_display (min {MIN_SIMILARITY}): {low}"
            + (f". {shown}" if shown else ""),
        )
    )

    # display digit style (minor, Arabic only)
    if language == "ar":
        wrong = _WESTERN if display_digits == "arabic_indic" else _ARABIC_INDIC
        offenders = [l.id for l in lines if wrong.search(l.text_display)]
        checks.append(
            _check(
                "display_digit_style",
                not offenders,
                f"text_display should use {display_digits} digits: {offenders}",
                severity="minor",
            )
        )

    return _result(checks, estimated_duration_sec=round(est, 1))


def validate_raw_script(raw: dict[str, Any], **kwargs: Any) -> tuple[Script | None, dict[str, Any]]:
    """Schema-validate `raw`, then run `validate_script`. A schema error is one blocker check."""
    try:
        script = Script.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            + (f" (got {str(e['input'])[:40]!r})" if e.get("input") is not None and not isinstance(e["input"], (dict, list)) else "")
            for e in exc.errors()[:8]
        )
        return None, _result([_check("schema", False, details)])
    return script, validate_script(script, **kwargs)


# --------------------------------------------------------------------------- #
# Story Critic pass rule
# --------------------------------------------------------------------------- #
# Scores below the unchanged threshold always fail. Evidence makes low scores auditable; it does not waive them.
DIMENSION_CATEGORIES: dict[str, tuple[str, ...]] = {
    "coherence": ("continuity", "clarity", "concept", "redundancy"),
    "character_consistency": ("character", "stage_direction"),
    "naturalness": ("naturalness", "stage_direction", "lecture"),
    "restraint": ("safety_culture", "character", "humor"),
    "age_fit": ("naturalness", "clarity", "lecture", "safety_culture"),
    "cultural_fit": ("safety_culture",),
    "concept_integration": ("concept", "lecture"),
    "pacing": ("pacing", "redundancy"),
    "fun": ("humor", "pacing"),
}


def evaluate_critic(
    report: CriticReport,
    *,
    concepts: Concepts,
    script: Script | None = None,
    verdicts: dict[str, bool] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    min_score = CRITIC_MIN_SCORE
    min_mean = CRITIC_MIN_MEAN
    max_explanatory = CRITIC_MAX_EXPLANATORY
    scores = report.scores
    mean = sum(scores[k] for k in SCORE_KEYS) / len(SCORE_KEYS)
    low = {k: v for k, v in scores.items() if v < min_score}
    checks.append(_check("critic_each_score", not low, f"scores below {min_score}: {low}"))
    checks.append(_check("critic_mean_score", mean >= min_mean, f"mean {mean:.2f} (min {min_mean})"))

    cov = {c.concept_id: c for c in report.coverage}
    bad_must = []
    for cid in concepts.ids("must"):
        c = cov.get(cid)
        if c is None:
            bad_must.append(f"{cid}: missing from critic coverage")
        elif not c.shown_in_plot or c.delivered_as != "plot_tool":
            bad_must.append(f"{cid}: shown_in_plot={c.shown_in_plot}, delivered_as={c.delivered_as}")
    checks.append(
        _check("critic_must_as_plot_tool", not bad_must, "; ".join(bad_must))
    )

    # Source fidelity is required for every must concept and every important
    # concept the Critic says is used. The Critic
    # must enumerate the cited teaching lines, state the claim, anchor it to
    # the supplied source, and explicitly judge the relationship. This makes
    # silent uncertainty or a wrong numeric example a repairable blocker.
    cov_for_facts = {c.concept_id: c for c in report.coverage}
    required_fact_concepts = concepts.ids("must") + [
        cid for cid in concepts.ids("important")
        if cid in cov_for_facts and cov_for_facts[cid].line_ids
    ]
    fact_problems: list[str] = []
    non_coverage_fact_problems: list[str] = []
    missing_fact_lines: dict[str, list[str]] = {}
    known_concepts = set(concepts.ids())
    real = {l.id for s_ in script.scenes for l in s_.lines} if script is not None else None
    fact_by_concept: dict[str, list[Any]] = {}
    for fact in report.fact_checks:
        if fact.concept_id not in known_concepts:
            fact_problems.append(f"unknown concept {fact.concept_id}")
            non_coverage_fact_problems.append(f"unknown concept {fact.concept_id}")
            continue
        if real is not None:
            invalid = [line_id for line_id in fact.line_ids if line_id not in real]
            if invalid:
                fact_problems.append(f"{fact.concept_id}: invalid fact-check line ids {invalid}")
                non_coverage_fact_problems.append(
                    f"{fact.concept_id}: invalid fact-check line ids {invalid}"
                )
        fact_by_concept.setdefault(fact.concept_id, []).append(fact)

    for concept_id in required_fact_concepts:
        entries = fact_by_concept.get(concept_id, [])
        coverage_entry = cov_for_facts.get(concept_id)
        if not entries:
            fact_problems.append(f"{concept_id}: missing source-fact review")
            non_coverage_fact_problems.append(f"{concept_id}: missing source-fact review")
            continue
        cited = {line_id for entry in entries for line_id in entry.line_ids}
        uncovered = sorted(set(coverage_entry.line_ids if coverage_entry else []) - cited)
        if uncovered:
            fact_problems.append(f"{concept_id}: educational lines not fact-checked {uncovered}")
            missing_fact_lines[concept_id] = uncovered
        for entry in entries:
            if entry.verdict != "supported":
                fact_problems.append(
                    f"{concept_id} {entry.line_ids}: {entry.verdict} claim '{entry.story_claim}' "
                    f"against source '{entry.source_basis}'"
                )
                non_coverage_fact_problems.append(
                    f"{concept_id} {entry.line_ids}: {entry.verdict} claim '{entry.story_claim}' "
                    f"against source '{entry.source_basis}'"
                )
    fact_check = _check("critic_fact_accuracy", not fact_problems,
                        "; ".join(fact_problems[:8]), severity="blocker")
    # This metadata allows the pipeline to distinguish an incomplete evidence inventory from
    # an actual contradiction/uncertain claim. Only the former can be supplemented by the
    # independent source auditor; all other fact failures remain blocking.
    fact_check["data"] = {
        "missing_line_ids_by_concept": missing_fact_lines,
        "supplementable_missing_coverage_only": bool(missing_fact_lines)
        and not non_coverage_fact_problems,
    }
    checks.append(fact_check)

    if script is not None:
        # The Critic must point at real lines. A "plot_tool" claim with no existing line behind it
        # is a verdict nobody can check, so it does not count.
        real = {l.id for s_ in script.scenes for l in s_.lines}
        unsupported = [
            cid
            for cid in concepts.ids("must")
            if (c := cov.get(cid)) is not None
            and c.shown_in_plot
            and c.delivered_as == "plot_tool"
            and not (set(c.line_ids) & real)
        ]
        checks.append(
            _check(
                "critic_evidence",
                not unsupported,
                f"plot_tool claimed without any existing cited line for: {unsupported}",
                severity="major",
            )
        )
        issue_evidence = [i for i in report.issues if i.line_id and i.line_id in real]
        unsupported_low = []
        for dimension, score in low.items():
            categories = DIMENSION_CATEGORIES.get(dimension, ())
            if not any(i.category in categories for i in issue_evidence):
                unsupported_low.append(dimension)
        invalid_issue_refs = [i.line_id for i in report.issues if i.line_id and i.line_id not in real]
        unanchored_safety = [i.message for i in report.issues
                             if i.category == "safety_culture" and (not i.line_id or i.line_id not in real)]
        checks.append(_check(
            "critic_score_evidence",
            not unsupported_low and not invalid_issue_refs and not unanchored_safety,
            f"low scores without category-matched real-line evidence: {unsupported_low}; "
            f"invalid issue line ids: {invalid_issue_refs}; safety issues without a real line: {unanchored_safety}",
            severity="major",
        ))

    important = concepts.ids("important")
    if important:
        ok = sum(1 for cid in important if cid in cov and cov[cid].shown_in_plot)
        ratio = ok / len(important)
    else:
        ratio = 1.0
    checks.append(
        _check(
            "critic_important_coverage",
            ratio >= IMPORTANT_MIN_RATIO,
            f"{ratio:.0%} of important concepts shown (min {IMPORTANT_MIN_RATIO:.0%})",
        )
    )

    checks.append(
        _check(
            "critic_explanatory_ratio",
            report.explanatory_line_ratio <= max_explanatory,
            f"explanatory_line_ratio {report.explanatory_line_ratio:.2f} (max {max_explanatory})",
        )
    )

    blocking = [i for i in report.issues if i.severity in ("blocker", "major")]
    recap_ids = set(script.recap_line_ids) if script is not None else set()
    blocking.extend(
        i for i in report.issues
        if i.severity == "minor" and i.category == "redundancy" and i.line_id in recap_ids
    )
    checks.append(
        _check(
            "critic_issues",
            not blocking,
            "; ".join(
                f"[{('major' if i.severity == 'minor' and i.category == 'redundancy' and i.line_id in recap_ids else i.severity)}"
                f"{'/' + i.category if i.category else ''}] {i.scene_id or ''} {i.line_id or ''} {i.message}".strip()
                for i in blocking
            ),
        )
    )
    for i in report.issues:
        if i.severity == "minor":
            checks.append(
                _check("critic_minor_issue", False, f"{i.scene_id or ''} {i.line_id or ''} {i.message}".strip(), "minor")
            )

    return _result(checks, scores={**scores, "mean": round(mean, 2)})


def evaluate_educational_accuracy(
    report: EducationalAccuracyReport,
    *,
    concepts: Concepts,
    script: Script,
    source_text: str,
    required_line_ids: set[str] | None = None,
    required_lines_by_concept: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Require independent, source-quoted verification of concept-linked teaching claims."""
    real_lines = {line.id for scene in script.scenes for line in scene.lines}
    known = {concept.id: concept for concept in concepts.concepts}
    target_concepts = set(concepts.ids("must", "important"))
    reviewed: set[str] = set()
    reviewed_by_concept: dict[str, set[str]] = {}
    problems: list[str] = []
    issues: list[dict[str, Any]] = []
    for claim in report.claims:
        if claim.concept_id not in target_concepts:
            problems.append(f"claim references non-required/unknown concept {claim.concept_id}")
        invalid = [line_id for line_id in claim.line_ids if line_id not in real_lines]
        if invalid:
            problems.append(f"{claim.concept_id}: invalid line ids {invalid}")
        if claim.source_quote not in source_text:
            problems.append(f"{claim.concept_id} {claim.line_ids}: source quote is not verbatim in lesson/concept input")
        reviewed.update(line_id for line_id in claim.line_ids if line_id in real_lines)
        reviewed_by_concept.setdefault(claim.concept_id, set()).update(
            line_id for line_id in claim.line_ids if line_id in real_lines
        )
        if claim.verdict != "supported":
            problems.append(f"{claim.concept_id} {claim.line_ids}: {claim.verdict} — {claim.claim}")
            for line_id in claim.line_ids:
                if line_id in real_lines:
                    scene_id = line_id.split("_l", 1)[0]
                    issues.append({
                        "severity": "blocker", "category": "concept", "scene_id": scene_id,
                        "line_id": line_id,
                        "message": f"Independent source audit marked this claim {claim.verdict}: {claim.claim}",
                        "fix_hint": "Correct the educational claim using the quoted lesson source and update dependent lines.",
                    })
    missing_concepts = sorted(target_concepts - {c.concept_id for c in report.claims})
    if missing_concepts:
        problems.append(f"missing accuracy review for concepts {missing_concepts}")
    missing_critic_claims = sorted((required_line_ids or set()) - reviewed)
    if missing_critic_claims:
        problems.append(f"Critic-identified educational lines not independently reviewed {missing_critic_claims}")
    if required_lines_by_concept:
        for concept_id, line_ids in required_lines_by_concept.items():
            missing = sorted(line_ids - reviewed_by_concept.get(concept_id, set()))
            if missing:
                problems.append(
                    f"concept {concept_id}: educational lines not independently reviewed under that concept {missing}"
                )
    passed = not problems
    return {
        "passed": passed,
        "checks": [_check("educational_accuracy_audit", passed, "; ".join(problems[:10]), severity="blocker")],
        "issues": issues,
    }


# --------------------------------------------------------------------------- #
# safety audit (structured: the model extracts who does what, code decides)
# --------------------------------------------------------------------------- #
SAFEGUARD_MAX_LINE_DISTANCE = 2   # an adult's helping line must sit this close to the hazard line


def evaluate_safety(report: SafetyReport, script: Script) -> dict[str, Any]:
    """Pass/fail for the Safety Auditor's hazard list, with no word lists.

    A hazard is acceptable when its handler is an adult from the script's own cast, or when the
    claimed safeguard is backed by a real line: for `adult_helping` a line in the SAME scene, within
    `SAFEGUARD_MAX_LINE_DISTANCE` lines, spoken by an adult character or naming one; for
    `child_safe_tool` a real line of the same scene (the auditor judges what it says). Entries that
    cite a line the script does not have cannot be checked and are ignored. An unknown or
    "unspecified" handler is treated as a child: a story must not leave the actor ambiguous.
    """
    lines: dict[str, tuple[str, int, str, str]] = {}   # id -> (scene, index, speaker, normalized text)
    for sc in script.scenes:
        for idx, ln in enumerate(sc.lines):
            lines[ln.id] = (sc.id, idx, ln.speaker, norm_ar(ln.text_display))
    ages = {c.key: c.age_years for c in script.characters}
    adult_keys = {c.key for c in script.characters if c.age_years >= 18}
    adult_names = [norm_ar(c.name) for c in script.characters if c.age_years >= 18 and c.name]
    real_line_ids = set(lines)
    reviewed = set(report.reviewed_line_ids)
    coverage_problems = []
    if len(reviewed) != len(report.reviewed_line_ids):
        coverage_problems.append("reviewed_line_ids contains duplicates")
    missing_reviews = sorted(real_line_ids - reviewed)
    invalid_reviews = sorted(reviewed - real_line_ids)
    if missing_reviews:
        coverage_problems.append(f"lines not reviewed: {missing_reviews[:8]}")
    if invalid_reviews:
        coverage_problems.append(f"review references nonexistent lines: {invalid_reviews[:8]}")

    def is_adult_line(line_id: str) -> bool:
        _, _, speaker, text = lines[line_id]
        return speaker in adult_keys or any(n and n in text for n in adult_names)

    problems: list[str] = []
    minor_problems: list[str] = []
    unsafe: list[dict[str, str]] = []
    for h in report.hazards:
        if h.line_id not in lines:
            problems.append(f"hazard references nonexistent line {h.line_id}")
            continue
        if h.handler not in ages and h.handler != "unspecified":
            problems.append(f"{h.line_id}: hazard handler '{h.handler}' is not in the cast")
        scene_id, idx, _, _ = lines[h.line_id]
        if ages.get(h.handler, 0) >= 18:
            continue
        reason = ""
        if h.safeguard == "none":
            reason = "no adult help or child-safe tool is shown"
        else:
            ref = h.safeguard_line_id
            if not ref or ref not in lines:
                reason = f"claimed safeguard ({h.safeguard}) cites no real line"
            else:
                ref_scene, ref_idx, _, _ = lines[ref]
                if ref_scene != scene_id:
                    reason = f"safeguard line {ref} is in another scene"
                elif h.safeguard == "child_safe_tool" and ref == h.line_id:
                    reason = "the hazard line cannot also serve as independent evidence that the tool is child-safe"
                elif h.safeguard == "adult_helping":
                    if abs(ref_idx - idx) > SAFEGUARD_MAX_LINE_DISTANCE:
                        reason = f"adult line {ref} is too far from the hazard line"
                    elif not is_adult_line(ref):
                        reason = f"line {ref} neither is spoken by nor names an adult character"
        # Risk is judged for this action, material, context and child's age by the auditor.
        # A category label alone must not turn an explicitly minor everyday risk into a blocker.
        if reason and h.risk == "minor":
            # An everyday task with at most a scratch as the worst case: a warning, never a blocker.
            minor_problems.append(f"{h.line_id} ({h.handler}): {h.hazard} - {reason}")
        elif reason:
            problems.append(f"{h.line_id} ({h.handler}): {h.hazard} - {reason}")
            unsafe.append({"scene_id": scene_id, "line_id": h.line_id,
                           "message": f"{h.hazard} - {reason}"})

    problems = coverage_problems + problems
    checks = [_check("safety_audit", not problems, "; ".join(problems[:6]), severity="blocker")]
    if minor_problems:
        checks.append(_check("safety_minor", False, "; ".join(minor_problems[:4]), severity="minor"))
    return _result(checks, unsafe=unsafe)
