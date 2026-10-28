"""Adapter shared by OpenRouter and Groq's OpenAI-compatible APIs."""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .json_utils import extract_json
from .usage_log import record_usage


class OutputTruncatedError(RuntimeError):
    """The model hit `max_tokens` before finishing its answer."""


class OpenAICompatibleLLM:
    def __init__(self, *, api_key: str, base_url: str, models: dict[str, str], provider: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=5, timeout=120)
        self.models = models
        self.provider = provider
        self.usage: list[dict[str, Any]] = []

    def _call(self, system: str, messages: list[dict[str, Any]], role: str, temperature: float, max_tokens: int) -> str:
        kwargs: dict[str, Any] = {}
        # gpt-oss models on Groq are reasoning models: thinking tokens count against
        # max_tokens, so keep the reasoning short to leave room for the JSON itself.
        if self.provider == "groq" and "gpt-oss" in self.models[role]:
            kwargs["extra_body"] = {"reasoning_effort": "low"}
        response = self.client.chat.completions.create(
            model=self.models[role], messages=[{"role": "system", "content": system}, *messages],
            temperature=temperature, max_tokens=max_tokens, response_format={"type": "json_object"},
            **kwargs,
        )
        usage = response.usage
        event = record_usage(
            provider=self.provider,
            model=self.models[role],
            model_role=role,
            tokens_in=getattr(usage, "prompt_tokens", None) if usage else None,
            tokens_out=getattr(usage, "completion_tokens", None) if usage else None,
        )
        self.usage.append(event)
        choice = response.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            # A truncated JSON must not be "repaired": the model would just return a
            # shorter object (e.g. without the last key, `recap_line_ids`).
            raise OutputTruncatedError(
                f"{self.models[role]} output was cut off at max_tokens={max_tokens}; "
                "raise S4_WRITER_MAX_TOKENS / S4_CRITIC_MAX_TOKENS in .env"
            )
        return choice.message.content or ""

    def complete_json(self, system: str, user: str, schema: Any, model_role: str = "large", temperature: float = 0.2, max_tokens: int = 4096, images: list[bytes] | None = None) -> dict[str, Any]:
        if images:
            raise NotImplementedError(f"Image input is not configured for {self.provider} yet")
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        text = self._call(system, messages, model_role, temperature, max_tokens)
        try:
            return extract_json(text)
        except json.JSONDecodeError as exc:
            messages.extend([{"role": "assistant", "content": text}, {"role": "user", "content": f"That was not valid JSON ({exc}). Return ONLY the corrected JSON object."}])
            return extract_json(self._call(system, messages, model_role, 0.0, max_tokens))