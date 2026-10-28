"""Measure how stable the Story Critic is on scripts that already exist (test-retest).

    uv run python scripts/measure_critic_stability.py script_i3.json script_out.json \
        --inputs tests/fixtures/s4/inputs_i3.json --runs 3

Each script is judged --runs times by the same Critic (same model, prompt, settings). For every
script it prints the score of each dimension per run, the spread, and whether the pass/fail decision
flipped, under the strict pass rule. It does not change any
script. Cost: one Critic call per script per run.

Read the result like this:
  flip rate            how often the decision differs from the majority result
  unstable dimensions  dimensions whose score changed between runs (the ones to fix in the rubric)
"""
import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from story_service.pipeline.models import Script
from story_service.pipeline.stages import s4_writer
from story_service.providers.llm import create_llm
from story_service.validators.script import evaluate_critic


def build_ctx(inputs: dict) -> "s4_writer._Ctx":
    return s4_writer._Ctx(
        intake=s4_writer.Intake.model_validate(inputs["intake"]),
        concepts=s4_writer.Concepts.model_validate(inputs["concepts"]),
        brief=s4_writer.Brief.model_validate(inputs["brief"]),
        idea=s4_writer.Idea.model_validate(inputs["idea"]),
        age_spec=s4_writer.AgeSpec.model_validate(inputs["age_spec"]),
        speech_style=s4_writer.SpeechStyle.model_validate(inputs["speech_style"]),
        wps={**s4_writer.DEFAULT_WPS, **(inputs.get("wps") or {})},
    )


def judge(llm, ctx, raw: dict, script: Script):
    """One Critic pass under the same strict rule used by S4."""
    report = s4_writer._critic(llm, ctx, raw)
    return evaluate_critic(report, concepts=ctx.concepts, script=script), report


def flip_rate(decisions: list[bool]) -> float:
    """Share of runs that disagree with the majority decision (0 = perfectly stable)."""
    if not decisions:
        return 0.0
    majority = sum(decisions) * 2 >= len(decisions)
    return sum(1 for d in decisions if d != majority) / len(decisions)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scripts", nargs="+", help="script JSON files written by run_s4.py")
    ap.add_argument("--inputs", default=str(ROOT / "tests/fixtures/s4/golden_inputs.json"))
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
    ctx = build_ctx(inputs)
    llm = create_llm()

    all_strict: list[float] = []
    unstable_counter: dict[str, int] = defaultdict(int)
    for path in args.scripts:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        script = Script.model_validate(raw)
        per_dim: dict[str, list[float]] = defaultdict(list)
        strict_pass: list[bool] = []
        for _ in range(args.runs):
            strict, report = judge(llm, ctx, raw, script)
            strict_pass.append(bool(strict["passed"]))
            for k, v in report.scores.items():
                per_dim[k].append(v)
        print(f"\n== {path}  ({args.runs} runs)")
        for k, vals in per_dim.items():
            spread = max(vals) - min(vals)
            mark = "  <- unstable" if spread > 0 else ""
            if spread > 0:
                unstable_counter[k] += 1
            sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            print(f"  {k:22s} {vals}  sd={sd:.2f}{mark}")
        fs = flip_rate(strict_pass)
        all_strict.append(fs)
        print(f"  accepted: {sum(strict_pass)}/{args.runs}")
        print(f"  flip rate: {fs:.0%}")

    t_in = sum(u["tokens_in"] for u in llm.usage)
    t_out = sum(u["tokens_out"] for u in llm.usage)
    print("\n== summary")
    print(f"  mean flip rate: {statistics.mean(all_strict):.0%}")
    if unstable_counter:
        worst = sorted(unstable_counter.items(), key=lambda kv: -kv[1])
        print("  unstable dimensions (scripts where the score changed): "
              + ", ".join(f"{k}={n}" for k, n in worst))
    print(f"  tokens: in={t_in} out={t_out}")
    print("  note: small samples (a few scripts x 3 runs) only show whether the problem is real; they "
          "do not prove reliability.")


if __name__ == "__main__":
    main()
