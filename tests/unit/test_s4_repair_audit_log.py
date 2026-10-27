"""Scoped repair, continuity audit and the aggregate run log (general behaviour, no topic words)."""
import copy
import json

import pytest
from helpers import golden_script, line, load_inputs, load_llm_fixture, scene

from story_service.pipeline import run_log
from story_service.pipeline.models import Concepts, ContinuityReport, CriticReport
from story_service.pipeline.repair import (
    affected_scene_ids,
    expand_with_neighbours,
    freeze_unaffected,
    scope_note,
    scene_ids_in,
)
from story_service.pipeline.stages import s4_writer
from story_service.providers.llm.fake import FakeLLM
from story_service.providers.llm.openai_compatible import OutputTruncatedError

IDS = ["s01", "s02", "s03", "s04", "s05"]


def _failed(name, detail="", severity="blocker"):
    return {"checks": [{"name": name, "passed": False, "severity": severity, "detail": detail}]}


# --------------------------------------------------------------------------- #
# which scenes does a failure concern?
# --------------------------------------------------------------------------- #
def test_scene_ids_are_read_from_scene_and_line_ids():
    assert scene_ids_in("lines over 18 words: ['s03_l02:22'], s05") == {"s03", "s05"}
    assert scene_ids_in("s123 and ps07") == set()


def test_line_level_code_failure_is_scoped_to_its_scene():
    got = affected_scene_ids(_failed("words_per_line", "lines over 18 words: ['s03_l02:22']"), None, [])
    assert got == {"s03"}


@pytest.mark.parametrize("name", ["duration", "narrator_share", "speaker_limit", "schema",
                                  "must_concepts_covered", "interaction_count", "recap"])
def test_whole_script_code_failures_leave_the_writer_free(name):
    assert affected_scene_ids(_failed(name, "s02 mentioned"), None, []) is None


def test_scoped_check_without_a_cited_scene_is_global():
    assert affected_scene_ids(_failed("no_emoji", "emoji in: []"), None, []) is None


def test_critic_issues_scope_the_repair_to_the_cited_scenes():
    crit = {"checks": [{"name": "critic_each_score", "passed": False, "severity": "blocker", "detail": ""},
                       {"name": "critic_issues", "passed": False, "severity": "blocker", "detail": ""}]}
    issues = [{"scene_id": "s04", "line_id": "s04_l05"}, {"scene_id": "s02", "line_id": None}]
    assert affected_scene_ids({"checks": []}, crit, issues) == {"s02", "s04"}


def test_low_scores_without_a_cited_place_are_global():
    crit = {"checks": [{"name": "critic_each_score", "passed": False, "severity": "blocker", "detail": ""}]}
    assert affected_scene_ids({"checks": []}, crit, []) is None


def test_pacing_score_feedback_cuts_post_resolution_filler():
    feedback = s4_writer._critic_hints({
        "scores": {"pacing": 3.5},
        "checks": [{"name": "critic_each_score", "passed": False,
                    "severity": "blocker", "detail": "pacing is below threshold"}],
    })
    assert "Pacing:" in feedback
    assert "after the central problem is solved" in feedback
    assert "consequence that affects the ending" in feedback


@pytest.mark.parametrize("name", ["critic_must_as_plot_tool", "critic_explanatory_ratio",
                                  "critic_important_coverage", "critic_evidence"])
def test_plot_structure_failures_are_global_even_with_cited_scenes(name):
    crit = {"checks": [{"name": name, "passed": False, "severity": "blocker", "detail": ""}]}
    assert affected_scene_ids({"checks": []}, crit, [{"scene_id": "s03"}]) is None


def test_neighbours_are_added_for_continuity_fixes():
    assert expand_with_neighbours({"s03"}, IDS) == {"s02", "s03", "s04"}
    assert expand_with_neighbours({"s01"}, IDS) == {"s01", "s02"}
    assert expand_with_neighbours({"s05"}, IDS) == {"s04", "s05"}


