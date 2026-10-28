"""Safety audit: the model extracts who does what, code judges it against the script's own cast.

General behaviour only: no object, topic or character names are special to the code.
"""
from pathlib import Path

import pytest
from helpers import golden_script, load_inputs, load_llm_fixture

from story_service.pipeline.models import SafetyReport, Script
from story_service.pipeline.stages import s4_writer
from story_service.providers.llm.fake import FakeLLM
from story_service.providers.llm.openai_compatible import OutputTruncatedError
from story_service.validators.script import evaluate_safety

# golden cast: nadia 9, karim 7, mama 35 (adult), youssef 9, omar 9.
# s03 = [mama, nadia, karim, nadia, karim, mama]; s02 = [narrator, karim, nadia, karim, nadia, narrator]


def _script(raw=None) -> Script:
    return Script.model_validate(raw or golden_script())


def _hazard(line_id="s02_l01", handler="nadia", safeguard="none", ref=None, scene=None):
    return {
        "line_id": line_id,
        "scene_id": scene or line_id[:3],
        "handler": handler,
        "hazard": "a child performs an action that could cause an injury",
        "risk_category": "other",
        "safeguard": safeguard,
        "safeguard_line_id": ref,
    }


def _report(*hazards) -> SafetyReport:
    ids = [ln["id"] for sc in golden_script()["scenes"] for ln in sc["lines"]]
    return SafetyReport.model_validate({"reviewed_line_ids": ids, "hazards": list(hazards)})


def _passes(*hazards, script=None) -> bool:
    return evaluate_safety(_report(*hazards), script or _script())["passed"]


# --------------------------------------------------------------------------- #
# code judgement (no word lists)
# --------------------------------------------------------------------------- #
def test_no_hazards_passes():
    assert _passes()


def test_child_with_no_safeguard_fails_and_reports_the_line():
    res = evaluate_safety(_report(_hazard()), _script())
    assert not res["passed"]
    assert res["checks"][0]["name"] == "safety_audit"
    assert res["checks"][0]["severity"] == "blocker"
    assert res["unsafe"][0]["line_id"] == "s02_l01"
    assert "s02_l01" in res["checks"][0]["detail"]


def test_adult_handler_needs_no_other_safeguard():
    assert _passes(_hazard("s03_l01", handler="mama"))


def test_unspecified_or_unknown_handler_is_treated_as_a_child():
    assert not _passes(_hazard(handler="unspecified"))
    assert not _passes(_hazard(handler="somebody_not_in_the_cast"))


def test_adult_helping_backed_by_a_nearby_adult_line_passes():
    # s03_l02 is nadia's line; s03_l01 is spoken by mama, one line earlier.
    assert _passes(_hazard("s03_l02", safeguard="adult_helping", ref="s03_l01"))


def test_adult_helping_is_rejected_when_the_cited_line_is_not_adult():
    # s03_l03 is karim (a child) and does not name the adult.
    assert not _passes(_hazard("s03_l02", safeguard="adult_helping", ref="s03_l03"))


def test_adult_helping_is_rejected_when_too_far_or_in_another_scene_or_missing():
    far = _hazard("s03_l04", safeguard="adult_helping", ref="s03_l01")   # 3 lines apart
    other_scene = _hazard("s02_l02", safeguard="adult_helping", ref="s03_l01")
    missing = _hazard("s03_l02", safeguard="adult_helping", ref=None)
    invented = _hazard("s03_l02", safeguard="adult_helping", ref="s09_l01")
    for h in (far, other_scene, missing, invented):
        assert not _passes(h), h


def test_adult_helping_accepts_a_line_that_names_the_adult():
    raw = golden_script()
    child_line = next(l for s in raw["scenes"] if s["id"] == "s03" for l in s["lines"] if l["id"] == "s03_l03")
    child_line["text_display"] = "ماما تمسك الأداة وتساعدني الآن."
    assert _passes(_hazard("s03_l02", safeguard="adult_helping", ref="s03_l03"), script=_script(raw))


def test_child_safe_tool_needs_a_real_line_in_the_same_scene():
    assert _passes(_hazard("s02_l02", safeguard="child_safe_tool", ref="s02_l01"))
    assert not _passes(_hazard("s02_l02", safeguard="child_safe_tool", ref=None))
    assert not _passes(_hazard("s02_l02", safeguard="child_safe_tool", ref="s03_l01"))
    assert not _passes(_hazard("s02_l02", safeguard="child_safe_tool", ref="s02_l02"))


def test_audit_citing_a_missing_hazard_line_fails_closed():
    result = evaluate_safety(_report(_hazard("s09_l01", scene="s09")), _script())
    assert not result["passed"]
    assert "nonexistent line" in result["checks"][0]["detail"]


def test_code_never_scans_for_words():
    """A line that talks about a risky object proves nothing: only the auditor's entries count."""
    raw = golden_script()
    raw["scenes"][1]["lines"][0]["text_display"] = "أمسكت نادية السكين الحادة بيدها."
    assert _passes(script=_script(raw))                     # auditor listed nothing
    assert not _passes(_hazard("s02_l01"), script=_script(raw))   # auditor listed it


def test_validator_source_holds_no_hazard_word_list():
    src = (Path(__file__).parents[2] / "src/story_service/validators/script.py").read_text(encoding="utf-8")
    for word in ("سكين", "سكاكين", "ساطور", "مكواة", "knife", "scissors", "oven"):
        assert word not in src, word


