from story_service.providers.llm.factory import LLMSettings, create_llm
from story_service.providers.llm.openai_compatible import OpenAICompatibleLLM


def test_hetzner_settings_use_official_model_defaults(monkeypatch):
    monkeypatch.setenv("LLM_ACTIVE_PROVIDER", "hetzner")
    monkeypatch.setenv("HETZNER_API_KEY", "test-token")
    monkeypatch.delenv("HETZNER_MODEL_LARGE", raising=False)
    monkeypatch.delenv("HETZNER_MODEL_SMALL", raising=False)
    monkeypatch.delenv("HETZNER_MODEL_VISION", raising=False)

    settings = LLMSettings.from_env()

    assert settings.provider == "hetzner"
    assert settings.api_key == "test-token"
    assert settings.models == {
        "large": "Qwen/Qwen3.6-35B-A3B-FP8",
        "small": "Qwen3.8-27B",
        "vision": "Qwen/Qwen3.6-35B-A3B-FP8",
    }


def test_hetzner_client_uses_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_ACTIVE_PROVIDER", "hetzner")
    monkeypatch.setenv("HETZNER_API_KEY", "test-token")

    client = create_llm()

    assert isinstance(client, OpenAICompatibleLLM)
    assert client.provider == "hetzner"
    assert str(client.client.base_url).rstrip("/") == "https://inference.hetzner.com/api/v1"

