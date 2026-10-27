"""FakeLLM: canned, schema-valid JSON per stage (spec 13.1).

Routing is by the `ROUTE` class attribute of the schema passed to `complete_json`
(`Script.ROUTE == "writer"`, `CriticReport.ROUTE == "critic"`). Each route value can be
a fixture file name (relative to tests/fixtures/llm), a dict, a callable(user_prompt) -> dict,
or a list of those, consumed one per call with the last one repeating.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "llm"

Item = str | dict | Callable[[str], dict]


class FakeLLM:
    def __init__(self, writer: Item | list[Item] | None = None, **routes: Item | list[Item]):
        if writer is not None:
            routes["writer"] = writer
        self._routes = {k: (v if isinstance(v, list) else [v]) for k, v in routes.items()}
        self._cursor: dict[str, int] = {}
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self,
        system: str,
        user: str,
        schema: Any,
        model_role: str = "large",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        images: list[bytes] | None = None,
    ) -> dict[str, Any]:
        route = getattr(schema, "ROUTE", "main")
        if route not in self._routes:
            raise KeyError(f"FakeLLM has no fixture for route '{route}'")
        self.calls.append(
            {"route": route, "system": system, "user": user, "model_role": model_role,
             "temperature": temperature, "max_tokens": max_tokens}
        )
        items = self._routes[route]
        i = min(self._cursor.get(route, 0), len(items) - 1)
        self._cursor[route] = self._cursor.get(route, 0) + 1
        return self._load(items[i], user)

    def calls_for(self, route: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["route"] == route]

    @staticmethod
    def _load(item: Item, user: str) -> dict[str, Any]:
        if callable(item):
            return item(user)
        if isinstance(item, dict):
            return copy.deepcopy(item)
        return json.loads((FIXTURE_DIR / item).read_text(encoding="utf-8"))