# --------------------------------------------------------------------------- #
# stage wiring
# --------------------------------------------------------------------------- #
REVIEWED_IDS = [ln["id"] for sc in golden_script()["scenes"] for ln in sc["lines"]]
CLEAN = {"reviewed_line_ids": REVIEWED_IDS, "hazards": []}


def _run(llm, **kw):
    records = []
    out = s4_writer.run(**load_inputs("golden"), llm=llm, safety_audit=True, on_attempt=records.append, **kw)
    return out, records


def test_unsafe_attempt_is_fed_back_and_the_clean_retry_passes():
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json",
                  safety=[{"reviewed_line_ids": REVIEWED_IDS, "hazards": [_hazard()]}, CLEAN])
    _, records = _run(llm)
    assert [r.passed for r in records] == [False, True]
    assert records[0].safety_validation["passed"] is False
    prompt2 = llm.calls_for("writer")[1]["user"]
    assert "s02_l01" in prompt2
    assert "named adult" in prompt2          # the generic category repair guide
    assert "s02" in (records[0].scope or [])


def test_safeguarded_hazard_is_accepted_on_the_first_attempt():
    ok = _hazard("s03_l02", safeguard="adult_helping", ref="s03_l01")
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json",
                  safety={"reviewed_line_ids": REVIEWED_IDS, "hazards": [ok]})
    _, records = _run(llm)
    assert len(records) == 1 and records[0].passed
    assert records[0].safety_report["hazards"][0]["safeguard"] == "adult_helping"


def test_safety_audit_still_runs_when_critic_rejects_for_unrelated_reasons():
    """Every code-valid draft gets safety feedback, even if another check already failed."""
    llm = FakeLLM("s4_script.json", critic="s4_critic_flat4.json",
                  safety={"reviewed_line_ids": REVIEWED_IDS, "hazards": [_hazard()]})
    records = []
    with pytest.raises(s4_writer.ValidationExhausted) as exc:
        s4_writer.run(**load_inputs("golden"), llm=llm, safety_audit=True, max_attempts=1,
                      on_attempt=records.append)
    assert len(llm.calls_for("safety")) == 1
    assert records[0].safety_validation["passed"] is False
    assert "critic_mean_score" in str(exc.value)
    assert "safety_audit" in str(exc.value)


def test_safety_audit_still_runs_when_critic_flags_a_hazard():
    critic_report = load_llm_fixture("s4_critic_flat4.json")
    critic_report["issues"] = [{
        "severity": "blocker", "category": "safety_culture", "scene_id": "s02",
        "line_id": "s02_l01", "message": "unsafe action", "fix_hint": "revise it",
    }]
    llm = FakeLLM("s4_script.json", critic=critic_report,
                  safety={"reviewed_line_ids": REVIEWED_IDS, "hazards": [_hazard()]})
    records = []
    with pytest.raises(s4_writer.ValidationExhausted):
        s4_writer.run(**load_inputs("golden"), llm=llm, safety_audit=True, max_attempts=1,
                      on_attempt=records.append)
    assert len(llm.calls_for("safety")) == 1
    assert records[0].safety_validation["passed"] is False


def test_early_audit_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("S4_SAFETY_EARLY", "0")
    bad = golden_script()
    bad["scenes"] = bad["scenes"][:2]            # too few scenes: a blocker, so no model call is spent
    llm = FakeLLM(bad, critic="s4_critic_pass.json", safety=CLEAN)
    with pytest.raises(s4_writer.ValidationExhausted):
        s4_writer.run(**load_inputs("golden"), llm=llm, safety_audit=True, max_attempts=1)
    assert llm.calls_for("safety") == [] and llm.calls_for("critic") == []


def _boom(_user):
    raise OutputTruncatedError("cut off")


@pytest.mark.parametrize("bad", [_boom, {"hazards": "not a list"}])
def test_broken_audit_fails_closed(bad):
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json", safety=bad)
    with pytest.raises(s4_writer.ValidationExhausted) as exc:
        s4_writer.run(**load_inputs("golden"), llm=llm, safety_audit=True, max_attempts=1)
    assert "safety_audit_unavailable" in str(exc.value)


def test_audit_is_on_by_default_and_can_be_disabled_explicitly(monkeypatch):
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json")
    s4_writer.run(**load_inputs("golden"), llm=llm)       # conftest sets S4_SAFETY_AUDIT=0
    assert llm.calls_for("safety") == []
    monkeypatch.delenv("S4_SAFETY_AUDIT", raising=False)
    assert s4_writer._safety_enabled(None) is True
    monkeypatch.setenv("S4_SAFETY_AUDIT", "0")
    assert s4_writer._safety_enabled(None) is False
    assert s4_writer._safety_enabled(True) is True


def test_safety_prompt_is_generic_and_in_the_input_hash():
    system, user = s4_writer.build_safety_messages(None, {"title": "x"})
    assert "Do not use a fixed" in system
    assert "<script_json>" in user
    h1 = s4_writer.compute_input_hash({"a": 1}, "m")
    assert isinstance(h1, str) and len(h1) == 64
    assert s4_writer.SAFETY_PROMPT in ("s4_safety.md",)


def test_comparison_script_snapshots_every_stage_prompt():
    """Provenance must cover every prompt the stage loads, or a prompt change goes unrecorded."""
    src = (Path(__file__).parents[2] / "scripts/compare_versions.py").read_text(encoding="utf-8")
    for name in (s4_writer.WRITER_PROMPT, s4_writer.CRITIC_PROMPT,
                 s4_writer.CONTINUITY_PROMPT, s4_writer.SAFETY_PROMPT):
        assert f'"{name}"' in src, name
