"""Small provider-independent JSON response helpers."""
from __future__ import annotations

import json
from typing import Any


def _repair_missing_object_commas(text: str) -> str:
    """Repair a small, unambiguous model-output mistake.

    Some models occasionally omit the comma between two object properties:
    ``{"one": 1\n "two": 2}``.  At that decoder error position the next
    token is a quoted key, so inserting a comma after the preceding value is
    safe.  This intentionally does not attempt broader JSON repair.
    """
    for _ in range(3):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError as exc:
            if exc.msg != "Expecting ',' delimiter" or exc.pos >= len(text) or text[exc.pos] != '"':
                return text
            previous = exc.pos - 1
            while previous >= 0 and text[previous].isspace():
                previous -= 1
            if previous < 0 or text[previous] not in '"}]0123456789eE':
                return text
            text = text[: previous + 1] + "," + text[previous + 1 :]
    return text


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise json.JSONDecodeError("no JSON object found", text, 0)
    return json.loads(_repair_missing_object_commas(text[start : end + 1]))
