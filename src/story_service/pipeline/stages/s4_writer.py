"""S4 Story Writer and Critic.

Flow per attempt (max `max_attempts`, default 3 = MAX_STAGE_ATTEMPTS, or env S4_MAX_ATTEMPTS):

    writer LLM (temp 0.7) -> schema + code validation (all blockers)
        -> [only if code validation passed] Story Critic LLM (temp 0.2, separate prompt)
        -> critic pass rule

Any failure feeds the failed checks and the critic report back to the writer. When attempts run
out `ValidationExhausted` is raised; the orchestrator turns it into a `needs_review` version with
`VALIDATION_EXHAUSTED` and skips S5 onward.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from story_service.pipeline.models import (
    AgeSpec,
    Brief,
    Concepts,
    ContinuityReport,
    RecheckReport,
    CriticReport,
    Idea,
    Intake,
    Script,
    SpeechStyle,
)
from story_service.pipeline.prompting import (
    canonical_json,
    input_hash,
    load_prompt,
    wrap_lesson_text,
)
from story_service.pipeline.repair import (
    affected_scene_ids,
    expand_with_neighbours,
    freeze_unaffected,
    scope_note,
)
from story_service.providers.llm.base import LLMClient
from story_service.providers.llm.openai_compatible import OutputTruncatedError
from story_service.validators.script import (
    DIMENSION_CATEGORIES,
    borderline_dimensions,
    critic_min_score,
    evaluate_critic,
    failed_checks,
    validate_raw_script,
)

STAGE = "S4_script"
WRITER_PROMPT = "s4_writer.md"
CRITIC_PROMPT = "s4_critic.md"
CONTINUITY_PROMPT = "s4_continuity.md"
RECHECK_PROMPT = "s4_recheck.md"
RECHECK_TEMPERATURE = 0.2
WRITER_TEMPERATURE = 0.7
CRITIC_TEMPERATURE = 0.2
CONTINUITY_TEMPERATURE = 0.2

def _positive_env_int(name: str, default: int) -> int:
    """Read an output-token budget with a clear error for invalid settings."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return parsed


def _optional_env_int(name: str) -> int | None:
    value = os.getenv(name)
    return _positive_env_int(name, 0) if value else None


# Optional cap on the estimated INPUT tokens of a writer request (e.g. 7000 for Groq's free tier,
# which rejects requests over 8000 tokens per minute with HTTP 413). Unset = no cap.
MAX_INPUT_TOKENS = _optional_env_int("S4_MAX_INPUT_TOKENS")
CHARS_PER_TOKEN = 3.0  # conservative for Arabic JSON (measured ~3.5)


def estimate_tokens(*texts: str) -> int:
    return round(sum(len(t) for t in texts) / CHARS_PER_TOKEN)


def _compact_previous(previous: dict[str, Any]) -> dict[str, Any]:
    """Shrink a failed script: one short string per line, no `text_spoken` (the writer rebuilds it)."""
    scenes = []
    for s in previous.get("scenes", []):
        if not isinstance(s, dict):
            continue
        lines = [
            f"{l.get('id')}|{l.get('speaker')}|{l.get('emotion', 'neutral')}|{l.get('text_display')}"
            for l in s.get("lines", [])
            if isinstance(l, dict)
        ]
        scenes.append({**{k: v for k, v in s.items() if k != "lines"}, "lines": lines})
    return {**{k: v for k, v in previous.items() if k != "scenes"}, "scenes": scenes}


# Kept below common OpenRouter credit ceilings; override in .env for longer scripts.
WRITER_MAX_TOKENS = _positive_env_int("S4_WRITER_MAX_TOKENS", 16000)
CRITIC_MAX_TOKENS = _positive_env_int("S4_CRITIC_MAX_TOKENS", 8000)  # reasoning models spend part of it thinking
CONTINUITY_MAX_TOKENS = _positive_env_int("S4_CONTINUITY_MAX_TOKENS", 6000)
RECHECK_MAX_TOKENS = _positive_env_int("S4_RECHECK_MAX_TOKENS", 3000)
CRITIC_TOKEN_CEILING = 24000  # cap for the automatic retry after a truncated critic answer
DEFAULT_WPS = {"ar": 2.4, "en": 2.7}


class ProviderFatalError(Exception):
    """A provider failure that retrying cannot fix (bad key, no credits, request too large).

    The stage stops at once instead of spending the remaining attempts on the same error."""

    code = "PROVIDER_ERROR"
    FATAL_STATUS = {401: "invalid or missing API key", 402: "not enough credits for this request",
                    403: "access denied", 413: "request too large for this provider/plan"}

    def __init__(self, status: int, message: str):
        super().__init__(f"{status} {self.FATAL_STATUS.get(status, '')}: {message}")
        self.status = status


class ValidationExhausted(Exception):
    """All attempts failed. `issues` use the API shape {code, severity, stage, scene_id, message}."""

    code = "VALIDATION_EXHAUSTED"

    def __init__(self, issues: list[dict[str, Any]], attempts: int, last_script: Script | None):
        details = "; ".join(issue["message"] for issue in issues) or "no blocker details recorded"
        super().__init__(
            f"S4 failed after {attempts} attempts: {len(issues)} unresolved issue(s): {details}"
        )
        self.issues = issues
        self.attempts = attempts
        self.last_script = last_script


