"""Run a repeatable, same-settings comparison between S4 project versions.

Example (uses configured provider/model and can consume substantial API tokens):
  uv run python scripts/compare_versions.py --version old=C:/work/story_writer_old --version current=.

Default cases: golden/i1, i2, i3, and the book-lesson sample; three runs each.
Writes raw stories, prompts' hashes, a Markdown summary, and an unfilled human-review queue under reports/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = {
    "golden_i1": "tests/fixtures/s4/golden_inputs.json",
    "i2": "tests/fixtures/s4/inputs_i2.json",
    "i3": "tests/fixtures/s4/inputs_i3.json",
    "book_lesson": "tests/fixtures/s4/book_lesson1_inputs.json",
}
PROMPTS = ("s4_writer.md", "s4_critic.md", "s4_continuity.md", "s4_safety.md", "s4_fact_accuracy.md")
INCLUDE = re.compile(r"^\s*\{\{include:([A-Za-z0-9_.-]+)\}\}\s*$", re.MULTILINE)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_sources(project: Path) -> dict[str, Path]:
    """Resolve every prompt module used by the three S4 roles for provenance snapshots."""
    prompt_dir = project / "src/story_service/pipeline/prompts"
    found: dict[str, Path] = {}

    def visit(name: str) -> None:
        path = (prompt_dir / name).resolve()
        if not path.is_relative_to(prompt_dir.resolve()):
            raise ValueError(f"prompt include escapes prompt directory: {name}")
        relative = path.relative_to(prompt_dir.resolve()).as_posix()
        if relative in found:
            return
        content = path.read_text(encoding="utf-8")
        found[relative] = path
        for child in INCLUDE.findall(content):
            visit(child)

    # Older project snapshots have safety guidance embedded in the writer and
    # critic prompts rather than a standalone safety module. Include any split
    # modules that exist, while retaining the core writer/critic requirements.
    for index, name in enumerate(PROMPTS):
        if not (prompt_dir / name).is_file():
            if index < 2:
                raise FileNotFoundError(f"required prompt is missing: {prompt_dir / name}")
            continue
        visit(name)
    return found


def snapshot_source(project: Path, destination: Path) -> str:
    """Copy source/config needed to reproduce S4, excluding local secrets and runtime outputs."""
    destination.mkdir(parents=True, exist_ok=True)
    sources = ("src", "scripts/run_s4.py", "pyproject.toml", "uv.lock")
    h = hashlib.sha256()
    for relative in sources:
        source = project / relative
        target = destination / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
            files = sorted(path for path in target.rglob("*") if path.is_file())
            for file in files:
                name = file.relative_to(destination).as_posix()
                h.update(name.encode("utf-8") + b"\0" + file.read_bytes() + b"\0")
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            h.update(relative.encode("utf-8") + b"\0" + target.read_bytes() + b"\0")
    return h.hexdigest()


def parse_version(raw: str) -> tuple[str, Path]:
    name, sep, folder = raw.partition("=")
    if not sep or not name or not folder:
        raise argparse.ArgumentTypeError("versions must use NAME=PROJECT_DIRECTORY")
    path = Path(folder).expanduser().resolve()
    if not (path / "scripts/run_s4.py").is_file():
        raise argparse.ArgumentTypeError(f"{path} has no scripts/run_s4.py")
    return name, path


def parse_cases(raw: str | None) -> dict[str, Path]:
    if not raw:
        return {name: ROOT / rel for name, rel in DEFAULT_CASES.items()}
    result = {}
    for item in raw.split(","):
        name, sep, value = item.partition("=")
        if not sep:
            raise ValueError("--cases entries must use NAME=INPUT_JSON")
        path = Path(value)
        result[name] = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    return result


def resolve_settings(project: Path, overrides: dict) -> dict:
    local = {k: v for k, v in dotenv_values(project / ".env").items() if v is not None}
    provider = overrides.get("provider") or os.getenv("LLM_ACTIVE_PROVIDER") or local.get("LLM_ACTIVE_PROVIDER", "openrouter")
    provider = provider.strip().lower()
    prefix = {"openrouter": "OPENROUTER", "google": "GOOGLE", "groq": "GROQ", "hetzner": "HETZNER"}.get(provider)
    if not prefix:
        raise ValueError(f"unsupported provider in comparison: {provider}")
    defaults = {
        "openrouter": ("google/gemini-2.5-flash", "google/gemini-2.5-flash-lite"),
        "google": ("gemini-2.5-flash", "gemini-2.5-flash-lite"),
        "groq": ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
        "hetzner": ("Qwen/Qwen3.6-35B-A3B-FP8", "Qwen3.8-27B"),
    }
    large = overrides.get("large_model") or os.getenv(f"{prefix}_MODEL_LARGE") or local.get(f"{prefix}_MODEL_LARGE") or defaults[provider][0]
    small = overrides.get("small_model") or os.getenv(f"{prefix}_MODEL_SMALL") or local.get(f"{prefix}_MODEL_SMALL") or defaults[provider][1]
    return {"provider": provider, "large_model": large, "small_model": small}


def run_once(name: str, project: Path, case: str, input_path: Path, run_no: int,
             artifact_dir: Path, settings: dict, token_limit: int,
             source_hash: str | None = None, source_snapshot: str | None = None) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output = artifact_dir / f"{name}__{case}__run{run_no}.json"
    log_path = artifact_dir / f"{name}__{case}__run{run_no}.jsonl"
    env = os.environ.copy()
    project_src = str(project / "src")
    env["PYTHONPATH"] = project_src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["S4_RUN_LOG_PATH"] = str(log_path)
    env["LLM_ACTIVE_PROVIDER"] = settings["provider"]
    prefix = settings["provider"].upper()
    if settings.get("large_model"):
        env[f"{prefix}_MODEL_LARGE"] = settings["large_model"]
    if settings.get("small_model"):
        env[f"{prefix}_MODEL_SMALL"] = settings["small_model"]
    env.update({"MAX_TOKENS_PER_STORY": str(token_limit), "S4_MAX_ATTEMPTS": "3",
                "S4_WRITER_MAX_TOKENS": "20000", "S4_CRITIC_MAX_TOKENS": "8000",
                "S4_CONTINUITY_MAX_TOKENS": "6000", "S4_MAX_INPUT_TOKENS": "",
                "S4_CRITIC_ADVISORY": "0",
                "S4_CONTINUITY_AUDIT": "1"})
    # Reuse the comparison runner's installed environment. Running `uv run` in a
    # snapshot directory can trigger network-only build isolation even when all
    # runtime dependencies are already available locally.
    command = [sys.executable, "scripts/run_s4.py", "--inputs", str(input_path), "--out", str(output)]
    started = datetime.now(timezone.utc).isoformat()
    proc = subprocess.run(command, cwd=project, env=env, text=True, capture_output=True)
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if log_path.exists() else []
    end = next((row for row in reversed(rows) if row.get("type") == "run_end"), {})
    attempt_rows = [row for row in rows if row.get("type") == "attempt"]
    score_values = [row.get("scores", {}) for row in attempt_rows if row.get("scores")]
    failed_rules = {}
    for row in attempt_rows:
        for item in row.get("failed_code", []) + row.get("failed_review", []):
            failed_rules[item["name"]] = failed_rules.get(item["name"], 0) + 1
    sources = prompt_sources(project)
    prompt_hashes = {file: digest(path) for file, path in sources.items()}
    prompt_snapshot_dir = artifact_dir / "prompts" / name
    prompt_snapshot_dir.mkdir(parents=True, exist_ok=True)
    prompt_files = {}
    for filename, source_path in sources.items():
        snapshot = prompt_snapshot_dir / filename
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(source_path.read_bytes())
        prompt_files[filename] = str(snapshot)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, text=True,
                              capture_output=True, check=False)
    return {
        "version": name, "case": case, "run": run_no, "started_at": started,
        "provider": settings["provider"], "large_model": settings.get("large_model"),
        "small_model": settings.get("small_model"), "token_limit": token_limit,
        "exit_code": proc.returncode,
        "status": end.get("status", "service_error" if proc.returncode else "missing_run_end"),
        "service_error": ("; ".join(proc.stderr.strip().splitlines()[-3:])[:500]
                          if not end and proc.returncode else None),
        "attempts": end.get("attempts", len(attempt_rows)),
        "tokens_in": end.get("tokens_in", sum(row.get("tokens_in", 0) for row in attempt_rows)),
        "tokens_out": end.get("tokens_out", sum(row.get("tokens_out", 0) for row in attempt_rows)),
        "scores_by_attempt": score_values, "failed_rules": failed_rules,
        "prompt_sha256": prompt_hashes, "prompt_files": prompt_files,
        "source_sha256": source_hash, "source_snapshot": source_snapshot,
        "git_revision": revision.stdout.strip() if revision.returncode == 0 else None,
        "input_sha256": digest(input_path),
        "story_file": str(output) if output.is_file() else None,
        "stdout_file": str(artifact_dir / f"{name}__{case}__run{run_no}.stdout.txt"),
        "stderr_file": str(artifact_dir / f"{name}__{case}__run{run_no}.stderr.txt"),
    }, proc.stdout, proc.stderr


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", action="append", type=parse_version, required=True,
                    help="version label and project directory, repeat at least twice")
    ap.add_argument("--cases", help="comma-separated case=inputs.json; default uses four fixed fixtures")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--reports", default=str(ROOT / "reports"))
    ap.add_argument("--provider", choices=("openrouter", "google", "groq", "hetzner"),
                    help="force the same provider for every project")
    ap.add_argument("--large-model", help="force the same large model for every project")
    ap.add_argument("--small-model", help="force the same small model for every project")
    ap.add_argument("--token-limit", type=int, default=600000,
                    help="same MAX_TOKENS_PER_STORY cap for each run")
    args = ap.parse_args()
    if len(args.version) < 2:
        ap.error("provide at least two --version arguments")
    if args.runs < 1:
        ap.error("--runs must be at least 1")
    cases = parse_cases(args.cases)
    if args.token_limit < 1:
        ap.error("--token-limit must be positive")
    overrides = {"provider": args.provider, "large_model": args.large_model, "small_model": args.small_model}
    settings_by_version = {name: resolve_settings(project, overrides) for name, project in args.version}
    distinct_settings = {(x["provider"], x["large_model"], x["small_model"])
                         for x in settings_by_version.values()}
    if len(distinct_settings) != 1:
        ap.error("versions have different provider/model settings; pass --provider, --large-model and --small-model to make the comparison fair")
    missing = [str(path) for path in cases.values() if not path.is_file()]
    if missing:
        ap.error("missing input files: " + ", ".join(missing))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    report_dir = Path(args.reports).resolve() / run_id
    artifacts = report_dir / "artifacts"
    report_dir.mkdir(parents=True, exist_ok=True)
    source_records = {
        name: (snapshot_source(project, artifacts / "source" / name),
               str(artifacts / "source" / name))
        for name, project in args.version
    }
    results = []
    review_queue = []
    for version_name, project in args.version:
        for case_name, input_path in cases.items():
            for run_no in range(1, args.runs + 1):
                record, stdout, stderr = run_once(version_name, project, case_name, input_path, run_no,
                                                  artifacts, settings_by_version[version_name], args.token_limit,
                                                  *source_records[version_name])
                Path(record["stdout_file"]).write_text(stdout, encoding="utf-8")
                Path(record["stderr_file"]).write_text(stderr, encoding="utf-8")
                results.append(record)
                review_queue.append({"version": version_name, "case": case_name, "run": run_no,
                                     "story_file": record["story_file"], "human_quality": None,
                                     "human_issues": [], "reviewer_notes": ""})
                print(f"{version_name}/{case_name} run {run_no}: {record['status']} "
                      f"({record['tokens_in'] + record['tokens_out']} tokens)")

    (report_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir / "human_review_queue.json").write_text(
        json.dumps(review_queue, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {}
    for version_name, _ in args.version:
        subset = [r for r in results if r["version"] == version_name]
        completed = [r for r in subset if r["status"] in ("accepted", "exhausted")]
        accepted = sum(r["status"] == "accepted" for r in completed)
        summary[version_name] = {
            "runs": len(subset), "valid_runs": len(completed), "service_errors": len(subset) - len(completed),
            "accepted": accepted,
            "acceptance_rate": accepted / len(completed) if completed else 0.0,
            "mean_attempts": round(sum(r["attempts"] for r in completed) / len(completed), 2) if completed else 0.0,
            "tokens": sum(r["tokens_in"] + r["tokens_out"] for r in subset),
            "failed_rules": {k: sum(r["failed_rules"].get(k, 0) for r in subset)
                             for k in sorted({k for r in subset for k in r["failed_rules"]})},
            "score_means": {
                dimension: round(sum(score[dimension] for r in completed for score in r["scores_by_attempt"]
                                     if dimension in score) /
                                sum(1 for r in completed for score in r["scores_by_attempt"] if dimension in score), 2)
                for dimension in sorted({key for r in completed for score in r["scores_by_attempt"] for key in score
                                         if key != "mean"})
            },
        }
    lines = ["# S4 version comparison", "", f"Run: `{run_id}`", "",
             f"Cases: {', '.join(cases)}; repeats per case/version: {args.runs}.",
             (f"Provider/model: {next(iter(distinct_settings))}; aggregate token cap: {args.token_limit:,} per run."),
             "The runner forces the same acceptance thresholds and strict pass mode across versions.", "",
             "| Version | Accepted / valid | Acceptance | Mean attempts | Tokens | Service errors |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, data in summary.items():
        lines.append(f"| {name} | {data['accepted']}/{data['valid_runs']} of {data['runs']} | {data['acceptance_rate']:.0%} | "
                     f"{data['mean_attempts']} | {data['tokens']:,} | {data['service_errors']} |")
    lines += ["", "## Rule failures", "", "Counts include failed rules across attempts, including partial attempts before a provider error.", ""]
    rules = sorted({rule for item in summary.values() for rule in item["failed_rules"]})
    lines.append("| Rule | " + " | ".join(summary) + " |")
    lines.append("|---" + "|---:" * len(summary) + "|")
    for rule in rules:
        lines.append("| " + rule + " | " + " | ".join(str(summary[name]["failed_rules"].get(rule, 0)) for name in summary) + " |")
    lines += ["", "## Mean Critic scores", "", "| Dimension | " + " | ".join(summary) + " |",
              "|---" + "|---:" * len(summary) + "|"]
    dims = sorted({d for x in summary.values() for d in x["score_means"]})
    for dim in dims:
        lines.append("| " + dim + " | " + " | ".join(str(summary[name]["score_means"].get(dim, "—")) for name in summary) + " |")
    service_errors = [r for r in results if r["status"] == "service_error"]
    if service_errors:
        lines += ["", "## Incomplete provider runs", "", "These runs are excluded from acceptance and score denominators; tokens from completed partial attempts are still included.", ""]
        for row in service_errors:
            lines.append(f"- {row['version']}/{row['case']} run {row['run']}: {row.get('service_error') or 'provider failure'}")
    lines += ["", "## Review and provenance", "",
              f"Human review queue: `{report_dir / 'human_review_queue.json'}` ({len(review_queue)} story runs; labels are blank).",
              "Review at least 20 judgments before interpreting quality changes. Read individual outputs and compare issue evidence.",
              "Prompt snapshots and SHA-256 hashes, Git revisions, input hashes, per-attempt scores, token usage, outputs and logs are recorded in `results.json` and `artifacts/`. Cost is reported as tokens because provider price schedules can differ and are not inferred here.",
              "", "No human-quality improvement is claimed by these automated metrics alone."]
    (report_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\\nReport: {report_dir / 'comparison.md'}")


if __name__ == "__main__":
    main()
