"""Cheap, deterministic S4 input gate. This runs before any model request."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from story_service.pipeline.models import AgeSpec, Brief, Concepts, Idea, Intake, SpeechStyle
from story_service.pipeline.text_utils import norm_ar
from story_service.validators.script import (
    DURATION_TOLERANCE,
    MAX_LINES,
    MAX_SCENES,
    MIN_LINES,
    MIN_SCENES,
)

DEFAULT_WPS = {"ar": 2.4, "en": 2.7}


@dataclass
class PreflightReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    normalized_inputs: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return not self.errors


class PreflightError(ValueError):
    def __init__(self, report: PreflightReport):
        self.report = report
        super().__init__("S4 input preflight failed: " + "; ".join(report.errors))


def _validation_messages(label: str, exc: ValidationError) -> list[str]:
    messages = []
    for item in exc.errors()[:8]:
        path = ".".join(str(part) for part in item.get("loc", ())) or label
        messages.append(f"{label}.{path}: {item.get('msg', 'invalid value')}")
    return messages


def preflight_s4(inputs: dict[str, Any], wps: dict[str, float] | None = None) -> PreflightReport:
    """Check schema and cross-input feasibility without calling an LLM.

    Semantic story quality and correctness cannot be known before a story exists; those remain
    enforced by the normal S4 validators and reviewers.
    """
    report = PreflightReport()
    models: dict[str, Any] = {}
    for name, model in (
        ("intake", Intake), ("concepts", Concepts), ("brief", Brief),
        ("idea", Idea), ("age_spec", AgeSpec), ("speech_style", SpeechStyle),
    ):
        try:
            models[name] = model.model_validate(inputs.get(name))
        except ValidationError as exc:
            report.errors.extend(_validation_messages(name, exc))
    if report.errors:
        return report

    intake: Intake = models["intake"]
    concepts: Concepts = models["concepts"]
    brief: Brief = models["brief"]
    idea: Idea = models["idea"]
    age: AgeSpec = models["age_spec"]
    repairs: list[str] = []
    ids = [concept.id for concept in concepts.concepts]
    known = set(ids)

    if len(ids) != len(known):
        report.errors.append("concepts contain duplicate ids")
    must_ids = concepts.ids("must")
    if not must_ids:
        report.errors.append("at least one concept with weight='must' is required")
    if intake.age_group != age.age_group:
        report.errors.append(
            f"intake.age_group ({intake.age_group}) does not match age_spec.age_group ({age.age_group})"
        )
    # Intake carries the requested duration; AgeSpec/Brief duplicate it for downstream stages.
    # Resolve this safe duplication conflict before generation instead of spending an attempt.
    if intake.target_duration_sec != age.target_duration_sec:
        age = age.model_copy(update={"target_duration_sec": intake.target_duration_sec})
        repairs.append(
            f"age_spec.target_duration_sec set to intake.target_duration_sec ({intake.target_duration_sec})"
        )
    if age.max_speakers < 2:
        report.errors.append("age_spec.max_speakers must allow at least one character plus narrator")
    if age.max_words_per_line < 1:
        report.errors.append("age_spec.max_words_per_line must be positive")
    if age.target_duration_sec < 1:
        report.errors.append("target duration must be positive")
    if not intake.topic_text.strip():
        report.errors.append("intake.topic_text is empty; there is no source to ground the lesson")

    moment_ids = [moment.get("concept_id") for moment in idea.concept_moments
                  if isinstance(moment, dict) and isinstance(moment.get("concept_id"), str)]
    unknown_moments = sorted(set(moment_ids) - known)
    if unknown_moments:
        report.errors.append(f"idea.concept_moments refer to unknown concepts: {unknown_moments}")
    missing_moments = sorted(set(must_ids) - set(moment_ids))
    if missing_moments:
        report.errors.append(f"idea has no plot moment for must concepts: {missing_moments}")
    for moment in idea.concept_moments:
        if not isinstance(moment, dict) or not moment.get("concept_id") or not moment.get("moment"):
            report.errors.append("every idea.concept_moments entry needs a known concept_id and a non-empty moment")
            break

    source = norm_ar(intake.topic_text + " " + " ".join(
        " ".join(getattr(concept, "evidence", []) or []) for concept in concepts.concepts
    ))
    for evidence in idea.constraints.concept_evidence:
        if evidence.concept_id not in known:
            report.errors.append(
                f"idea.constraints.concept_evidence references unknown concept {evidence.concept_id}"
            )
        terms = evidence.required_terms + evidence.required_any_forms
        for term in terms:
            if not term.strip() or norm_ar(term) not in source:
                report.errors.append(
                    f"required literal evidence {term!r} for {evidence.concept_id} is absent from the lesson/concept source"
                )

    constraints = idea.constraints
    max_scene_lines = MAX_SCENES * MAX_LINES
    if constraints.required_child_count and constraints.required_child_count > max_scene_lines:
        report.errors.append(
            f"required_child_count ({constraints.required_child_count}) exceeds the maximum story line capacity ({max_scene_lines})"
        )
    if len(constraints.required_participants) > max_scene_lines:
        report.errors.append("required_participants exceed the maximum story line capacity")
    if len(set(constraints.required_participants)) != len(constraints.required_participants):
        report.errors.append("idea.constraints.required_participants contains duplicate names")
    if (constraints.required_child_count is not None and constraints.required_participants
            and len(constraints.required_participants) > constraints.required_child_count):
        report.warnings.append(
            "required_participants names more children than required_child_count; all named participants "
            "will be preserved, and the numeric count remains a minimum"
        )
    max_character_speakers = age.max_speakers - 1
    required_children = constraints.required_child_count or len(constraints.required_participants)
    if required_children > max_character_speakers:
        report.warnings.append(
            f"idea needs {required_children} named child participants but only {max_character_speakers} characters may speak; "
            f"keep at least {required_children - max_character_speakers} participant(s) silent"
        )
    interaction_min = 2 if age.target_duration_sec >= 150 else 1
    available_middle_scenes = MAX_SCENES - 2
    if interaction_min > available_middle_scenes:
        report.errors.append(
            f"interaction requirement ({interaction_min}) exceeds available middle scenes ({available_middle_scenes})"
        )

    active_wps = {**DEFAULT_WPS, **(wps or {})}
    speed = active_wps.get(intake.language)
    if not isinstance(speed, (int, float)) or speed <= 0:
        report.errors.append(f"wps for language {intake.language!r} must be a positive number")
    elif age.max_words_per_line > 0 and age.target_duration_sec > 0:
        max_lines = MAX_SCENES * MAX_LINES
        max_duration = max_lines * (age.max_words_per_line / speed + 0.3)
        min_allowed = age.target_duration_sec * (1 - DURATION_TOLERANCE)
        if max_duration < min_allowed:
            report.errors.append(
                f"duration is infeasible: even {MAX_SCENES} scenes of {MAX_LINES} maximum-length lines "
                f"reach only about {max_duration:.0f}s, below the required {min_allowed:.0f}s minimum"
            )

    report.repairs = repairs
    normalized = deepcopy(inputs)
    for name, model in models.items():
        if name == "age_spec":
            model = age
        elif name == "brief":
            model = brief
        normalized[name] = model.model_dump(mode="python")
    report.normalized_inputs = normalized
    return report