@dataclass
class AttemptRecord:
    """One writer attempt, for the orchestrator to persist in `stage_runs`."""

    attempt: int
    script: dict[str, Any] | None
    code_validation: dict[str, Any]
    critic_report: dict[str, Any] | None = None
    critic_validation: dict[str, Any] | None = None
    passed: bool = False
    feedback_for_next: str | None = None
    selected: bool = False  # advisory mode: this attempt was the best one and became the result
    audit_report: dict[str, Any] | None = None      # continuity ledger + issues (None = not run)
    audit_validation: dict[str, Any] | None = None
    frozen_scenes: list[str] = field(default_factory=list)  # restored from the previous attempt
    scope: list[str] | None = None   # scenes the NEXT attempt may change (None = whole script)
    recheck_report: dict[str, Any] | None = None  # borderline-score verdicts (None = not needed/run)


@dataclass
class S4Outcome:
    """How the returned script was accepted.

    `accepted`: code checks and the Critic rule both passed.
    `needs_review`: only returned because advisory mode keeps the best failed attempt; the script
    is NOT approved and the orchestrator must not treat it as a pass (stages after S4 should wait
    for a human). `failed_rules` lists what still fails.
    """

    status: str  # "accepted" | "needs_review"
    attempt: int
    failed_rules: list[str] = field(default_factory=list)
    mean_score: float | None = None


def _candidate_rank(crit_val: dict[str, Any], report: dict[str, Any] | None, attempt: int):
    """Severity-aware ranking of failed attempts (higher is better).

    A blocker issue or a must concept that is not a plot tool outweighs any number of low scores,
    which in turn outweigh minor polish. Mean score and recency only break ties.
    """
    issues = (report or {}).get("issues", [])
    blockers = sum(1 for i in issues if i.get("severity") == "blocker")
    majors = sum(1 for i in issues if i.get("severity") == "major")
    failed = {c["name"] for c in failed_checks(crit_val) if c["severity"] != "minor"}
    must_bad = int(bool(failed & {"critic_must_as_plot_tool", "critic_evidence"}))
    other = len(failed - {"critic_must_as_plot_tool", "critic_evidence", "critic_issues"})
    mean = (crit_val.get("scores") or {}).get("mean", -1.0)
    return (-blockers, -must_bad, -majors, -other, mean, attempt)


@dataclass
class _Ctx:
    intake: Intake
    concepts: Concepts
    brief: Brief
    idea: Idea
    age_spec: AgeSpec
    speech_style: SpeechStyle
    wps: dict[str, float]
    display_digits: str = "western"
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #
def _dump(model: Any) -> Any:
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


def _brief_for_prompt(brief: Brief) -> dict[str, Any]:
    d = _dump(brief)
    d.pop("speech_style", None)  # passed separately
    return d


def _word_plan(ctx: _Ctx) -> tuple[int, int, int, int]:
    """(target_words, word_low, word_high, avg_words_per_line) for long scripts."""
    target = ctx.age_spec.target_duration_sec
    wps = ctx.wps.get(ctx.intake.language, 2.5)
    target_words = round(target * wps - 0.3 * 39)
    return (
        target_words,
        round(target_words * 0.85),
        round(target_words * 1.10),
        max(1, round(target_words / 39)),
    )


def _writer_execution_budget(ctx: _Ctx) -> str:
    """Give the writer a concrete size plan, not just a duration formula.

    Models tend to interpret a narrated-story request as a short outline.  The
    validator correctly rejects that, but retry feedback alone is often not
    enough to make the model expand the structure.  This plan is intentionally
    derived from the same duration assumptions used by the validator.
    """
    target = ctx.age_spec.target_duration_sec
    max_chars = max(1, ctx.age_spec.max_speakers - 1)
    shared = (
        f"SPEAKERS: at most {max_chars} characters may speak in total (plus the narrator). "
        "Other people from the idea may be mentioned but must never have a line. "
        "EVERY scene, including the last one, has 3-9 lines; the recap is the final 1-2 lines "
        "of the last scene, which must still have at least 3 lines. "
    )
    if target >= 150:
        _, word_low, word_high, avg = _word_plan(ctx)
        narr_cap = max(1, int(0.35 * 35) - 1)  # from the smallest allowed total (35 lines)
        if len(ctx.concepts.ids("must")) >= 2:
            plan = (
                "SCENE PLAN: s01 setup and the first problem; s02 the first must concept is USED and "
                "changes the plot (interaction here); s03 the second problem appears while the conflict "
                "is still open; s04 the second must concept is USED and settles it (interaction here); "
                "s05 one small NEW consequence or beat (something changes), then the 1-2 recap lines. "
            )
        else:
            plan = (
                "SCENE PLAN: s01 setup and the problem; s02 the must concept is USED for the first time "
                "and changes the plot (interaction here); s03 a complication appears while the problem is "
                "still open; s04 the concept is USED again in a new way and settles it (interaction here); "
                "s05 one small NEW consequence or beat (something changes), then the 1-2 recap lines. "
            )
        return (
            "EXECUTION BUDGET (mandatory): write exactly 5 scenes, with 7-8 lines in "
            "each scene (35-40 lines total). At most " + str(narr_cap) + " of those lines may be "
            "narrator lines (the hard limit is 35%; keep a margin): count them before returning. "
            f"Aim for {word_low}-{word_high} spoken words "
            f"in total, to meet the {target}s target: that is about {avg} words in EVERY line, "
            "so do not write short 3-5 word lines. "
            + plan +
            "The last scene is never filler: do not hand out, eat, tidy up or re-explain what s04 "
            "already settled. Include exactly 2 interactions, in s02 and s04 (a third in s03 only "
            "if it is at least 20 seconds after the others). "
            + shared + "This is a full narrated script, not an outline."
        )
    return (
        "EXECUTION BUDGET (mandatory): write 5-6 scenes with 4-7 lines in each scene. "
        "At most 30% of all lines may be narrator lines: count them before returning. "
        "Use the required number of interactions in middle scenes only, in different scenes, "
        "with at least 6 lines between any two of them. " + shared +
        "This is a full narrated script, not an outline; expand the plot until it meets "
        "the requested duration."
    )


