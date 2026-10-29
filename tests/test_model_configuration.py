import pytest

from material_scheduler.models import LiveModel
from material_scheduler.policy import DomainError


@pytest.mark.parametrize("value,expected", [("false", False), ("true", True), ("", None)])
def test_thinking_option_is_explicit(monkeypatch, value, expected):
    captured = {}

    class BoundModel:
        def bind_tools(self, tools):
            captured["tools"] = tools
            return self

    def fake_init(name, **options):
        captured.update(name=name, options=options)
        return BoundModel()

    monkeypatch.setenv("MODEL_NAME", "qwen3.8-max")
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-not-a-real-key")
    monkeypatch.setenv("MODEL_ENABLE_THINKING", value)
    monkeypatch.setattr("material_scheduler.models.init_chat_model", fake_init)
    LiveModel([])
    if expected is None:
        assert "extra_body" not in captured["options"]
    else:
        assert captured["options"]["extra_body"] == {"enable_thinking": expected}
    assert captured["options"]["max_retries"] == 0
    assert captured["tools"][0]["function"]["name"] == "finish"


def test_invalid_thinking_option_fails_before_network(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "qwen3.8-max")
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-not-a-real-key")
    monkeypatch.setenv("MODEL_ENABLE_THINKING", "invalid")
    with pytest.raises(DomainError, match="只接受 true 或 false"):
        LiveModel([])


@pytest.mark.parametrize(
    "provider,endpoint",
    [
        ("bailian", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("openai", "https://api.openai.com/v1"),
    ],
)
def test_provider_routes_without_base_url_variable(monkeypatch, provider, endpoint):
    captured = {}

    class FakeModel:
        def bind_tools(self, _tools):
            return self

    def fake_init(_name, **options):
        captured.update(options)
        return FakeModel()

    monkeypatch.setenv("MODEL_PROVIDER", provider)
    monkeypatch.setenv("MODEL_NAME", "qwen3.8-max")
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-not-a-real-key")
    monkeypatch.setenv("MODEL_BASE_URL", "https://invalid.example.test")
    monkeypatch.setattr("material_scheduler.models.init_chat_model", fake_init)
    LiveModel([])
    assert captured["base_url"] == endpoint and captured["model_provider"] == "openai"
