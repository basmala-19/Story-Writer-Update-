import json

from story_service.providers.llm.usage_log import record_usage


def test_record_usage_appends_safe_metering_data(monkeypatch, tmp_path):
    log_path = tmp_path / "usage" / "tokens.jsonl"
    monkeypatch.setenv("LLM_USAGE_LOG_PATH", str(log_path))

    event = record_usage(provider="groq", model="test-model", model_role="large", tokens_in=12, tokens_out=34)

    assert event["tokens_in"] == 12
    assert json.loads(log_path.read_text(encoding="utf-8")) == event