def build_writer_messages(
    ctx: _Ctx, feedback: str | None, previous: dict[str, Any] | None = None
) -> tuple[str, str]:
    lesson = (
        f"Topic title: {ctx.intake.topic_title}\n\n"
        f"Topic text (teach ONLY this):\n{ctx.intake.topic_text}\n\n"
        f"Earlier text of the lesson (assumed known, do not teach):\n{ctx.intake.prior_text or '-'}\n\n"
        f"Later topic (do NOT teach, do not preview):\n{ctx.intake.later_preview or '-'}"
    )
    payload = {
        "language": ctx.intake.language,
        "dialect_applied": ctx.intake.dialect_applied,
        "country": ctx.intake.country,
        "grade": ctx.intake.grade,
        "subject": ctx.intake.subject,
        "age_spec": _dump(ctx.age_spec),
        "display_digits": ctx.display_digits,
        "wps": ctx.wps,
        "speech_style": _dump(ctx.speech_style),
        "concepts": _dump(ctx.concepts),
        "selected_idea": _dump(ctx.idea),
        "localization_brief": _brief_for_prompt(ctx.brief),
    }
    user = (
        "Write the script as one JSON object.\n\n"
        f"{_writer_execution_budget(ctx)}\n\n"
        f"<inputs>\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n</inputs>\n\n"
        f"{wrap_lesson_text(lesson)}"
    )
    if feedback:
        user += f"\n\n<feedback>\n{feedback}\n</feedback>"
        system = load_prompt(WRITER_PROMPT)
        if previous:
            # Repair, don't rewrite: a fresh script each time fixes one problem and
            # introduces another. Showing the failed script keeps what already works.
            # Tiers: full script -> compact script -> nothing, whichever first fits the cap.
            tail = (
                "Return the COMPLETE corrected JSON object. Change only what is needed to fix "
                "the problems in <feedback>; keep every other scene, line and field as it is."
            )
            full = (
                f"\n\n<previous_script>\n{json.dumps(previous, ensure_ascii=False)}\n"
                f"</previous_script>\n{tail}"
            )
            compact = (
                "\n\n<previous_script format=\"compact\">\n"
                f"{json.dumps(_compact_previous(previous), ensure_ascii=False)}\n</previous_script>\n"
                "Lines are written as id|speaker|emotion|text_display. Rebuild every line as a full "
                "object (id, speaker, emotion, text_display, text_spoken; text_spoken follows "
                f"text_display with numbers spelled out). {tail}"
            )
            for extra in (full, compact):
                if MAX_INPUT_TOKENS is None or estimate_tokens(system, user, extra) <= MAX_INPUT_TOKENS:
                    user += extra
                    break
        return system, user
    return load_prompt(WRITER_PROMPT), user


def build_critic_messages(ctx: _Ctx, script: dict[str, Any]) -> tuple[str, str]:
    payload = {
        "language": ctx.intake.language,
        "dialect_applied": ctx.intake.dialect_applied,
        "country": ctx.intake.country,
        "age_spec": _dump(ctx.age_spec),
        "speech_style": _dump(ctx.speech_style),
        "concepts": _dump(ctx.concepts),
        "localization_brief": _brief_for_prompt(ctx.brief),
    }
    lesson = (
        f"Topic text:\n{ctx.intake.topic_text}\n\n"
        f"Later topic (must NOT be taught):\n{ctx.intake.later_preview or '-'}"
    )
    user = (
        "Review this script.\n\n"
        f"<context>\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n</context>\n\n"
        f"{wrap_lesson_text(lesson)}\n\n"
        f"<script_json>\n{json.dumps(script, ensure_ascii=False)}\n</script_json>"
    )
    return load_prompt(CRITIC_PROMPT), user


# --------------------------------------------------------------------------- #
# feedback formatting
# --------------------------------------------------------------------------- #
def _format_failures(validation: dict[str, Any]) -> str:
    return "\n".join(
        f"- [{c['severity']}] {c['name']}: {c['detail']}" for c in failed_checks(validation)
    )