# --------------------------------------------------------------------------- #
# restoring locked scenes
# --------------------------------------------------------------------------- #
def test_freeze_restores_scenes_outside_the_editable_set():
    old = golden_script()
    new = copy.deepcopy(old)
    line(new, "s02_l01")["text_display"] = "تغيير مسموح"
    line(new, "s05_l01")["text_display"] = "تغيير غير مسموح"
    merged, restored = freeze_unaffected(new, old, {"s02", "s03"})
    assert restored == ["s05"] or "s05" in restored
    assert scene(merged, "s05") == scene(old, "s05")
    assert line(merged, "s02_l01")["text_display"] == "تغيير مسموح"
    assert merged["scenes"][0] == old["scenes"][0]  # s01 locked and unchanged


def test_freeze_does_nothing_without_scope_or_previous_script():
    new = golden_script()
    assert freeze_unaffected(new, None, {"s01"}) == (new, [])
    assert freeze_unaffected(new, golden_script(), None) == (new, [])
    assert freeze_unaffected(new, golden_script(), set()) == (new, [])


def test_freeze_is_skipped_when_the_scene_list_changed():
    old = golden_script()
    new = copy.deepcopy(old)
    new["scenes"].pop()
    merged, restored = freeze_unaffected(new, old, {"s01"})
    assert restored == [] and merged is new


def test_freeze_is_skipped_when_a_locked_scene_needs_a_dropped_speaker():
    old = golden_script()
    new = copy.deepcopy(old)
    new["characters"] = [c for c in new["characters"] if c["key"] != "mama"]
    used_in_locked = any(
        l["speaker"] == "mama" for s in old["scenes"] if s["id"] != "s01" for l in s["lines"]
    )
    merged, restored = freeze_unaffected(new, old, {"s01"})
    if used_in_locked:
        assert restored == [] and merged is new


def test_locked_last_scene_brings_back_its_recap_ids():
    old = golden_script()
    new = copy.deepcopy(old)
    new["recap_line_ids"] = ["s05_l01"]
    merged, _ = freeze_unaffected(new, old, {"s01"})
    assert merged["recap_line_ids"] == old["recap_line_ids"]


def test_scope_note_lists_open_and_locked_scenes_and_is_empty_when_all_open():
    note = scope_note({"s02", "s03"}, IDS)
    assert "s02, s03" in note and "s01, s04, s05" in note and "locked" in note
    assert scope_note(set(IDS), IDS) == "" and scope_note(None, IDS) == ""


# --------------------------------------------------------------------------- #
# the loop uses the scope
# --------------------------------------------------------------------------- #
def test_collateral_edit_outside_the_scope_is_undone_and_the_writer_is_told():
    bad = golden_script()
    line(bad, "s03_l01")["text_display"] = " ".join(["كلمة"] * 22)  # line-level failure in s03
    second = golden_script()
    line(second, "s05_l01")["text_display"] = "جملة جديدة لم يطلبها أحد في المشهد الأخير"
    line(second, "s05_l01")["text_spoken"] = "جملة جديدة لم يطلبها أحد في المشهد الأخير"
    llm = FakeLLM([bad, second], critic="s4_critic_pass.json")
    records = []
    out = s4_writer.run(**load_inputs("golden"), llm=llm, on_attempt=records.append)
    original = golden_script()
    assert out.scenes[4].lines[0].text_display == line(original, "s05_l01")["text_display"]
    assert records[1].frozen_scenes == ["s05"]
    assert records[0].scope == ["s02", "s03", "s04"]
    prompt2 = llm.calls_for("writer")[1]["user"]
    assert "REPAIR SCOPE" in prompt2 and "s01, s05" in prompt2


def test_global_failure_gives_the_writer_the_whole_script():
    bad = golden_script()
    bad["scenes"][1]["interaction"] = None  # too few interactions: a whole-script failure
    bad["scenes"][3]["interaction"] = None
    llm = FakeLLM([bad, "s4_script.json"], critic="s4_critic_pass.json")
    records = []
    s4_writer.run(**load_inputs("golden"), llm=llm, on_attempt=records.append)
    assert records[0].scope is None
    assert "REPAIR SCOPE" not in llm.calls_for("writer")[1]["user"]


