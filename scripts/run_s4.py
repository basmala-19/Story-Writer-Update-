"""Run stage S4 against a real LLM and print what happens.

    uv run python scripts/run_s4.py                 # sample request (fractions, Egypt, 9-11)
    uv run python scripts/run_s4.py --inputs my.json --out my_script.json
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from story_service.pipeline import run_log
from story_service.pipeline.preflight import preflight_s4
from story_service.pipeline.stages import s4_writer
from story_service.providers.llm import create_llm
from story_service.validators.script import failed_checks


def show_attempt(rec):
    print(f"\n=== attempt {rec.attempt}: {'PASSED' if rec.passed else 'FAILED'} ===")
    print(f"estimated duration: {rec.code_validation.get('estimated_duration_sec')} s")
    for c in failed_checks(rec.code_validation):
        print(f"  code  [{c['severity']}] {c['name']}: {c['detail']}")
    if rec.frozen_scenes:
        print(f"  repair: scenes restored unchanged from the previous attempt: {rec.frozen_scenes}")
    if rec.audit_validation:
        for c in failed_checks(rec.audit_validation):
            print(f"  audit [{c['severity']}] {c['name']}: {c['detail']}")
    if rec.safety_validation:
        for c in failed_checks(rec.safety_validation):
            print(f"  safety [{c['severity']}] {c['name']}: {c['detail']}")
    if rec.critic_validation and rec.critic_validation.get("scores"):
        print(f"  critic scores: {rec.critic_validation.get('scores')}")
    if rec.critic_validation:
        for c in failed_checks(rec.critic_validation):
            if c["name"] not in ("continuity_audit", "safety_audit", "safety_audit_unavailable"):
                print(f"  critic [{c['severity']}] {c['name']}: {c['detail']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", default=str(ROOT / "tests/fixtures/s4/golden_inputs.json"))
    ap.add_argument("--out", default="output/script_out.json")
    ap.add_argument("--preflight-only", action="store_true",
                    help="check and normalize S4 inputs without contacting a model")
    args = ap.parse_args()
    inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    preflight = preflight_s4(inputs)
    for warning in preflight.warnings:
        print(f"PREFLIGHT NOTE: {warning}")
    for repair in preflight.repairs:
        print(f"PREFLIGHT AUTO-FIX: {repair}")
    if not preflight.passed:
        print("\nPREFLIGHT FAILED before any model request:")
        for error in preflight.errors:
            print(f"  - {error}")
        sys.exit(5)
    inputs = preflight.normalized_inputs or inputs
    if args.preflight_only:
        print("PREFLIGHT PASSED: inputs are consistent and feasible for S4.")
        sys.exit(0)
    llm = create_llm()
    records = []
    run_id = uuid.uuid4().hex[:8]
    idea = (inputs.get("idea") or {}).get("id") or Path(args.inputs).stem
    seen = {"n": 0}

    def used(since):
        new = llm.usage[since:]
        return sum(u["tokens_in"] for u in new), sum(u["tokens_out"] for u in new)

    def on_attempt(rec):
        records.append(rec)
        show_attempt(rec)
        t_in, t_out = used(seen["n"])
        seen["n"] = len(llm.usage)
        run_log.log_attempt(run_id=run_id, idea=idea, rec=rec, tokens_in=t_in, tokens_out=t_out)

    def finish(status, attempts):
        t_in, t_out = used(0)
        run_log.log_run_end(run_id=run_id, idea=idea, status=status, attempts=attempts,
                            tokens_in=t_in, tokens_out=t_out)

    try:
        script, outcome = s4_writer.run_with_outcome(**inputs, llm=llm, on_attempt=on_attempt)
    except s4_writer.ProviderFatalError as exc:
        print(f"\nSTOPPED ({exc.code}): {exc}")
        if exc.status == 0:
            print("No story retries were spent. Check internet/DNS access and the provider endpoint in .env.")
        elif exc.status == 429:
            print("No story retries were spent. Wait for the provider quota reset or switch provider/model in .env.")
        else:
            print("Retrying cannot fix this. Check the API key/credits in .env, or lower S4_WRITER_MAX_TOKENS.")
        finish("provider_error", len(records))
        sys.exit(3)
    except s4_writer.TokenBudgetExceeded as exc:
        print(f"\nSTOPPED (TOKEN_BUDGET_EXHAUSTED): {exc}")
        finish("token_budget_exhausted", len(records))
        sys.exit(4)
    except s4_writer.ValidationExhausted as exc:
        print(f"\nNEEDS REVIEW ({exc.code}) after {exc.attempts} attempts:")
        for i in exc.issues:
            print(f"  - [{i['severity']}] {i['message']}")
        if exc.last_script:
            Path(args.out).write_text(exc.last_script.model_dump_json(indent=2), encoding="utf-8")
            print(f"last attempt saved to {args.out}")
        finish("exhausted", exc.attempts)
        sys.exit(1)
    # `outcome.attempt` is the selected best attempt in advisory mode, not the number of
    # attempts actually executed. The run log's `attempts` field must reflect actual work.
    finish(outcome.status, len(records))
    if outcome.status != "accepted":
        print(
            f"\nNEEDS REVIEW (not a pass): the Critic never passed; best attempt "
            f"{outcome.attempt} kept. Still failing: {', '.join(outcome.failed_rules)}"
        )
    Path(args.out).write_text(script.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nTitle: {script.title}\n{script.logline}\n")
    for s in script.scenes:
        print(f"[{s.id}] {s.summary}")
        for l in s.lines:
            print(f"   {l.speaker:>8}: {l.text_display}")
        if s.interaction:
            print(f"   >> interaction {s.interaction.type}: {s.interaction.prompt}")
    print(f"\nTokens: in={sum(u['tokens_in'] for u in llm.usage)} out={sum(u['tokens_out'] for u in llm.usage)}")
    print(f"Saved to {args.out}")
    if outcome.status != "accepted":
        sys.exit(2)


if __name__ == "__main__":
    main()
