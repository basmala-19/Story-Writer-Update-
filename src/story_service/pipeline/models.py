"""Pydantic models for stage S4 (Story Writer and Critic).

Modules 1-3 own the full intake / concepts / brief / idea models. The ones below
are deliberately tolerant (`extra="allow"`) and only declare the fields S4 reads.
"""
from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from story_service.pipeline.text_utils import word_count

Emotion = Literal["neutral", "happy", "surprised", "worried", "thinking", "laughing"]
TimeOfDay = Literal["day", "evening", "night"]
Gender = Literal["f", "m"]
Role = Literal["protagonist", "supporting"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _max_words(value: str, limit: int, label: str) -> str:
    if word_count(value) > limit:
        raise ValueError(f"{label} has {word_count(value)} words (max {limit})")
    return value


# --------------------------------------------------------------------------- #
# Interactions (spec Section 7.7)
# --------------------------------------------------------------------------- #
class _InteractionBase(_Strict):
    id: str = Field(min_length=1)
    after_line: str = Field(min_length=1)
    concept_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    hint: str | None = None

    @field_validator("prompt")
    @classmethod
    def _prompt_len(cls, v: str) -> str:
        return _max_words(v, 25, "prompt")

    @field_validator("hint")
    @classmethod
    def _hint_len(cls, v: str | None) -> str | None:
        return v if v is None else _max_words(v, 20, "hint")


class TapTarget(_Strict):
    label: str = Field(min_length=1)
    reveal_text: str = Field(min_length=1)
    thing: str = Field(min_length=1)

    @field_validator("label")
    @classmethod
    def _label_len(cls, v: str) -> str:
        return _max_words(v, 4, "label")

    @field_validator("reveal_text")
    @classmethod
    def _reveal_len(cls, v: str) -> str:
        return _max_words(v, 20, "reveal_text")


class TapReveal(_InteractionBase):
    type: Literal["tap_reveal"]
    targets: list[TapTarget] = Field(min_length=2, max_length=4)


class ChooseOption(_Strict):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    correct: bool
    feedback: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _text_len(cls, v: str) -> str:
        return _max_words(v, 12, "option text")

    @field_validator("feedback")
    @classmethod
    def _fb_len(cls, v: str) -> str:
        return _max_words(v, 25, "feedback")


class ChooseAnswer(_InteractionBase):
    type: Literal["choose_answer"]
    options: list[ChooseOption] = Field(min_length=2, max_length=4)
    max_attempts: Literal[2] = 2

    @model_validator(mode="after")
    def _one_correct(self) -> ChooseAnswer:
        if sum(o.correct for o in self.options) != 1:
            raise ValueError("choose_answer needs exactly one correct option")
        if len({o.id for o in self.options}) != len(self.options):
            raise ValueError("option ids must be unique")
        return self


class DragPair(_Strict):
    id: str = Field(min_length=1)
    item: str = Field(min_length=1)
    target: str = Field(min_length=1)

    @field_validator("item", "target")
    @classmethod
    def _len(cls, v: str) -> str:
        return _max_words(v, 4, "pair text")


class DragMatch(_InteractionBase):
    type: Literal["drag_match"]
    pairs: list[DragPair] = Field(min_length=2, max_length=4)
    wrong_feedback: str = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> DragMatch:
        if len({p.id for p in self.pairs}) != len(self.pairs):
            raise ValueError("pair ids must be unique")
        return self


class PredictOption(_Strict):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class Predict(_InteractionBase):
    type: Literal["predict"]
    options: list[PredictOption] = Field(min_length=2, max_length=3)
    expected_option_id: str
    reveal_text: str = Field(min_length=1)

    @field_validator("reveal_text")
    @classmethod
    def _reveal_len(cls, v: str) -> str:
        return _max_words(v, 30, "reveal_text")

    @model_validator(mode="after")
    def _expected_exists(self) -> Predict:
        if self.expected_option_id not in {o.id for o in self.options}:
            raise ValueError("expected_option_id is not one of the options")
        return self


Interaction = Annotated[
    TapReveal | ChooseAnswer | DragMatch | Predict, Field(discriminator="type")
]


# --------------------------------------------------------------------------- #
# Script (S4 output)
# --------------------------------------------------------------------------- #
class Character(_Strict):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    role: Role
    gender: Gender
    age_years: int = Field(ge=1, le=120)
    traits: list[str] = Field(default_factory=list)


class Line(_Strict):
    id: str
    speaker: str
    emotion: Emotion = "neutral"
    text_display: str = Field(min_length=1)
    text_spoken: str = Field(min_length=1)


class Scene(_Strict):
    id: str
    summary: str = Field(min_length=1)
    location: str = Field(min_length=1)
    time_of_day: TimeOfDay
    lines: list[Line]
    concepts_covered: list[str] = Field(default_factory=list)
    interaction: Interaction | None = None


class Script(_Strict):
    """`script.json`."""

    ROUTE: ClassVar[str] = "writer"  # FakeLLM routing key

    title: str = Field(min_length=1)
    logline: str = Field(min_length=1)
    characters: list[Character] = Field(min_length=1)
    scenes: list[Scene]
    recap_line_ids: list[str]


# --------------------------------------------------------------------------- #
# Story Critic output
# --------------------------------------------------------------------------- #
SCORE_KEYS = (
    "coherence",
    "character_consistency",
    "naturalness",
    "restraint",
    "age_fit",
    "cultural_fit",
    "concept_integration",
    "pacing",
    "fun",
)


class CoverageEntry(_Strict):
    concept_id: str
    line_ids: list[str] = Field(default_factory=list)
    shown_in_plot: bool
    delivered_as: Literal["plot_tool", "lecture", "mention"]


ISSUE_CATEGORIES = (
    "continuity",      # object/quantity/order of events, state claimed before or after it is true
    "redundancy",      # same information, joke or conclusion repeated
    "lecture",         # definition-shaped or textbook-like line
    "naturalness",     # unnatural, literal or register-breaking wording
    "stage_direction", # a character narrates their own action
    "clarity",         # ambiguous sentence, unclear reference, confusing joke
    "character",       # out-of-character speech or an unlisted/absent person
    "concept",         # a concept mentioned, not used, or taught too early/late
    "pacing",          # a scene that adds nothing, or an unmotivated jump
    "humor",
    "safety_culture",
    "other",
)


class CriticIssue(_Strict):
    severity: Literal["blocker", "major", "minor"]
    category: Literal[
        "continuity", "redundancy", "lecture", "naturalness", "stage_direction", "clarity",
        "character", "concept", "pacing", "humor", "safety_culture", "other",
    ] | None = None
    scene_id: str | None = None
    line_id: str | None = None
    message: str
    fix_hint: str | None = None


class CriticReport(_Strict):
    ROUTE: ClassVar[str] = "critic"

    scores: dict[str, float]
    coverage: list[CoverageEntry]
    explanatory_line_ratio: float = Field(ge=0, le=1)
    issues: list[CriticIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _scores_complete(self) -> CriticReport:
        missing = set(SCORE_KEYS) - set(self.scores)
        extra = set(self.scores) - set(SCORE_KEYS)
        if missing or extra:
            raise ValueError(f"scores keys mismatch: missing={sorted(missing)} extra={sorted(extra)}")
        bad = [k for k, v in self.scores.items() if not 1 <= v <= 5]
        if bad:
            raise ValueError(f"scores outside 1..5: {bad}")
        return self


class DimensionVerdict(_Strict):
    dimension: str
    defect: bool                      # True only when a concrete defect can be pointed at
    line_ids: list[str] = Field(default_factory=list)
    evidence: str = ""                # one sentence: the quoted words and why they fail the dimension
    fix_hint: str | None = None


class RecheckReport(_Strict):
    """Second, narrow look at the borderline score dimensions of a Critic report."""

    ROUTE: ClassVar[str] = "recheck"

    verdicts: list[DimensionVerdict] = Field(default_factory=list)


class LedgerEntry(_Strict):
    item: str          # an object, quantity, or fact that changes during the story
    line_id: str       # the narrator line that establishes this state
    state: str         # the state right after that line


class ContinuityIssue(_Strict):
    severity: Literal["major", "minor"]
    scene_id: str
    line_id: str
    message: str
    fix_hint: str | None = None


class ContinuityReport(_Strict):
    """Mechanical state audit: a ledger built from narrator lines, then every character claim
    checked against the ledger at that point of the story."""

    ROUTE: ClassVar[str] = "continuity"

    ledger: list[LedgerEntry] = Field(default_factory=list)
    issues: list[ContinuityIssue] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Upstream inputs (owned by modules 1-3; only what S4 reads)
# --------------------------------------------------------------------------- #
class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class Intake(_Loose):
    topic_title: str
    topic_text: str
    prior_text: str = ""
    later_preview: str = ""
    language: Literal["ar", "en"]
    dialect_applied: str | None = None
    country: str
    age_group: Literal["6-8", "9-11", "12-14", "15-18"]
    grade: str = ""
    subject: str = ""
    target_duration_sec: int


class Concept(_Loose):
    id: str
    title: str
    weight: Literal["must", "important", "optional"]
    definition: str = ""
    misconception: dict | None = None


class Concepts(_Loose):
    concepts: list[Concept]
    assumed_prior_knowledge: list[str] = Field(default_factory=list)

    def ids(self, *weights: str) -> list[str]:
        return [c.id for c in self.concepts if not weights or c.weight in weights]


class Idea(_Loose):
    id: str
    logline: str
    setting: str
    central_problem: str
    concept_moments: list[dict]
    tone: str = ""
    one_liner: str = ""


class AgeSpec(_Strict):
    age_group: Literal["6-8", "9-11", "12-14", "15-18"]
    max_words_per_line: int
    max_speakers: int
    target_duration_sec: int


class SpeechStyle(_Loose):
    guidance: str
    example_phrases: list[str] = Field(default_factory=list)


class Brief(_Loose):
    names_female: list[str] = Field(default_factory=list)
    names_male: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)
    foods: list[str] = Field(default_factory=list)
    sensitivities: list[str] = Field(default_factory=list)
    speech_style: SpeechStyle