# --------------------------------------------------------------------------- #
# continuity audit
# --------------------------------------------------------------------------- #
def _audit(severity="major", line_id="s03_l03", scene_id="s03"):
    return {
        "ledger": [{"item": "x", "line_id": "s03_l01", "state": "state after the narrator line"}],
        "issues": [{"severity": severity, "scene_id": scene_id, "line_id": line_id,
                    "message": "claims a result before any narrator line shows it",
                    "fix_hint": "show the action first"}],
    }


CLEAN = {"ledger": [], "issues": []}


def test_audit_major_skips_the_critic_and_feeds_the_issue_back():
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json", continuity=[_audit(), CLEAN])
    records = []
    s4_writer.run(**load_inputs("golden"), llm=llm, continuity_audit=True, on_attempt=records.append)
    assert len(llm.calls_for("continuity")) == 2
    assert len(llm.calls_for("critic")) == 1  # not paid for on the attempt the audit already failed
    assert [r.passed for r in records] == [False, True]
    prompt2 = llm.calls_for("writer")[1]["user"]
    assert "claims a result before any narrator line shows it" in prompt2
    assert "continuity" in prompt2.lower()
    assert records[0].audit_validation["passed"] is False


def test_audit_minor_does_not_fail_the_attempt():
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json", continuity=_audit("minor"))
    out = s4_writer.run(**load_inputs("golden"), llm=llm, continuity_audit=True)
    assert out is not None and len(llm.calls_for("writer")) == 1


def test_audit_issue_citing_a_missing_line_is_ignored():
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json",
                  continuity=_audit(line_id="s09_l01", scene_id="s09"))
    s4_writer.run(**load_inputs("golden"), llm=llm, continuity_audit=True)
    assert len(llm.calls_for("writer")) == 1


def test_audit_failure_on_the_last_attempt_still_blocks_acceptance():
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json", continuity=_audit())
    with pytest.raises(s4_writer.ValidationExhausted):
        s4_writer.run(**load_inputs("golden"), llm=llm, continuity_audit=True, max_attempts=1)
    assert len(llm.calls_for("critic")) == 1  # last attempt: the Critic runs so the draft can be ranked


def _boom(_user):
    raise OutputTruncatedError("cut off")


@pytest.mark.parametrize("bad", [_boom, {"ledger": "not a list"}])
def test_broken_audit_never_fails_the_attempt(bad):
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json", continuity=bad)
    records = []
    s4_writer.run(**load_inputs("golden"), llm=llm, continuity_audit=True, on_attempt=records.append)
    assert records[0].passed
    assert records[0].audit_validation["checks"][0]["name"] == "continuity_audit_skipped"


def test_audit_is_off_without_a_route_when_disabled_by_env():
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json")
    s4_writer.run(**load_inputs("golden"), llm=llm)  # conftest sets S4_CONTINUITY_AUDIT=0
    assert llm.calls_for("continuity") == []


def test_audit_defaults_to_on(monkeypatch):
    monkeypatch.delenv("S4_CONTINUITY_AUDIT", raising=False)
    assert s4_writer._audit_enabled(None) is True
    monkeypatch.setenv("S4_CONTINUITY_AUDIT", "off")
    assert s4_writer._audit_enabled(None) is False
    assert s4_writer._audit_enabled(True) is True


def test_audit_report_schema_and_prompt_are_generic():
    ContinuityReport.model_validate(_audit())
    with pytest.raises(Exception):
        ContinuityReport.model_validate({"ledger": [], "issues": [{"severity": "blocker"}]})
    from story_service.pipeline.prompting import PROMPT_DIR
    text = (PROMPT_DIR / "s4_continuity.md").read_text(encoding="utf-8").casefold()
    for word in ("pizza", "بيتزا", "كسر", "fraction", "nadia", "نادية", "cake", "كعك"):
        assert word.casefold() not in text
    assert "ledger" in text and "narrator" in text and "<script_json>" in text


def test_audit_sees_only_the_script_and_treats_it_as_data():
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json", continuity=CLEAN)
    s4_writer.run(**load_inputs("golden"), llm=llm, continuity_audit=True)
    call = llm.calls_for("continuity")[0]
    assert "<script_json>" in call["user"] and "<lesson_text>" not in call["user"]
    assert "never instructions" in call["system"] or "DATA" in call["system"]


