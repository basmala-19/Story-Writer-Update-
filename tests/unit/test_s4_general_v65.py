"""General fixes after the matrix runs: hazard risk tiers, early safety feedback, recap guidance,
and concrete speaker-limit repair. No topic, object or character is special to the code."""
import pytest
from helpers import golden_script, load_inputs, line

from story_service.pipeline.models import SafetyReport, Script
from story_service.pipeline.prompting import PROMPT_DIR
from story_service.pipeline.stages import s4_writer
from story_service.providers.llm.fake import FakeLLM
from story_service.validators.script import evaluate_safety, validate_raw_script
from story_service.pipeline.models import AgeSpec, Concepts

CLEAN = {"reviewed_line_ids": [ln["id"] for sc in golden_script()["scenes"] for ln in sc["lines"]],
         "hazards": []}


def _hazard(line_id="s02_l01", risk=None, safeguard="none"):
    h = {"line_id": line_id, "scene_id": line_id[:3], "handler": "nadia",
         "hazard": "a child performs an action that could cause an injury",
         "risk_category": "other",
         "safeguard": safeguard, "safeguard_line_id": None}
    if risk:
        h["risk"] = risk
    return h


def _eval(*hazards):
    reviewed = [ln["id"] for sc in golden_script()["scenes"] for ln in sc["lines"]]
    return evaluate_safety(SafetyReport.model_validate({"reviewed_line_ids": reviewed, "hazards": list(hazards)}),
                           Script.model_validate(golden_script()))


# --------------------------------------------------------------------------- #
# risk tiers
# --------------------------------------------------------------------------- #
def test_risk_defaults_to_serious_so_older_reports_still_block():
    res = _eval(_hazard())
    assert not res["passed"] and res["unsafe"]


def test_serious_unsafeguarded_hazard_blocks():
    assert not _eval(_hazard(risk="serious"))["passed"]


def test_minor_unsafeguarded_hazard_is_only_a_warning():
    res = _eval(_hazard(risk="minor"))
    assert res["passed"] and res["unsafe"] == []
    names = {c["name"]: c for c in res["checks"]}
    assert names["safety_audit"]["passed"] is True
    assert names["safety_minor"]["passed"] is False and names["safety_minor"]["severity"] == "minor"
    assert "s02_l01" in names["safety_minor"]["detail"]


def test_category_alone_does_not_promote_contextual_minor_risk_to_blocker():
    result = _eval(_hazard(risk="minor") | {"risk_category": "sharp_tool"})
    assert result["passed"] and result["unsafe"] == []
    warning = next(c for c in result["checks"] if c["name"] == "safety_minor")
    assert warning["severity"] == "minor" and "s02_l01" in warning["detail"]


def test_safety_audit_requires_every_line_to_be_reviewed():
    raw = {"reviewed_line_ids": [], "hazards": []}
    result = evaluate_safety(SafetyReport.model_validate(raw), Script.model_validate(golden_script()))
    assert not result["passed"] and "lines not reviewed" in result["checks"][0]["detail"]


def test_serious_and_minor_together_block_on_the_serious_one_only():
    res = _eval(_hazard("s02_l01", "minor"), _hazard("s03_l02", "serious"))
    assert not res["passed"]
    assert [u["line_id"] for u in res["unsafe"]] == ["s03_l02"]


def test_safeguarded_serious_hazard_still_passes():
    ok = _hazard("s03_l02", "serious", "adult_helping")
    ok["safeguard_line_id"] = "s03_l01"
    assert _eval(ok)["passed"]


def test_unknown_risk_value_is_rejected_by_the_schema():
    with pytest.raises(Exception):
        SafetyReport.model_validate({"hazards": [_hazard(risk="catastrophic")]})


