"""Prompt helpers shared by LLM stages (spec 5.0: untrusted input, prompt hashing)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).parent / "prompts"

OPEN_TAG, CLOSE_TAG = "<lesson_text>", "</lesson_text>"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def prompt_hash(*names: str) -> str:
    h = hashlib.sha256()
    for n in names:
        h.update((PROMPT_DIR / n).read_bytes())
    return h.hexdigest()


def wrap_lesson_text(text: str) -> str:
    """Wrap untrusted lesson text in delimiters. Delimiter look-alikes inside the text are
    neutralised so the text cannot close the block early."""
    safe = text.replace(CLOSE_TAG, "[/lesson_text]").replace(OPEN_TAG, "[lesson_text]")
    return f"{OPEN_TAG}\n{safe}\n{CLOSE_TAG}"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_hash(inputs: Any, prompt_names: tuple[str, ...], model_id: str) -> str:
    """Spec 5.0: SHA-256 of canonical JSON of stage inputs + prompt file hash + model id."""
    payload = canonical_json({"inputs": inputs, "prompts": prompt_hash(*prompt_names), "model": model_id})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