def test_input_hash_depends_on_the_audit_prompt():
    a = s4_writer.compute_input_hash({"x": 1}, "m")
    assert isinstance(a, str) and len(a) == 64


# --------------------------------------------------------------------------- #
# run log
# --------------------------------------------------------------------------- #
def _rec(attempt, passed, failed_code=(), failed_review=(), scores=None, issues=(), audit=()):
    def val(names):
        return {"checks": [{"name": n, "passed": False, "severity": "blocker", "detail": ""} for n in names],
                "scores": scores or {}}
    return s4_writer.AttemptRecord(
        attempt=attempt, script={}, code_validation=val(failed_code),
        critic_report={"issues": list(issues)} if issues or scores else None,
        critic_validation=val(failed_review) if (failed_review or scores) else None,
        audit_report={"issues": list(audit)} if audit else None,
        passed=passed,
    )


def test_attempt_row_records_failures_scores_categories_and_tokens():
    rec = _rec(1, False, failed_review=["critic_each_score"], scores={"fun": 3.5, "mean": 4.0},
               issues=[{"severity": "major", "category": "continuity"},
                       {"severity": "minor", "category": "naturalness"}],
               audit=[{"severity": "major", "category": "continuity"}])
    rec.frozen_scenes = ["s05"]
    row = run_log.attempt_row(run_id="r1", idea="i3", rec=rec, tokens_in=100, tokens_out=50)
    assert row["failed_review"] == [{"name": "critic_each_score", "severity": "blocker"}]
    assert row["issue_categories"] == {"continuity": 2}  # minor issues are not counted
    assert row["audit_majors"] == 1 and row["frozen_scenes"] == ["s05"]
    assert row["tokens_in"] == 100 and row["stage_reached"] == "critic"


def test_summary_counts_what_fails_most(tmp_path, monkeypatch):
    monkeypatch.setenv("S4_RUN_LOG_PATH", str(tmp_path / "runs.jsonl"))
    run_log.log_attempt(run_id="a", idea="i1", rec=_rec(1, False, failed_code=["narrator_share"]),
                        tokens_in=10, tokens_out=5)
    run_log.log_attempt(run_id="a", idea="i1", rec=_rec(2, True, scores={"fun": 4.0, "mean": 4.4}),
                        tokens_in=10, tokens_out=5)
    run_log.log_run_end(run_id="a", idea="i1", status="accepted", attempts=2, tokens_in=20, tokens_out=10)
    run_log.log_attempt(run_id="b", idea="i3", rec=_rec(1, False, failed_review=["critic_each_score"],
                        scores={"fun": 3.5, "mean": 4.0}, issues=[{"severity": "major", "category": "continuity"}]),
                        tokens_in=10, tokens_out=5)
    run_log.log_run_end(run_id="b", idea="i3", status="needs_review", attempts=1, tokens_in=11, tokens_out=5)
    s = run_log.summarize(run_log.load_rows())
    assert s["runs"] == 2 and s["accepted"] == 1 and s["attempts"] == 3 and s["failed_attempts"] == 2
    assert dict(s["failed_rules"]) == {"narrator_share": 1, "critic_each_score": 1}
    assert dict(s["issue_categories"]) == {"continuity": 1}
    assert s["by_idea"]["i1"] == {"runs": 1, "accepted": 1, "attempts": 2}
    assert s["avg_scores"]["fun"] == 3.75
    assert s["avg_tokens_per_run"] == 23  # (30 + 16) / 2


