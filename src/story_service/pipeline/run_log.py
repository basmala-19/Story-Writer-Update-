"""Aggregate S4 run log (JSON Lines): one row per attempt and one per run.

Reading runs one at a time does not show which failure is most common. This log keeps the facts
needed to count them: which rule failed, which issue categories the Critic and the continuity audit
used, the scores, and the tokens each attempt cost. It never stores prompts or story text.
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
        {"name": c["name"], "severity": c["severity"]}
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
    return {
        "type": "attempt",
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "idea": idea,
        "model": model,
        "attempt": rec.attempt,
        "passed": bool(rec.passed),
        "stage_reached": (
            "critic" if rec.critic_report is not None
            else "audit" if rec.audit_validation is not None
            else "code"
        ),
        "failed_code": _failed(rec.code_validation),
        "failed_review": _failed(rec.critic_validation),
        "scores": scores,
        "issue_categories": dict(Counter(i.get("category") or "uncategorised"
                                         for i in report_issues + audit_issues
                                         if i.get("severity") in ("blocker", "major"))),
        "audit_majors": sum(1 for i in audit_issues if i.get("severity") == "major"),
        "recheck": {
            v.get("dimension"): bool(v.get("defect"))
            for v in ((getattr(rec, "recheck_report", None) or {}).get("verdicts") or [])
        },
        "frozen_scenes": list(getattr(rec, "frozen_scenes", []) or []),
        "scope": getattr(rec, "scope", None),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
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
