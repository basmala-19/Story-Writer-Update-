"""Aggregate S4 run log (JSON Lines): one row per attempt and one per run.

Reading runs one at a time does not show which failure is most common. This log keeps the rules,
issue categories, model scores, tokens, and compact safety/continuity/source-accuracy audit reports
needed to review why a draft passed or failed. Full attempt scripts are opt-in via
`S4_RUN_LOG_FULL_SCRIPTS=true`; prompts are never stored.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RUN_LOG_PATH = "logs/s4_runs.jsonl"


def run_log_path() -> Path:
    return Path(os.getenv("S4_RUN_LOG_PATH", DEFAULT_RUN_LOG_PATH))


def _append(row: dict[str, Any], path: Path | None = None) -> None:
    path = path or run_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _failed(validation: dict[str, Any] | None) -> list[dict[str, str]]:
    if not validation:
        return []
    return [
        {"name": c["name"], "severity": c["severity"],
         "detail": str(c.get("detail", ""))[:1200]}
        for c in validation.get("checks", [])
        if not c["passed"] and c["severity"] != "minor"
    ]


def attempt_row(
    *, run_id: str, idea: str, rec: Any, tokens_in: int, tokens_out: int, model: str | None = None
) -> dict[str, Any]:
    crit = rec.critic_validation or {}
    scores = dict((crit.get("scores") or {}))
    report_issues = list((rec.critic_report or {}).get("issues", []))
    audit_issues = list((rec.audit_report or {}).get("issues", []))
    safety_issues = [
        {"severity": "blocker", "category": "safety_culture"}
        for u in (rec.safety_validation or {}).get("unsafe", [])
    ]
    return {
        "type": "attempt",
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "idea": idea,
        "model": model,
        "attempt": rec.attempt,
        "passed": bool(rec.passed),
        "stage_reached": (
            "educational_accuracy" if getattr(rec, "fact_accuracy_validation", None) is not None
            else "audit" if (rec.audit_validation is not None or rec.safety_validation is not None)
            else "critic" if rec.critic_report is not None
            else "code"
        ),
        "failed_code": _failed(rec.code_validation),
        "failed_review": _failed(rec.critic_validation),
        "scores": scores,
        "critic_fact_checks": (rec.critic_report or {}).get("fact_checks", []),
        "critic_issues": report_issues,
        "issue_categories": dict(Counter(i.get("category") or "uncategorised"
                                         for i in report_issues + audit_issues + safety_issues
                                         if i.get("severity") in ("blocker", "major"))),
        "audit_majors": sum(1 for i in audit_issues if i.get("severity") == "major"),
        "continuity_audit": rec.audit_report,
        "continuity_validation": rec.audit_validation,
        "safety_audit": rec.safety_report,
        "safety_validation": rec.safety_validation,
        "educational_accuracy_audit": getattr(rec, "fact_accuracy_report", None),
        "educational_accuracy_validation": getattr(rec, "fact_accuracy_validation", None),
        "frozen_scenes": list(getattr(rec, "frozen_scenes", []) or []),
        "scope": getattr(rec, "scope", None),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "auditor_metrics": getattr(rec, "auditor_metrics", {}) or {},
        # Full story snapshots can make logs large and may contain user-provided lesson content,
        # so they are only emitted when explicitly enabled for a debugging run.
        **({
            "writer_raw_script": getattr(rec, "writer_raw_script", None),
            "processed_script": getattr(rec, "script", None),
        } if os.getenv("S4_RUN_LOG_FULL_SCRIPTS", "false").strip().lower() in {"1", "true", "yes", "on"} else {}),
        **(_repair_debug_details(rec) if os.getenv("S4_RUN_LOG_REPAIR_DETAILS", "false").strip().lower()
           in {"1", "true", "yes", "on"} else {}),
        "auditor_issue_overlap": _approximate_auditor_issue_overlap(getattr(rec, "auditor_metrics", {}) or {}),
    }



def _repair_debug_details(rec: Any) -> dict[str, Any]:
    """Opt-in trace of the feedback prepared for the next retry and this attempt's hard checks.

    Feedback may contain lesson/story text, so it is excluded from logs unless explicitly enabled.
    The compact validation snapshot makes it possible to see which constraints passed or failed
    at each retry without storing the full script or full prompt.
    """
    validations = {}
    for name, validation in (("code", getattr(rec, "code_validation", None)),
                             ("critic", getattr(rec, "critic_validation", None))):
        if not isinstance(validation, dict):
            continue
        validations[name] = {
            "passed": validation.get("passed"),
            "checks": [
                {"name": check.get("name"), "passed": check.get("passed"),
                 "severity": check.get("severity"), "detail": str(check.get("detail", ""))[:500],
                 **({"data": check["data"]} if isinstance(check.get("data"), dict) else {})}
                for check in validation.get("checks", [])
                if isinstance(check, dict)
                and (check.get("severity") in ("blocker", "major") or not check.get("passed", True))
            ],
        }
    return {
        "repair_feedback_for_next": getattr(rec, "feedback_for_next", None),
        "repair_scope": getattr(rec, "scope", None),
        "repair_validation_snapshot": validations,
    }

def _approximate_auditor_issue_overlap(metrics: dict[str, Any]) -> dict[str, Any]:
    """Approximate cross-auditor overlap by exact (line_id, category) keys, not semantics."""
    owners: dict[tuple[str, str], list[str]] = defaultdict(list)
    for auditor, metric in metrics.items():
        for issue in metric.get("issue_keys", []) if isinstance(metric, dict) else []:
            line_id, category = issue.get("line_id"), issue.get("category")
            if line_id and category:
                owners[(str(line_id), str(category))].append(auditor)
    shared = [
        {"line_id": line_id, "category": category, "auditors": sorted(set(auditors))}
        for (line_id, category), auditors in owners.items() if len(set(auditors)) > 1
    ]
    return {
        "method": "approximate_exact_line_id_and_category_overlap; not semantic identity",
        "shared_issue_keys": shared,
        "unique_issue_keys_by_auditor": {
            auditor: sum(1 for issue in metric.get("issue_keys", [])
                         if issue.get("line_id") and issue.get("category")
                         and len(set(owners.get((str(issue["line_id"]), str(issue["category"])), []))) == 1)
            for auditor, metric in metrics.items() if isinstance(metric, dict)
        },
    }


def log_attempt(**kwargs: Any) -> dict[str, Any]:
    row = attempt_row(**kwargs)
    _append(row)
    return row


def log_run_end(
    *, run_id: str, idea: str, status: str, attempts: int, tokens_in: int, tokens_out: int,
    model: str | None = None,
) -> dict[str, Any]:
    row = {
        "type": "run_end", "ts": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
        "idea": idea, "model": model, "status": status, "attempts": attempts,
        "tokens_in": tokens_in, "tokens_out": tokens_out,
    }
    _append(row)
    return row


def load_rows(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or run_log_path()
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts that answer 'what fails most?' across runs. Pure function, easy to test."""
    ends = [r for r in rows if r["type"] == "run_end"]
    attempts = [r for r in rows if r["type"] == "attempt"]
    by_idea: dict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "accepted": 0, "attempts": 0})
    for r in ends:
        d = by_idea[r["idea"]]
        d["runs"] += 1
        d["accepted"] += r["status"] == "accepted"
        d["attempts"] += r["attempts"]

    failed_rules: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    sums: dict[str, list[float]] = defaultdict(list)
    for a in attempts:
        for c in a["failed_code"] + a["failed_review"]:
            failed_rules[c["name"]] += 1
        categories.update(a["issue_categories"])
        for k, v in (a["scores"] or {}).items():
            sums[k].append(v)

    return {
        "runs": len(ends),
        "accepted": sum(r["status"] == "accepted" for r in ends),
        "attempts": len(attempts),
        "failed_attempts": sum(not a["passed"] for a in attempts),
        "by_idea": dict(by_idea),
        "failed_rules": failed_rules.most_common(),
        "issue_categories": categories.most_common(),
        "avg_scores": {k: round(sum(v) / len(v), 2) for k, v in sorted(sums.items())},
        "avg_tokens_per_run": (
            round(sum(r["tokens_in"] + r["tokens_out"] for r in ends) / len(ends)) if ends else 0
        ),
        "attempts_frozen": sum(1 for a in attempts if a["frozen_scenes"]),
    }