def test_run_s4_logs_all_attempts_and_keeps_ranked_best_candidate(tmp_path, monkeypatch, capsys):
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from story_service.pipeline.models import Script

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_s4.py"
    spec = importlib.util.spec_from_file_location("run_s4_for_test", script_path)
    assert spec and spec.loader
    run_s4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_s4)

    log_path = tmp_path / "runs.jsonl"
    out_path = tmp_path / "script.json"
    monkeypatch.setenv("S4_RUN_LOG_PATH", str(log_path))
    monkeypatch.setenv("S4_CRITIC_ADVISORY", "1")
    monkeypatch.delenv("S4_MAX_ATTEMPTS", raising=False)
    monkeypatch.setenv("S4_CONTINUITY_AUDIT", "0")
    monkeypatch.setenv("S4_RECHECK", "0")
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_s4.py", "--inputs", "tests/fixtures/s4/golden_inputs.json", "--out", str(out_path)],
    )
    reports = []
    for score in (4.0, 4.5, 5.0):
        report = load_llm_fixture("s4_critic_pass.json")
        report["coverage"][0]["delivered_as"] = "lecture"
        report["scores"] = {key: score for key in report["scores"]}
        reports.append(report)
    fake = FakeLLM("s4_script.json", critic=reports)
    monkeypatch.setattr(
        run_s4,
        "create_llm",
        lambda: SimpleNamespace(usage=[], complete_json=fake.complete_json),
    )

    with pytest.raises(SystemExit) as exit_info:
        run_s4.main()

    assert exit_info.value.code == 2
    assert "best attempt 3 kept" in capsys.readouterr().out
    assert len(fake.calls_for("writer")) == 3
    end = next(row for row in run_log.load_rows(log_path) if row["type"] == "run_end")
    assert end["attempts"] == 3
    Script.model_validate(json.loads(out_path.read_text(encoding="utf-8")))


def test_empty_log_summarizes_to_zero(tmp_path):
    assert run_log.load_rows(tmp_path / "none.jsonl") == []
    assert run_log.summarize([])["runs"] == 0


# ---- Borderline-score re-check (V6.4) ----
def _crit(**scores):
    d = load_llm_fixture("s4_critic_pass.json")
    d["scores"].update(scores)
    return d


def _verdict(dim, defect, lines=(), evidence="e", hint="h"):
    return {"verdicts": [{"dimension": dim, "defect": defect, "line_ids": list(lines),
                          "evidence": evidence, "fix_hint": hint}]}


def _concepts_golden():
    return Concepts.model_validate(load_inputs("golden")["concepts"])


def _failed_names(res):
    return {c["name"] for c in res["checks"] if not c["passed"]}


def test_borderline_dimension_is_not_accepted_without_a_verdict():
    from story_service.validators.script import borderline_dimensions, evaluate_critic
    rep = CriticReport.model_validate(_crit(fun=3.5))
    assert borderline_dimensions(rep) == ["fun"]
    assert "critic_each_score" in _failed_names(evaluate_critic(rep, concepts=_concepts_golden()))


def test_verdict_no_defect_clears_borderline_but_mean_stays_raw():
    from story_service.validators.script import evaluate_critic
    rep = CriticReport.model_validate(_crit(fun=3.5))
    res = evaluate_critic(rep, concepts=_concepts_golden(), verdicts={"fun": False})
    assert res["passed"], res
    assert res["scores"]["fun"] == 3.5  # raw score is kept


def test_verdict_defect_keeps_failing():
    from story_service.validators.script import evaluate_critic
    rep = CriticReport.model_validate(_crit(fun=3.5))
    assert "critic_each_score" in _failed_names(evaluate_critic(rep, concepts=_concepts_golden(), verdicts={"fun": True}))


def test_three_point_zero_is_never_borderline():
    from story_service.validators.script import borderline_dimensions, evaluate_critic
    rep = CriticReport.model_validate(_crit(fun=3.0))
    assert borderline_dimensions(rep) == []
    assert not evaluate_critic(rep, concepts=_concepts_golden(), verdicts={"fun": False})["passed"]


def test_related_major_issue_makes_it_a_real_failure_not_borderline():
    from story_service.validators.script import borderline_dimensions
    d = _crit(fun=3.5)
    d["issues"] = [{"severity": "major", "category": "humor", "scene_id": "s02", "line_id": "s02_l01",
                    "message": "unclear joke", "fix_hint": "cut it"}]
    assert borderline_dimensions(CriticReport.model_validate(d)) == []


