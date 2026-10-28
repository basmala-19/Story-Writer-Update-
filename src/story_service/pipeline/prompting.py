"""Prompt helpers shared by LLM stages (spec 5.0: untrusted input, prompt hashing)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).parent / "prompts"

OPEN_TAG, CLOSE_TAG = "<lesson_text>", "</lesson_text>"


_INCLUDE = re.compile(r"^\s*\{\{include:([A-Za-z0-9_.-]+)\}\}\s*$", re.MULTILINE)


def prompt_sources(*names: str) -> tuple[str, ...]:
    """Return root prompts and every transitive include, once, in stable order."""
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        path = (PROMPT_DIR / name).resolve()
        if not path.is_relative_to(PROMPT_DIR.resolve()):
            raise ValueError(f"prompt path escapes prompt directory: {name}")
        normalized = path.relative_to(PROMPT_DIR.resolve()).as_posix()
        if normalized in visiting:
            raise ValueError(f"cyclic prompt include: {normalized}")
        if normalized in ordered:
            return
        visiting.add(normalized)
        content = path.read_text(encoding="utf-8")
        ordered.append(normalized)
        for child in _INCLUDE.findall(content):
            visit(child)
        visiting.remove(normalized)

    for name in names:
        visit(name)
    return tuple(ordered)


def load_prompt(name: str) -> str:
    """Load a prompt, expanding explicit local includes in place."""
    root = PROMPT_DIR.resolve()

    def expand_file(current: str, stack: tuple[str, ...]) -> str:
        path = (PROMPT_DIR / current).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"prompt path escapes prompt directory: {current}")
        normalized = path.relative_to(root).as_posix()
        if normalized in stack:
            raise ValueError(f"cyclic prompt include: {normalized}")
        content = path.read_text(encoding="utf-8")

        def expand(match: re.Match[str]) -> str:
            return expand_file(match.group(1), (*stack, normalized))

        return _INCLUDE.sub(expand, content)

    return expand_file(name, ())


def prompt_hash(*names: str) -> str:
    h = hashlib.sha256()
    for source in prompt_sources(*names):
        h.update(source.encode("utf-8"))
        h.update(b"\0")
        h.update((PROMPT_DIR / source).read_bytes())
        h.update(b"\0")
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
