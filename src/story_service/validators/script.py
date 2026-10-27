"""Validators for stage S4.

`validate_script` implements the "Code validation (all blockers)" list of Module 4.
`evaluate_critic` implements the Story Critic "Pass" rule.
Both return `{passed, checks: [{name, passed, severity, detail}], scores?}` (spec 5.0).
"""
from __future__ import annotations

import itertools
import os
import re
from difflib import SequenceMatcher
from typing import Any

from pydantic import ValidationError

from story_service.pipeline.models import (
    SCORE_KEYS,
    AgeSpec,
    Concepts,
    CriticReport,
    Script,
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
CRITIC_MAX_EXPLANATORY = 0.20
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


def _env_float(name: str, default: float) -> float:
    """Critic thresholds can be relaxed from .env during development (read lazily, at call time)."""
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc


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
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
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
    checks.append(
        _check(
            "speaker_limit",
            len(non_narrator) <= limit,
            f"{len(non_narrator)} speaking characters (max {limit} plus narrator)",
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
# --- Borderline scores -----------------------------------------------------------------------
# The rubric says: 4 = good with a small fix, 3 = a material weakness. A half-point miss (3.5) that
# no blocker/major issue explains is the Critic disagreeing with its own rubric, and it is where most
# pass/fail noise comes from. Such a dimension is "borderline": it is NOT accepted automatically.
# The stage asks a narrow second question (see `s4_recheck.md`): can a concrete defect be pointed at
# in a real line? Only a "no" (or a "yes" with no real line behind it) clears the dimension.
# 3.0 or lower always fails. The mean is always computed from the RAW scores.
BORDERLINE_FLOOR = 3.5
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


def critic_min_score() -> float:
    return _env_float("S4_CRITIC_MIN_SCORE", CRITIC_MIN_SCORE)


def borderline_dimensions(report: CriticReport, min_score: float | None = None) -> list[str]:
    """Dimensions scored 3.5 (never lower) with no blocker/major issue in a related category."""
    min_score = critic_min_score() if min_score is None else min_score
    blocking = [i for i in report.issues if i.severity in ("blocker", "major")]
    out = []
    for k, v in report.scores.items():
        if v >= min_score or v < BORDERLINE_FLOOR:
            continue
        related = DIMENSION_CATEGORIES.get(k, ())
        backed = any(i.category is None or i.category == "other" or i.category in related for i in blocking)
        if not backed:
            out.append(k)
    return out


def evaluate_critic(
    report: CriticReport,
    *,
    concepts: Concepts,
    script: Script | None = None,
    verdicts: dict[str, bool] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    min_score = _env_float("S4_CRITIC_MIN_SCORE", CRITIC_MIN_SCORE)
    min_mean = _env_float("S4_CRITIC_MIN_MEAN", CRITIC_MIN_MEAN)
    max_explanatory = _env_float("S4_CRITIC_MAX_EXPLANATORY", CRITIC_MAX_EXPLANATORY)
    scores = report.scores
    mean = sum(scores[k] for k in SCORE_KEYS) / len(SCORE_KEYS)

    # `verdicts`: dimension -> True when the re-check confirmed a concrete defect, False when it
    # found none. A borderline dimension is cleared only by an explicit False.
    border = set(borderline_dimensions(report, min_score))
    cleared = {k: v for k, v in scores.items() if k in border and verdicts is not None and verdicts.get(k) is False}
    low = {k: v for k, v in scores.items() if v < min_score and k not in cleared}
    note = f" (re-check found no concrete defect, so accepted as polish-only: {cleared})" if cleared else ""
    checks.append(_check("critic_each_score", not low, f"scores below {min_score}: {low}{note}"))
    checks.append(
        _check("critic_mean_score", mean >= min_mean, f"mean {mean:.2f} (min {min_mean})")
    )

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
    checks.append(
        _check(
            "critic_issues",
            not blocking,
            "; ".join(
                f"[{i.severity}{'/' + i.category if i.category else ''}] {i.scene_id or ''} {i.line_id or ''} {i.message}".strip()
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