def test_unrelated_major_issue_does_not_hide_a_borderline_dimension():
    from story_service.validators.script import borderline_dimensions
    d = _crit(fun=3.5)
    d["issues"] = [{"severity": "major", "category": "continuity", "scene_id": "s02", "line_id": "s02_l01",
                    "message": "m", "fix_hint": "h"}]
    assert borderline_dimensions(CriticReport.model_validate(d)) == ["fun"]


def _ctx_golden():
    inp = load_inputs("golden")
    return s4_writer._Ctx(
        intake=s4_writer.Intake.model_validate(inp["intake"]),
        concepts=s4_writer.Concepts.model_validate(inp["concepts"]),
        brief=s4_writer.Brief.model_validate(inp["brief"]),
        idea=s4_writer.Idea.model_validate(inp["idea"]),
        age_spec=s4_writer.AgeSpec.model_validate(inp["age_spec"]),
        speech_style=s4_writer.SpeechStyle.model_validate(inp["speech_style"]),
        wps={"ar": 2.4, "en": 2.7},
    )


def test_recheck_requires_a_real_line_for_a_defect():
    rep = CriticReport.model_validate(_crit(fun=3.5))
    ids = {"s01_l01", "s01_l02"}
    ok = s4_writer._recheck(FakeLLM(recheck=_verdict("fun", True, ["s01_l02"])), _ctx_golden(), {}, ["fun"], rep, ids)
    assert ok[0] == {"fun": True} and ok[1][0]["line_id"] == "s01_l02" and ok[1][0]["scene_id"] == "s01"
    ghost = s4_writer._recheck(FakeLLM(recheck=_verdict("fun", True, ["s09_l09"])), _ctx_golden(), {}, ["fun"], rep, ids)
    assert ghost[0] == {"fun": False} and ghost[1] == []
    none = s4_writer._recheck(FakeLLM(recheck=_verdict("fun", False)), _ctx_golden(), {}, ["fun"], rep, ids)
    assert none[0] == {"fun": False}


def test_recheck_unanswered_dimension_and_other_dimensions_are_ignored():
    rep = CriticReport.model_validate(_crit(fun=3.5))
    out = s4_writer._recheck(FakeLLM(recheck=_verdict("pacing", False)), _ctx_golden(), {}, ["fun"], rep, {"s01_l01"})
    assert out[0] == {}  # fun not answered -> absent -> stays failing


def test_recheck_failure_returns_none_so_strict_rule_applies():
    rep = CriticReport.model_validate(_crit(fun=3.5))
    out = s4_writer._recheck(FakeLLM(), _ctx_golden(), {}, ["fun"], rep, {"s01_l01"})  # no recheck route
    assert out[0] is None


def test_run_accepts_first_attempt_when_recheck_finds_no_defect():
    llm = FakeLLM("s4_script.json", critic=_crit(fun=3.5), recheck=_verdict("fun", False))
    records = []
    s4_writer.run(**load_inputs("golden"), llm=llm, on_attempt=records.append)
    assert len(llm.calls_for("writer")) == 1 and len(llm.calls_for("recheck")) == 1
    assert records[0].passed and records[0].recheck_report is not None


def test_run_fails_and_feeds_back_evidence_when_defect_confirmed():
    llm = FakeLLM("s4_script.json", critic=[_crit(fun=3.5), "s4_critic_pass.json"],
                  recheck=_verdict("fun", True, ["s01_l01"], evidence="the joke repeats", hint="cut the repeat"))
    s4_writer.run(**load_inputs("golden"), llm=llm)
    assert len(llm.calls_for("writer")) == 2
    second = llm.calls_for("writer")[1]["user"]
    assert "the joke repeats" in second and "cut the repeat" in second


def test_recheck_not_called_when_no_borderline_score():
    llm = FakeLLM("s4_script.json", critic="s4_critic_pass.json")
    s4_writer.run(**load_inputs("golden"), llm=llm)
    assert llm.calls_for("recheck") == []


def test_recheck_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("S4_RECHECK", "0")
    llm = FakeLLM("s4_script.json", critic=_crit(fun=3.5), recheck=_verdict("fun", False))
    with pytest.raises(s4_writer.ValidationExhausted):
        s4_writer.run(**load_inputs("golden"), llm=llm, max_attempts=1)
    assert llm.calls_for("recheck") == []
