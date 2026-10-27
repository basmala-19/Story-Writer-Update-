"""LLM provider interface (spec 11.2). Real adapters are built in milestone M1."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

ModelRole = str  # "large" | "small" | "vision"


@runtime_checkable
class LLMClient(Protocol):
    def complete_json(
        self,
        system: str,
        user: str,
        schema: Any,
        model_role: ModelRole,
        temperature: float,
        max_tokens: int,
        images: list[bytes] | None = None,
    ) -> dict[str, Any]:
        """Return the parsed JSON object. Implementations own transient retries,
        the single invalid-JSON repair attempt, and usage recording."""
        ...