def _remedy_hints(ctx: _Ctx, code_validation: dict[str, Any]) -> str:
    """Turn failed code checks into concrete, numeric instructions for the next attempt."""
    names = {c["name"] for c in failed_checks(code_validation)}
    hints: list[str] = []
    est = code_validation.get("estimated_duration_sec")
    target = ctx.age_spec.target_duration_sec
    if "duration" in names and isinstance(est, (int, float)):
        wps = ctx.wps.get(ctx.intake.language, 2.5)
        diff = target - est
        words = round(abs(diff) * wps)
        hints.append(
            f"- Duration: the script is ~{est:.0f}s, the target is {target}s. "
            + (
                f"ADD about {words} spoken words (longer lines of ~9-12 words and more lines), "
                "keeping every line within the word limit."
                if diff > 0
                else f"REMOVE about {words} spoken words."
            )
        )
    if "speaker_limit" in names:
        hints.append(
            f"- Speakers: define at most {max(1, ctx.age_spec.max_speakers - 1)} speaking "
            "characters. Merge or drop the others; they may be mentioned but not speak."
        )
    if "narrator_share" in names:
        data = next((c.get("data") or {} for c in failed_checks(code_validation)
                     if c["name"] == "narrator_share"), {})
        n, total, cap = data.get("narrator"), data.get("total"), data.get("cap")
        numbers = (
            f"Right now {n} of {total} lines are narrator lines and at most {cap} are allowed, so "
            f"convert {max(1, n - cap)} narrator line(s) into short character lines. "
            if None not in (n, total, cap) else ""
        )
        hints.append(
            "- Narrator share: the narrator must deliver at most 35% of all lines. " + numbers +
            "Convert (do not delete) so the total number of lines stays the same: let a character "
            "who is already allowed to speak react or say what is happening, and merge two "
            "consecutive narrator lines into one. Before returning JSON, count narrator lines / all lines "
            "and verify the ratio is <= 0.35. Do not exceed the character speaker limit."
        )
    if "lines_per_scene" in names:
        hints.append(
            "- Lines per scene: every scene (the last one too) needs 3-9 lines. Put the "
            "1-2 recap lines at the end of a normal scene instead of a separate tiny scene."
        )
    if "interaction_gap" in names or "interaction_position" in names:
        hints.append(
            "- Interactions: put them in different middle scenes (not first/last), with at "
            "least 8 lines between two interactions."
        )
    if "schema" in names:
        hints.append(
            "- Schema: the JSON shape is wrong. Use exactly the English key names from the "
            "Output section and include every required key. Each interaction needs id, type, "
            "after_line, concept_id and prompt, plus the fields of its type (targets / options / "
            "pairs / expected_option_id / reveal_text / wrong_feedback). Use only the allowed "
            "values listed in the Output section for enum fields (emotion, time_of_day, role, gender); "
            "the failed check above shows the value that was rejected."
        )
    if "spoken_display_similarity" in names:
        hints.append(
            "- text_spoken must be text_display copied word for word, changing only numbers and "
            "symbols into spelled-out words. Rewrite the listed lines that way."
        )
    if "recap" in names:
        hints.append("- Recap: recap_line_ids must hold 1-2 line ids from the LAST scene.")
    if "no_stage_markup" in names:
        hints.append(
            "- Stage directions: remove parentheses/brackets/asterisks from the listed lines. "
            "Move the physical action to a narrator line and keep only the words a person says."
        )
    if "near_duplicate_lines" in names:
        hints.append(
            "- Repeated lines: each listed pair says the same thing twice. Keep the first one, and "
            "replace the second with a line that moves the plot (a decision, a result, a new detail) "
            "or delete it and let the next scene start sooner."
        )
    return "How to fix:\n" + "\n".join(hints) if hints else ""


def _critic_hints(critic_validation: dict[str, Any]) -> str:
    names = {c["name"] for c in failed_checks(critic_validation)}
    hints: list[str] = []
    if "critic_must_as_plot_tool" in names or "critic_evidence" in names:
        hints.append(
            "- A must concept was delivered as a lecture/mention (or the Critic could not point to "
            "the lines that use it). Use ACTION, not a definition: put a small problem in front of "
            "the characters while the main story problem is still open, let a character USE the "
            "concept to decide something, and show a result that changes what happens next. Keep any "
            "follow-up explanation to one short line in the character's own words."
        )
    if "continuity_audit" in names:
        hints.append(
            "- Continuity: write a short state table for yourself (each object or quantity: state before "
            "-> narrator action -> state after) and make every cited line agree with it. A narrator "
            "line shows an action BEFORE any character mentions its result; do not repeat an action "
            "that already happened. Fix with the smallest edit."
        )
    if "critic_explanatory_ratio" in names:
        hints.append(
            "- Too many explanatory lines. Keep at most 20% of lines explanatory; turn the rest "
            "into action, reaction, a decision or light humor."
        )
    if "critic_issues" in names:
        hints.append(
            "- Fix every listed issue using its fix_hint, blockers first. Anything inside an "
            "interaction (objects, numbers, names) must already have appeared in the story lines "
            "before it; never introduce new quantities or items in an interaction or just to patch "
            "a scene."
        )
    if names & {"critic_each_score", "critic_mean_score"}:
        low = (critic_validation.get("scores") or {})
        weak = sorted((k for k, v in low.items() if k != "mean" and v < 4.0), key=lambda k: low[k])
        focus = f" Weakest dimensions: {', '.join(weak)}." if weak else ""
        hints.append(
            "- Scores are low: fix the specific cited lines first." + focus + " Prefer concrete "
            "actions and short childlike dialogue over abstract summaries, repeated explanations or a "
            "moral slogan. Humor must pay off a setup that already exists in the story, not be a "
            "final line added to sound funny. Preserve facts and scene continuity."
        )
        if "pacing" in weak:
            hints.append(
                "- Pacing: keep every remaining scene tied to the main goal. Do not add a new "
                "incident after the central problem is solved unless it changes the goal, forces "
                "a meaningful decision, or creates a consequence that affects the ending. Cut "
                "filler obstacles and recap only once."
            )
    return "How to fix the Story Critic problems:\n" + "\n".join(hints) if hints else ""


_SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}

