"""Targeted S4 regression cases and observability for validation-only failures.

These tests are offline and do not claim that a live model independently detects a defect.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from story_service.pipeline.repair import affected_scene_ids, expand_with_neighbours
from story_service.pipeline.stages import s4_writer

ROOT = Path(__file__).resolve().parents[2]


def _i2_story():
    return json.loads((ROOT / "tests/fixtures/s4/regression_i2_story.json").read_text(encoding="utf-8"))


def _all_lines(story):
    return {
        line["id"]: line
        for scene in story["scenes"]
        for line in scene["lines"]
    }


def test_i2_redundancy_regression_has_two_real_evidence_lines():
    """The ending may repeat the same learning point already stated by Yusuf."""
    story = _i2_story()
    lines = _all_lines(story)
    assert "جزء واحد من أربعة أجزاء" in lines["s06_l02"]["text_spoken"]
    assert "قسمنا الكعكة بالتساوي" in lines["s06_l06"]["text_spoken"]
    # This is a human-authored candidate for review, not an automatic semantic verdict.
    candidate = {
        "category": "redundancy",
        "line_ids": ["s06_l02", "s06_l06"],
        "reason": "Both lines restate that equal division makes the customer's share clear.",
    }
    assert all(line_id in lines for line_id in candidate["line_ids"])
    assert candidate["category"] == "redundancy"


def test_i2_redundancy_report_scopes_repair_to_the_affected_scene_and_neighbour():
    story = _i2_story()
    issue = {
        "scene_id": "s06",
        "line_id": "s06_l06",
        "category": "redundancy",
        "message": "This recap repeats the learning point stated in s06_l02.",
        "fix_hint": "Keep the stronger line or replace the recap with a new consequence.",
    }
    scoped = affected_scene_ids(
        {"checks": []},
        {"checks": [{"name": "critic_issues", "passed": False,
                     "severity": "major", "detail": issue["message"]}]},
        [issue],
    )
    all_scene_ids = [scene["id"] for scene in story["scenes"]]
    assert scoped == {"s06"}
    assert expand_with_neighbours(scoped, all_scene_ids) == {"s05", "s06"}


def test_auditor_metrics_count_validation_only_rejections(monkeypatch):
    """A report with no issue entries still records failed coverage/validation checks."""
    llm = SimpleNamespace(
        usage=[],
        calls=[],
        _s4_auditor_metrics_sink={},
    )

    def callback():
        llm.calls.append({"route": "continuity"})
        return (
            {"issues": []},
            {"passed": False, "checks": [{
                "name": "continuity_audit", "passed": False, "severity": "major",
                "detail": "interactions not reviewed: ['s02_i1']",
            }]},
        )

    result = s4_writer._measure_auditor_call(llm, "continuity", callback)
    metric = llm._s4_auditor_metrics_sink["continuity"]
    assert result[1]["passed"] is False
    assert metric["reported_issue_count"] == 0
    assert metric["validation_rejections"] == 1
    assert metric["failed_validation_check_count"] == 1
    assert metric["failed_validation_checks"][0]["name"] == "continuity_audit"
    assert metric["issues_found"] == 1
    assert metric["tokens_in"] is None and metric["tokens_out"] is None
    assert "not a semantic unique-issue count" in metric["issues_count_note"]