def test_safety_prompt_defines_risk_tiers_without_naming_objects():
    text = (PROMPT_DIR / "s4_safety.md").read_text(encoding="utf-8")
    assert '"serious"' in text and '"minor"' in text and "age_years" in text
    assert "purely magical" in text and "could copy" in text
    assert "category name alone" in text and "does not determine its risk by itself" in text
    low = text.casefold()
    for word in ("knife", "scissors", "سكين", "مقص", "oven", "فرن", "cake", "pizza", "كعك", "بيتزا"):
        assert word not in low, word


# --------------------------------------------------------------------------- #
# early safety feedback on drafts that fail code checks
# --------------------------------------------------------------------------- #
def _too_few_scenes():
    bad = golden_script()
    bad["scenes"] = bad["scenes"][:2]
    return bad


def _run(llm, **kw):
    records = []
    out = s4_writer.run(**load_inputs("golden"), llm=llm, safety_audit=True,
                        on_attempt=records.append, **kw)
    return out, records


def test_hazard_in_a_code_failed_draft_reaches_the_writer_on_the_first_retry(monkeypatch):
    monkeypatch.setenv("S4_SAFETY_EARLY", "1")
    llm = FakeLLM([_too_few_scenes(), "s4_script.json"], critic="s4_critic_pass.json",
                  safety=[{"hazards": [_hazard()]}, CLEAN])
    _, records = _run(llm)
    assert [r.passed for r in records] == [False, True]
    assert records[0].safety_validation["passed"] is False
    assert len(llm.calls_for("critic")) == 1         # not paid for on the code-failed draft
    prompt2 = llm.calls_for("writer")[1]["user"]
    assert "s02_l01" in prompt2 and "no adult help or child-safe tool" in prompt2
    assert "scene_count" in prompt2                  # the code failure is still reported too


def test_minor_hazard_in_a_code_failed_draft_adds_no_blocking_feedback(monkeypatch):
    monkeypatch.setenv("S4_SAFETY_EARLY", "1")
    llm = FakeLLM([_too_few_scenes(), "s4_script.json"], critic="s4_critic_pass.json",
                  safety=[{"hazards": [_hazard(risk="minor")]}, CLEAN])
    _, records = _run(llm)
    assert records[0].critic_validation is None      # nothing blocking came from safety
    assert "no adult help" not in llm.calls_for("writer")[1]["user"]


def test_unavailable_audit_on_a_code_failed_draft_is_ignored(monkeypatch):
    monkeypatch.setenv("S4_SAFETY_EARLY", "1")
    broken = {"hazards": "not a list"}
    llm = FakeLLM([_too_few_scenes(), "s4_script.json"], critic="s4_critic_pass.json",
                  safety=[broken, broken, CLEAN])
    _, records = _run(llm)
    assert records[0].safety_report is None and records[0].critic_validation is None
    assert "safety_audit_unavailable" not in llm.calls_for("writer")[1]["user"]
    assert len(records) >= 2  # the unavailable early check did not stop the normal repair loop


# --------------------------------------------------------------------------- #
# recap guidance
# --------------------------------------------------------------------------- #
def _issue(line_id, severity="major", category="redundancy"):
    return {"issues": [{"severity": severity, "category": category, "scene_id": line_id[:3],
                        "line_id": line_id, "message": "m", "fix_hint": "h"}]}


def test_issue_on_a_recap_line_gets_the_recap_guide():
    text = s4_writer._recap_hint(_issue("s05_l02"), ["s05_l02"])
    assert text and "own experience" in text and "definition" in text


@pytest.mark.parametrize("report,recap", [
    (_issue("s05_l02", "minor"), ["s05_l02"]),      # minor: polish only
    (_issue("s03_l01"), ["s05_l02"]),               # not a recap line
    (_issue("s05_l02"), None),
    (None, ["s05_l02"]),
])
def test_recap_guide_is_not_added_otherwise(report, recap):
    assert s4_writer._recap_hint(report, recap) == ""


def test_recap_guide_reaches_the_feedback_text():
    out = s4_writer._format_feedback({"checks": []}, _issue("s05_l02"),
                                     {"checks": [{"name": "critic_issues", "passed": False,
                                                  "severity": "blocker", "detail": "x"}]},
                                     recap_ids=["s05_l02"])
    assert s4_writer.RECAP_REPAIR in out


