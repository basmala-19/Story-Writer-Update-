from story_service.pipeline.models import Script
from story_service.providers.llm.google_client import GoogleStudioLLM, google_response_schema
from story_service.providers.llm.json_utils import extract_json


def test_google_response_schema_removes_only_non_string_constants():
    schema = google_response_schema(Script)
    assert schema is not None
    serialized = str(schema)
    assert "'const': 2" not in serialized
    assert "'const': 'tap_reveal'" in serialized


def test_google_writer_uses_json_mode_without_server_side_schema(monkeypatch):
    """S4 needs to receive recoverable bad output for its feedback loop."""
    client = object.__new__(GoogleStudioLLM)
    schemas = []

    def fake_call(system, user, role, temperature, max_tokens, schema):
        schemas.append(schema)
        return "{}"

    monkeypatch.setattr(client, "_call", fake_call)

    assert client.complete_json("system", "make JSON", Script) == {}
    assert schemas == [None]


def test_extract_json_repairs_omitted_object_property_comma():
    assert extract_json('{"title": "Story"\n  "count": 2}') == {"title": "Story", "count": 2}


def test_google_json_repair_includes_invalid_response(monkeypatch):
    """Gemini repair calls are stateless, so they must contain the bad JSON."""
    client = object.__new__(GoogleStudioLLM)
    invalid = '{"title": }'
    replies = iter([invalid, '{"title": "fixed", "count": 1}'])
    calls = []

    def fake_call(system, user, role, temperature, max_tokens, schema):
        calls.append((user, temperature))
        return next(replies)

    monkeypatch.setattr(client, "_call", fake_call)

    result = client.complete_json("system", "make JSON", dict, max_tokens=100)

    assert result == {"title": "fixed", "count": 1}
    assert calls[1][1] == 0.0
    assert f'<invalid_json>\n{invalid}\n</invalid_json>' in calls[1][0]


def test_google_json_repair_retries_using_the_latest_invalid_response(monkeypatch):
    client = object.__new__(GoogleStudioLLM)
    first = '{"title": }'
    second = '{"title": [}'
    replies = iter([first, second, '{"title": "fixed", "count": 1}'])
    calls = []

    def fake_call(system, user, role, temperature, max_tokens, schema):
        calls.append(user)
        return next(replies)

    monkeypatch.setattr(client, "_call", fake_call)

    assert client.complete_json("system", "make JSON", dict) == {"title": "fixed", "count": 1}
    assert len(calls) == 3
    assert first in calls[1]
    assert second in calls[2]


def test_openai_compatible_raises_on_truncated_output(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from story_service.providers.llm.openai_compatible import (
        OpenAICompatibleLLM,
        OutputTruncatedError,
    )

    monkeypatch.setattr(
        "story_service.providers.llm.openai_compatible.record_usage", lambda **kw: dict(kw)
    )
    client = object.__new__(OpenAICompatibleLLM)
    client.models = {"large": "openai/gpt-oss-120b"}
    client.provider = "groq"
    client.usage = []
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=7000),
            choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content='{"title": "x"'))],
        )

    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(OutputTruncatedError):
        client.complete_json("system", "user", dict, max_tokens=7000)
    assert seen["extra_body"] == {"reasoning_effort": "low"}