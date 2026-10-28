"""Durable, provider-neutral token-usage logging."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_USAGE_LOG_PATH = "logs/llm_usage.jsonl"


def record_usage(*, provider: str, model: str, model_role: str, tokens_in: int | None, tokens_out: int | None) -> dict[str, Any]:
    """Append one successful LLM call to the configured JSON Lines log.

    The log deliberately contains only metering metadata--never prompts,
    responses, or API keys. JSON Lines makes it safe to append and easy to
    import into a spreadsheet, database, or observability tool later.
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "model_role": model_role,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        # Cost stays unknown unless a dated provider/model price is explicitly configured.
        "estimated_cost_usd": None,
        "cost_status": "unavailable_no_dated_price",
    }
    path = Path(os.getenv("LLM_USAGE_LOG_PATH", DEFAULT_USAGE_LOG_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event
