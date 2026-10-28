import json

from story_service.providers.llm.usage_log import record_usage


def test_record_usage_appends_safe_metering_data(monkeypatch, tmp_path):
    log_path = tmp_path / "usage" / "tokens.jsonl"
    monkeypatch.setenv("LLM_USAGE_LOG_PATH", str(log_path))

    event = record_usage(provider="groq", model="test-model", model_role="large", tokens_in=12, tokens_out=34)

    assert event["tokens_in"] == 12
    assert json.loads(log_path.read_text(encoding="utf-8")) == event


def test_missing_usage_is_null_not_zero_and_cost_is_unknown(monkeypatch, tmp_path):
    log_path = tmp_path / "usage" / "missing.jsonl"
    monkeypatch.setenv("LLM_USAGE_LOG_PATH", str(log_path))
    event = record_usage(provider="test", model="unknown-model", model_role="large", tokens_in=None, tokens_out=None)
    assert event["tokens_in"] is None and event["tokens_out"] is None
    assert event["estimated_cost_usd"] is None
    assert event["cost_status"] == "unavailable_no_dated_price"
