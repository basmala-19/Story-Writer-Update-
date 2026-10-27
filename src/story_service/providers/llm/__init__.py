"""Configurable LLM clients and factory."""

from typing import Any


def create_llm(*args: Any, **kwargs: Any):
    """Lazily import real provider SDKs only when a real client is requested."""
    from .factory import create_llm as _create_llm

    return _create_llm(*args, **kwargs)


__all__ = ["create_llm"]
