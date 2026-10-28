# story-service: Module 4 (S4 Story Writer + Critic)

```bash
uv sync --all-extras
uv run pytest -q
cp .env.example .env
# Set LLM_ACTIVE_PROVIDER and that provider's API key/model values in .env.
uv run python scripts/run_s4.py
```

## LLM configuration

The real LLM is selected entirely from `.env`. Set `LLM_ACTIVE_PROVIDER` to
`openrouter`, `google`, `groq`, or `hetzner`, then configure the matching API key and role
models. See `.env.example` for every available setting. Hetzner Inference is an
experimental service without production availability guarantees. `large`, `small`, and
`vision` can each use a different model; stages select the appropriate role.

Each successful LLM request is appended to `logs/llm_usage.jsonl` by default.
The record contains the UTC timestamp, provider, model, role, and input/output
token counts only. Change `LLM_USAGE_LOG_PATH` in `.env` if needed.

`S4_WRITER_MAX_TOKENS` and `S4_CRITIC_MAX_TOKENS` control the maximum output
tokens requested from the provider (defaults: 7000 and 3000). Lower them if a
provider reports insufficient credits.

## Reviewing a live S4 run in a notebook

Run the review notebook from the project directory:

```bash
uv sync --extra notebook
uv run --extra notebook jupyter lab
```

Open `notebooks/s4_pipeline_review.ipynb`. It displays the input fixture, every
writer/critic prompt and JSON response, the validation result for each attempt,
and the final script. It never displays environment variables or API keys.

- `src/story_service/pipeline/stages/s4_writer.py`: stage (writer, code validation, critic, 3-attempt loop)
- `src/story_service/validators/script.py`: code checks (all blockers) and Story Critic pass rule
- `src/story_service/pipeline/models.py`: `Script`, interactions (spec 7.7), `CriticReport`, tolerant upstream inputs
- `src/story_service/pipeline/prompts/s4_writer.md`, `s4_critic.md`: prompts
- `tests/unit/test_s4_script.py`: golden, 19 mutations, critic rules, retries, injection
- `docs/DECISIONS.md`: every ambiguity and how it was resolved
- `src/story_service/pipeline/prompts/s4_safety.md`: Safety Auditor (hazard, handler, safeguard); `validators.script.evaluate_safety` judges it


### Optional S4 repair debugging

Set `S4_RUN_LOG_REPAIR_DETAILS=true` to include the feedback prepared for the next retry,
the repair scope, and a compact snapshot of failed/hard validation checks in `logs/s4_runs.jsonl`.
This can contain story or lesson text, so it is disabled by default. It does not log the full prompt
or change repair prompts, validation thresholds, or retry behavior.