# Generic repair instructions per issue category. They never mention a topic, an object or a
# character, so they apply to any story. The Critic chooses the category; code never guesses it
# from keywords.
CATEGORY_REPAIR = {
    "continuity": (
        "Continuity: list each important object/quantity in order (state before -> action -> state "
        "after) and make the cited lines agree with that list. A result must come after its cause; "
        "a line must not claim something is already done (or still pending) when the story state "
        "says otherwise; remaining counts equal the starting count minus what was actually taken. "
        "Fix with the smallest edit, and do not add new objects or quantities."
    ),
    "redundancy": (
        "Redundancy: say each fact, joke and conclusion once. Keep the first occurrence where it "
        "does story work, then replace the repeat with new information or remove it. A closing "
        "line must not repeat the conclusion of the scene right before it."
    ),
    "lecture": (
        "Lecture: turn the cited line into a reaction, a decision or a question from a character who "
        "needs the answer right now, using words from their own situation, not a definition."
    ),
    "naturalness": (
        "Naturalness: replace the cited wording with the plain phrase a child of that age and "
        "dialect would actually say; avoid literal-translation phrasing and formal register in "
        "casual speech. If unsure of a fancy phrase, use the simpler one."
    ),
    "stage_direction": (
        "Stage direction: move the physical action to a narrator line; the character keeps only the "
        "words they would say aloud."
    ),
    "clarity": (
        "Clarity: rewrite the cited sentence so one reading is possible given what has already "
        "happened at that point in the story (who, what, and whether it has happened yet)."
    ),
    "character": (
        "Character: keep each character's voice and role consistent; anyone addressed or mentioned "
        "as present must be in `characters`, and a silent character must not receive dialogue."
    ),
    "concept": (
        "Concept: the concept must be used by a character to solve something while the problem is "
        "still open; do not teach content from the later topic."
    ),
    "pacing": (
        "Pacing: every scene must move the main goal forward. Merge or cut scenes that only repeat "
        "or distribute what is already settled. After the central problem is solved, do not add a "
        "new incident unless it changes the goal, forces a meaningful decision, or creates a "
        "consequence that affects the ending. Raise the concept problem earlier."
    ),
    "humor": (
        "Humor: use one small kind joke that grows from something already in the story and is "
        "understandable on its own; cut any joke that needs outside knowledge."
    ),
}


def _sorted_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        report.get("issues", []), key=lambda i: _SEVERITY_ORDER.get(i.get("severity"), 3)
    )


def _compact_critic_report(report: dict[str, Any]) -> str:
    """Only what the writer can act on: the issues (blockers first) with category and fix hint.
    Scores and coverage are summarised by the failed rules, and the full report wastes tokens."""
    issues = [
        {
            k: i.get(k)
            for k in ("severity", "category", "scene_id", "line_id", "message", "fix_hint")
            if i.get(k)
        }
        for i in _sorted_issues(report)
    ]
    return json.dumps({"issues": issues}, ensure_ascii=False, indent=1) if issues else ""


def _category_repair_hint(critic_report: dict[str, Any] | None) -> str:
    """Repair guidance for the issue categories the Critic actually used (blocker/major only)."""
    if not critic_report:
        return ""
    cats: list[str] = []
    for issue in _sorted_issues(critic_report):
        cat = issue.get("category")
        if issue.get("severity") in ("blocker", "major") and cat in CATEGORY_REPAIR and cat not in cats:
            cats.append(cat)
    if not cats:
        return ""
    return (
        "Repair guide for the issue categories above (apply to the cited lines only; keep every "
        "line that already works):\n" + "\n".join(f"- {CATEGORY_REPAIR[c]}" for c in cats)
    )


def _format_feedback(
    code_validation: dict[str, Any],
    critic_report: dict[str, Any] | None,
    critic_validation: dict[str, Any] | None,
    ctx: _Ctx | None = None,
) -> str:
    parts = []
    code_fail = _format_failures(code_validation)
    if code_fail:
        parts.append("Code checks that failed:\n" + code_fail)
        hints = _remedy_hints(ctx, code_validation) if ctx is not None else ""
        if hints:
            parts.append(hints)
    if critic_validation is not None:
        crit_fail = _format_failures(critic_validation)
        if crit_fail:
            parts.append("Review result (failed rules):\n" + crit_fail)
            hints = _critic_hints(critic_validation)
            if hints:
                parts.append(hints)
    if critic_report is not None:
        compact = _compact_critic_report(critic_report)
        if compact:
            parts.append("Story Critic issues to fix:\n" + compact)
        guide = _category_repair_hint(critic_report)
        if guide:
            parts.append(guide)
    return "\n\n".join(parts)


def _issues_from(
    code_validation: dict[str, Any], critic_validation: dict[str, Any] | None
) -> list[dict[str, Any]]:
    issues = []
    for source in (code_validation, critic_validation):
        if source is None:
            continue
        for c in failed_checks(source):
            if c["severity"] in ("blocker", "major"):
                issues.append(
                    {
                        "code": "VALIDATION_EXHAUSTED",
                        "severity": c["severity"],
                        "stage": STAGE,
                        "scene_id": None,
                        "message": f"{c['name']}: {c['detail']}",
                    }
                )
    return issues


# --------------------------------------------------------------------------- #
# stage
# --------------------------------------------------------------------------- #
def compute_input_hash(inputs: dict[str, Any], model_id: str) -> str:
    """Cache key for `stage_runs.input_hash` (spec 5.0)."""
    return input_hash(inputs, (WRITER_PROMPT, CRITIC_PROMPT, CONTINUITY_PROMPT, RECHECK_PROMPT), model_id)


def _fill_missing_recap(raw: dict[str, Any]) -> dict[str, Any]:
    """If the model forgot `recap_line_ids`, derive it from the last two lines of the last scene.

    The writer prompt already requires the recap to be the final 1-2 lines of the last scene, so
    this only restores a field that is fully determined by that rule. Anything else stays untouched
    and is judged by the validator.
    """
    if not isinstance(raw, dict) or raw.get("recap_line_ids"):
        return raw
    scenes = raw.get("scenes")
    if not isinstance(scenes, list) or not scenes or not isinstance(scenes[-1], dict):
        return raw
    lines = scenes[-1].get("lines")
    if not isinstance(lines, list) or not lines:
        return raw
    ids = [l.get("id") for l in lines[-2:] if isinstance(l, dict) and isinstance(l.get("id"), str)]
    return {**raw, "recap_line_ids": ids} if ids else raw