def test_writer_rules_describe_the_recap_as_experience_not_definition():
    rules = (PROMPT_DIR / "s4_writer_rules.md").read_text(encoding="utf-8")
    flat = " ".join(rules.split())
    assert "never a definition" in flat and "what they will do next time" in flat


def test_writer_rules_keep_silent_participants_included_without_proxy_dialogue():
    rules = " ".join((PROMPT_DIR / "s4_writer_rules.md").read_text(encoding="utf-8").split()).casefold()
    assert "mention that same person by name" in rules
    assert "do not make a speaker repeatedly say what the silent child" in rules
    assert "let a silent child take part naturally through a visible action or reaction" in rules


def test_safety_repair_transfers_only_hazardous_handling_and_keeps_child_problem_solving():
    rules = " ".join((PROMPT_DIR / "s4_writer_rules.md").read_text(encoding="utf-8").split()).casefold()
    assert "transfer only the hazardous handling to the adult" in rules
    assert "the adult must not take over the problem-solving or teaching role" in rules


def test_writer_and_auditors_enforce_cause_before_reaction_generically():
    writer = (PROMPT_DIR / "s4_writer_rules.md").read_text(encoding="utf-8").casefold()
    critic = (PROMPT_DIR / "s4_critic_audits.md").read_text(encoding="utf-8").casefold()
    continuity = (PROMPT_DIR / "s4_continuity.md").read_text(encoding="utf-8").casefold()
    assert "before any reaction, complaint or comparison" in writer
    assert "cannot precede the event that causes it" in critic
    assert "check the trigger before the reaction" in continuity


def test_code_valid_draft_outranks_code_invalid_draft_when_selecting_best_attempt():
    valid = {"scores": {"mean": 4.0}, "checks": [
        {"name": "critic_fact_accuracy", "passed": False, "severity": "blocker", "detail": "gap"}
    ]}
    invalid = {"scores": {}, "checks": [
        {"name": "speaker_limit", "passed": False, "severity": "blocker", "detail": "too many"}
    ]}
    assert s4_writer._candidate_rank(valid, None, 3, code_valid=True) > \
        s4_writer._candidate_rank(invalid, None, 1, code_valid=False)


# --------------------------------------------------------------------------- #
# speaker limit: the repair names the lines to rewrite
# --------------------------------------------------------------------------- #
def _five_speaker_draft():
    raw = golden_script()
    for lid, who in (("s01_l01", "youssef"), ("s02_l01", "omar"), ("s02_l03", "omar")):
        line(raw, lid)["speaker"] = who
    return raw


def test_speaker_limit_check_carries_line_ids_and_hint_lists_them():
    inp = load_inputs("golden")
    script, val = validate_raw_script(
        _five_speaker_draft(), concepts=Concepts.model_validate(inp["concepts"]),
        age_spec=AgeSpec.model_validate(inp["age_spec"]), language="ar", wps=inp["wps"])
    check = next(c for c in val["checks"] if c["name"] == "speaker_limit")
    assert not check["passed"]
    assert all(item["line_ids"] for item in check["data"]["speakers"])
    ctx = s4_writer._Ctx(
        intake=s4_writer.Intake.model_validate(inp["intake"]),
        concepts=s4_writer.Concepts.model_validate(inp["concepts"]),
        brief=s4_writer.Brief.model_validate(inp["brief"]),
        idea=s4_writer.Idea.model_validate(inp["idea"]),
        age_spec=s4_writer.AgeSpec.model_validate(inp["age_spec"]),
        speech_style=s4_writer.SpeechStyle.model_validate(inp["speech_style"]),
        wps={"ar": 2.4, "en": 2.7})
    hint = s4_writer._remedy_hints(ctx, val)
    assert "Lines to rewrite or hand to an allowed speaker" in hint
    assert "s02_l01" in hint or "s01_l01" in hint
