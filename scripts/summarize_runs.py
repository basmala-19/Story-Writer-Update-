"""Summarise logs/s4_runs.jsonl: what fails most, per idea and overall.

    uv run python scripts/summarize_runs.py
    uv run python scripts/summarize_runs.py --log path/to/s4_runs.jsonl
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from story_service.pipeline.run_log import load_rows, run_log_path, summarize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None)
    args = ap.parse_args()
    path = Path(args.log) if args.log else run_log_path()
    rows = load_rows(path)
    if not rows:
        print(f"No runs logged in {path}")
        return
    s = summarize(rows)
    rate = f"{s['accepted']}/{s['runs']}" if s["runs"] else "0/0"
    print(f"Runs: {s['runs']}  accepted: {rate}  attempts: {s['attempts']} "
          f"(failed {s['failed_attempts']})  avg tokens/run: {s['avg_tokens_per_run']}")
    print(f"Attempts where scenes were frozen (scoped repair): {s['attempts_frozen']}")
    print("\nPer idea:")
    for idea, d in sorted(s["by_idea"].items()):
        print(f"  {idea:<12} runs={d['runs']} accepted={d['accepted']} attempts={d['attempts']}")
    print("\nFailed rules (most common first):")
    for name, n in s["failed_rules"]:
        print(f"  {n:>3}  {name}")
    print("\nBlocker/major issue categories:")
    for name, n in s["issue_categories"]:
        print(f"  {n:>3}  {name}")
    print("\nAverage Critic scores:")
    for k, v in s["avg_scores"].items():
        print(f"  {k:<22} {v}")


if __name__ == "__main__":
    main()
