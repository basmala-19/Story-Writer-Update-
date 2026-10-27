"""Google AI Studio (Gemini) implementation of the LLM interface."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from google import genai
from google.genai import types

from .json_utils import extract_json
from .usage_log import record_usage


def google_response_schema(schema: Any) -> dict[str, Any] | None:
    """Return a Gemini-compatible JSON Schema for a Pydantic output model.

    Gemini's typed `response_schema` converter accepts only string Literal
    values.  The writer model has `Literal[2]` for `max_attempts`; it remains
    enforced by Pydantic after generation, but cannot be sent to Gemini as a
    numeric `const`.
    """
    if not hasattr(schema, "model_json_schema"):
        return None
    result = deepcopy(schema.model_json_schema())

    def strip_numeric_constants(value: Any) -> None:
        if isinstance(value, dict):
            if "const" in value and not isinstance(value["const"], str):
                del value["const"]
            for child in value.values():
                strip_numeric_constants(child)
        elif isinstance(value, list):
            for child in value:
                strip_numeric_constants(child)

    strip_numeric_constants(result)
    return result


class GoogleStudioLLM:
    _MAX_JSON_ATTEMPTS = 3
    def __init__(self, *, api_key: str, models: dict[str, str]):
        self.client = genai.Client(api_key=api_key)
        self.models = models
        self.usage: list[dict[str, Any]] = []

    def _call(
        self,
        system: str,
        user: str,
        role: str,
        temperature: float,
        max_tokens: int,
        schema: Any | None = None,
    ) -> str:
        response = self.client.models.generate_content(
            model=self.models[role], contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                max_output_tokens=max_tokens,
                response_mime_type="application/json",
                # Pass JSON Schema directly rather than the SDK's typed schema
                # converter, which rejects the project's `Literal[2]` field.
                response_json_schema=google_response_schema(schema),
            ),
        )
        metadata = response.usage_metadata
        event = record_usage(
            provider="google",
            model=self.models[role],
            model_role=role,
            tokens_in=getattr(metadata, "prompt_token_count", 0) or 0,
            tokens_out=getattr(metadata, "candidates_token_count", 0) or 0,
        )
        self.usage.append(event)
        return response.text or ""

    def complete_json(self, system: str, user: str, schema: Any, model_role: str = "large", temperature: float = 0.2, max_tokens: int = 4096, images: list[bytes] | None = None) -> dict[str, Any]:
        if images:
            raise NotImplementedError("Image input is not configured for Google AI Studio yet")
        # A Script is intentionally validated by S4 after generation.  Gemini's
        # server-side schema enforcement can reject an otherwise recoverable
        # response (for example, one missing recap_line_ids) with a 400 before
        # the retry/feedback loop gets to see it.  Keep JSON mode, but let the
        # local Pydantic and story validators provide actionable feedback.
        request_schema = None if getattr(schema, "ROUTE", None) == "writer" else schema
        text = self._call(system, user, model_role, temperature, max_tokens, request_schema)
        for attempt in range(1, self._MAX_JSON_ATTEMPTS):
            try:
                return extract_json(text)
            except json.JSONDecodeError as exc:
                error_message = str(exc)
            # Each Gemini request is independent: unlike the OpenAI-compatible
            # client, it has no assistant-message history.  Include the bad
            # response so the model can actually repair the missing comma (or
            # other syntax error) rather than generating an unrelated answer.
            repair = (
                f"{user}\n\nThe response below was not valid JSON ({error_message}). "
                f"This is repair attempt {attempt} of {self._MAX_JSON_ATTEMPTS - 1}. "
                "Correct that exact response. Return ONLY the corrected JSON object.\n"
                f"<invalid_json>\n{text}\n</invalid_json>"
            )
            text = self._call(system, repair, model_role, 0.0, max_tokens, request_schema)
        return extract_json(text)