ALLOWED_EMOTIONS = ("neutral", "happy", "surprised", "worried", "thinking", "laughing")
_EMOTION_SYNONYMS = {
    "relieved": "happy", "excited": "happy", "proud": "happy", "glad": "happy", "pleased": "happy",
    "cheerful": "happy", "joyful": "happy", "calm": "neutral", "serious": "neutral",
    "sad": "worried", "scared": "worried", "afraid": "worried", "anxious": "worried",
    "nervous": "worried", "upset": "worried", "angry": "worried", "curious": "thinking",
    "confused": "thinking", "puzzled": "thinking", "focused": "thinking", "amused": "laughing",
    "playful": "laughing", "shocked": "surprised", "amazed": "surprised",
}


def _normalize_emotions(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Map an invented `emotion` value to the closest allowed one instead of failing the attempt.

    `emotion` is presentation metadata (S6/S7 pick a voice/animation from it); a near-synonym is
    safe, and losing a whole writer attempt over it costs thousands of tokens. Unknown values become
    "neutral". Returns the fixed script and a note per changed line.
    """
    notes: list[str] = []
    if not isinstance(raw, dict) or not isinstance(raw.get("scenes"), list):
        return raw, notes
    for scene in raw["scenes"]:
        for ln in scene.get("lines", []) if isinstance(scene, dict) else []:
            if isinstance(ln, dict) and isinstance(ln.get("emotion"), str) and ln["emotion"] not in ALLOWED_EMOTIONS:
                new = _EMOTION_SYNONYMS.get(ln["emotion"].strip().lower(), "neutral")
                notes.append(f"{ln.get('id')}: emotion '{ln['emotion']}' -> '{new}'")
                ln["emotion"] = new
    return raw, notes


def _critic(llm: LLMClient, ctx: _Ctx, script: dict[str, Any]) -> CriticReport:
    system, user = build_critic_messages(ctx, script)
    last: Exception | None = None
    budget = CRITIC_MAX_TOKENS
    truncations = malformed = 0
    while True:
        try:
            raw = llm.complete_json(system, user, CriticReport, "large", CRITIC_TEMPERATURE, budget)
        except OutputTruncatedError as exc:
            # A cut-off report is unusable, but the script is fine: retry the Critic with more room
            # instead of losing the whole attempt.
            last = exc
            truncations += 1
            if truncations > 2:
                raise
            budget = min(budget * 2, CRITIC_TOKEN_CEILING)
            continue
        try:
            return CriticReport.model_validate(raw)
        except ValidationError as exc:  # one extra call if the critic returns malformed output
            last = exc
            malformed += 1
            if malformed >= 2:
                raise


def build_continuity_messages(ctx: _Ctx, script: dict[str, Any]) -> tuple[str, str]:
    """Only the script: the auditor needs no lesson text, concepts or style, just the story's own facts."""
    user = (
        "Audit this script.\n\n"
        f"<script_json>\n{json.dumps(script, ensure_ascii=False)}\n</script_json>"
    )
    return load_prompt(CONTINUITY_PROMPT), user


def _audit_enabled(override: bool | None) -> bool:
    if override is not None:
        return override
    return os.getenv("S4_CONTINUITY_AUDIT", "1").strip().lower() not in ("0", "false", "no", "off")


def _audit_skipped(reason: str) -> dict[str, Any]:
    return {
        "passed": True,
        "checks": [{"name": "continuity_audit_skipped", "passed": False, "severity": "minor",
                    "detail": reason}],
    }


def _audit(
    llm: LLMClient, ctx: _Ctx, script: dict[str, Any], valid_line_ids: set[str]
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Run the continuity auditor. Returns (report dict or None, validation).

    The audit fails an attempt only for `major` issues that cite a real line. A broken or
    truncated audit call never fails the attempt; it is recorded as a minor "skipped" check.
    """
    system, user = build_continuity_messages(ctx, script)
    budget = CONTINUITY_MAX_TOKENS
    report: ContinuityReport | None = None
    for _ in range(2):
        try:
            raw = llm.complete_json(
                system, user, ContinuityReport, "large", CONTINUITY_TEMPERATURE, budget
            )
            report = ContinuityReport.model_validate(raw)
            break
        except OutputTruncatedError:
            budget = min(budget * 2, CRITIC_TOKEN_CEILING)
        except ValidationError as exc:
            return None, _audit_skipped(f"audit returned {exc.error_count()} schema error(s)")
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if status in ProviderFatalError.FATAL_STATUS:
                raise ProviderFatalError(status, str(exc)[:300]) from exc
            return None, _audit_skipped(f"audit call failed: {type(exc).__name__}")
    if report is None:
        return None, _audit_skipped("audit output was cut off")

    # An issue that cites a line the script does not have is a verdict nobody can check.
    issues = [i for i in report.issues if i.line_id in valid_line_ids]
    majors = [i for i in issues if i.severity == "major"]
    checks = [{
        "name": "continuity_audit",
        "passed": not majors,
        "severity": "major",
        "detail": "; ".join(f"{i.line_id}: {i.message}" for i in majors[:4]),
    }]
    dump = report.model_dump(mode="json")
    dump["issues"] = [
        {"severity": i.severity, "category": "continuity", "scene_id": i.scene_id,
         "line_id": i.line_id, "message": i.message, "fix_hint": i.fix_hint}
        for i in issues
    ]
    return dump, {"passed": not majors, "checks": checks}


def _recheck_enabled() -> bool:
    return os.getenv("S4_RECHECK", "1").strip().lower() not in ("0", "false", "no", "off")


def build_recheck_messages(
    ctx: _Ctx, script: dict[str, Any], dims: list[str], report: CriticReport
) -> tuple[str, str]:
    notes = [
        {k: v for k, v in i.model_dump(mode="json").items() if k in ("category", "scene_id", "line_id", "message") and v}
        for i in report.issues
        if i.severity == "minor"
    ]
    payload = {
        "language": ctx.intake.language,
        "dialect_applied": ctx.intake.dialect_applied,
        "speech_style": _dump(ctx.speech_style),
        "dimensions": dims,
        "earlier_minor_notes_may_be_wrong": notes,
    }
    user = (
        "Verify these borderline dimensions.\n\n"
        f"<context>\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n</context>\n\n"
        f"<script_json>\n{json.dumps(script, ensure_ascii=False)}\n</script_json>"
    )
    return load_prompt(RECHECK_PROMPT), user


def _recheck(
    llm: LLMClient, ctx: _Ctx, script: dict[str, Any], dims: list[str], report: CriticReport,
    valid_line_ids: set[str],
) -> tuple[dict[str, bool] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Second look at borderline dimensions only.

    Returns (verdicts, issues for the writer, raw report). verdicts maps dimension -> True when a
    concrete defect was confirmed on a REAL line, False when none was found. A dimension the
    re-check did not answer is simply absent (stays failing). If the call breaks, verdicts is None
    and the strict rule applies, so a broken re-check can never turn a fail into a pass.
    """
    system, user = build_recheck_messages(ctx, script, dims, report)
    budget = RECHECK_MAX_TOKENS
    rr: RecheckReport | None = None
    for _ in range(2):
        try:
            raw = llm.complete_json(system, user, RecheckReport, "large", RECHECK_TEMPERATURE, budget)
            rr = RecheckReport.model_validate(raw)
            break
        except OutputTruncatedError:
            budget = min(budget * 2, CRITIC_TOKEN_CEILING)
        except ValidationError:
            return None, [], None
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if status in ProviderFatalError.FATAL_STATUS:
                raise ProviderFatalError(status, str(exc)[:300]) from exc
            return None, [], None
    if rr is None:
        return None, [], None

    verdicts: dict[str, bool] = {}
    issues: list[dict[str, Any]] = []
    for v in rr.verdicts:
        if v.dimension not in dims or v.dimension in verdicts:
            continue
        real = [l for l in v.line_ids if l in valid_line_ids]
        confirmed = v.defect and bool(real)  # a defect nobody can locate is not a defect
        verdicts[v.dimension] = confirmed
        if confirmed:
            cats = DIMENSION_CATEGORIES.get(v.dimension, ("other",))
            issues.append({
                "severity": "major",
                "category": cats[0],
                "scene_id": real[0].split("_")[0],
                "line_id": real[0],
                "message": v.evidence or f"{v.dimension}: concrete defect confirmed",
                "fix_hint": v.fix_hint,
            })
    return verdicts, issues, rr.model_dump(mode="json")


def run(**kwargs: Any) -> Script:
    """Backward-compatible entry point: returns the script only. Use `run_with_outcome` to learn
    whether it was accepted or is only the best failed attempt."""
    return run_with_outcome(**kwargs)[0]


def run_with_outcome(
    *,
    intake: dict | Intake,
    concepts: dict | Concepts,
    brief: dict | Brief,
    idea: dict | Idea,
    age_spec: dict | AgeSpec,
    speech_style: dict | SpeechStyle,
    wps: dict[str, float] | None = None,
    feedback: str | None = None,
    llm: LLMClient,
    max_attempts: int | None = None,
    display_digits: str = "western",
    on_attempt: Callable[[AttemptRecord], None] | None = None,
    continuity_audit: bool | None = None,
) -> tuple[Script, S4Outcome]:
    ctx = _Ctx(
        intake=Intake.model_validate(intake),
        concepts=Concepts.model_validate(concepts),
        brief=Brief.model_validate(brief),
        idea=Idea.model_validate(idea),
        age_spec=AgeSpec.model_validate(age_spec),
        speech_style=SpeechStyle.model_validate(speech_style),
        wps={**DEFAULT_WPS, **(wps or {})},
        display_digits=display_digits,
    )
    language = ctx.intake.language
    must_ids = set(ctx.concepts.ids("must"))
    assert must_ids, "S2 must provide at least one 'must' concept"

    max_attempts = max_attempts or _optional_env_int("S4_MAX_ATTEMPTS") or 3
    advisory = os.getenv("S4_CRITIC_ADVISORY", "").strip().lower() in ("1", "true", "yes")
    # Advisory mode: a failing Critic triggers a revision (same loop, same attempt budget) and, when
    # attempts run out, the best code-valid script is returned instead of raising.
    candidates: list[tuple[tuple, Script, AttemptRecord]] = []

    pending_feedback = feedback
    last_script: Script | None = None
    last_code: dict[str, Any] = {"checks": []}
    last_crit_val: dict[str, Any] | None = None
    last_raw: dict[str, Any] | None = None
    pending_scope: set[str] | None = None  # scenes the next attempt may change (None = all)
    audit_on = _audit_enabled(continuity_audit)

    for attempt in range(1, max_attempts + 1):
        system, user = build_writer_messages(ctx, pending_feedback, last_raw)
        try:
            raw = llm.complete_json(
                system, user, Script, "large", WRITER_TEMPERATURE, WRITER_MAX_TOKENS
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in ProviderFatalError.FATAL_STATUS:
                raise ProviderFatalError(status, str(exc)[:300]) from exc
            # Provider-side structured-output validation can reject a model
            # response before it is available to our JSON/schema validator.
            # Treat it as a failed writer attempt so transient failures do not
            # terminate the whole stage and the remaining attempts still run.
            code_val = {
                "passed": False,
                "checks": [
                    {
                        "name": "writer_request",
                        "passed": False,
                        "severity": "blocker",
                        "detail": f"Writer request failed: {type(exc).__name__}: {exc}",
                    }
                ],
            }
            rec = AttemptRecord(attempt=attempt, script=None, code_validation=code_val)
            last_code, last_crit_val = code_val, None
            pending_scope = None
            rec.feedback_for_next = pending_feedback = (
                "Writer request failed before a script was returned. "
                "Return one complete JSON object that includes every required field, "
                "especially recap_line_ids.\n\n"
                + _format_feedback(code_val, None, None)
            )
            if on_attempt:
                on_attempt(rec)
            continue
        raw = _fill_missing_recap(raw)
        raw, normalized = _normalize_emotions(raw)
        # Repair, don't rewrite: scenes the feedback is not about are restored from the last attempt.
        raw, frozen = freeze_unaffected(raw, last_raw, pending_scope)
        last_raw = raw
        script, code_val = validate_raw_script(
            raw,
            concepts=ctx.concepts,
            age_spec=ctx.age_spec,
            language=language,
            wps=ctx.wps,
            display_digits=display_digits,
        )
        if normalized:
            code_val["normalized"] = normalized
        rec = AttemptRecord(
            attempt=attempt, script=raw, code_validation=code_val, frozen_scenes=frozen
        )
        last_script, last_code, last_crit_val = script or last_script, code_val, None
        fb_report: dict[str, Any] | None = None
        crit_val: dict[str, Any] | None = None

        if code_val["passed"]:
            assert script is not None
            audit_val: dict[str, Any] | None = None
            valid_ids = {l.id for sc in script.scenes for l in sc.lines}
            if audit_on:
                rec.audit_report, audit_val = _audit(llm, ctx, raw, valid_ids)
                rec.audit_validation = audit_val
            audit_failed = audit_val is not None and not audit_val["passed"]
            audit_issues = (rec.audit_report or {}).get("issues", [])

            if audit_failed and attempt < max_attempts:
                # A concrete state error: send it back now instead of paying for a full Critic pass.
                rec.critic_validation = last_crit_val = crit_val = audit_val
                fb_report = {"issues": audit_issues}
            else:
                try:
                    report = _critic(llm, ctx, raw)
                except (ValidationError, OutputTruncatedError) as exc:
                    crit_val = {
                        "passed": False,
                        "checks": [
                            {
                                "name": "critic_schema",
                                "passed": False,
                                "severity": "major",
                                "detail": "Story Critic returned no usable report: "
                                + (f"{exc.error_count()} schema error(s)" if isinstance(exc, ValidationError) else str(exc)),
                            }
                        ],
                    }
                else:
                    verdicts: dict[str, bool] | None = None
                    extra_issues: list[dict[str, Any]] = []
                    border = borderline_dimensions(report, critic_min_score())
                    if border and _recheck_enabled():
                        verdicts, extra_issues, rec.recheck_report = _recheck(
                            llm, ctx, raw, border, report, valid_ids
                        )
                    crit_val = evaluate_critic(
                        report, concepts=ctx.concepts, script=script, verdicts=verdicts
                    )
                    rec.critic_report = report.model_dump(mode="json")
                    fb_report = {
                        **rec.critic_report,
                        "issues": [*rec.critic_report["issues"], *extra_issues, *audit_issues],
                    }
                if audit_val is not None:
                    crit_val = {
                        **crit_val,
                        "passed": crit_val["passed"] and audit_val["passed"],
                        "checks": [*crit_val["checks"], *audit_val["checks"]],
                    }
                rec.critic_validation = last_crit_val = crit_val
                if crit_val["passed"]:
                    rec.passed = True
                    if on_attempt:
                        on_attempt(rec)
                    return script, S4Outcome(
                        "accepted", attempt, [], (crit_val.get("scores") or {}).get("mean")
                    )
                candidates.append((_candidate_rank(crit_val, fb_report, attempt), script, rec))

        all_ids = [sc["id"] for sc in raw.get("scenes", []) if isinstance(sc, dict) and "id" in sc]
        affected = affected_scene_ids(code_val, crit_val, (fb_report or {}).get("issues", []))
        pending_scope = expand_with_neighbours(affected, all_ids) if affected else None
        rec.scope = sorted(pending_scope) if pending_scope else None
        feedback_text = _format_feedback(code_val, fb_report, rec.critic_validation, ctx)
        note = scope_note(pending_scope, all_ids)
        rec.feedback_for_next = pending_feedback = (
            feedback_text + ("\n\n" + note if note else "")
        )
        if on_attempt:
            on_attempt(rec)

    best_script = last_script
    if candidates:
        _, best, best_rec = max(candidates, key=lambda c: c[0])
        best_script = best
        if advisory:
            best_rec.selected = True
            return best, S4Outcome(
                "needs_review",
                best_rec.attempt,
                [c["name"] for c in failed_checks(best_rec.critic_validation or {"checks": []}) if c["severity"] != "minor"],
                (best_rec.critic_validation or {}).get("scores", {}).get("mean"),
            )
    # Not advisory: the stage fails, but hand over the best code-valid attempt (not merely the
    # last one) so a reviewer starts from the strongest draft.
    raise ValidationExhausted(_issues_from(last_code, last_crit_val), max_attempts, best_script)


__all__ = [
    "AttemptRecord",
    "S4Outcome",
    "run_with_outcome",
    "ValidationExhausted",
    "ProviderFatalError",
    "build_critic_messages",
    "build_continuity_messages",
    "build_writer_messages",
    "canonical_json",
    "compute_input_hash",
    "run",
]
