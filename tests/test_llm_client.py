import pytest

from citefact.llm.client import DEFAULT_MODEL, call_llm, resolve_model, supports_prompt_caching


class _FakeResponse:
    def __init__(self, content="ok", finish_reason="stop"):
        self.choices = [type("C", (), {
            "message": type("M", (), {"content": content})(),
            "finish_reason": finish_reason,
        })()]
        self.model = "anthropic/claude-sonnet-5"


def test_call_llm_returns_result(monkeypatch):
    monkeypatch.setattr("citefact.llm.client.litellm.completion", lambda **k: _FakeResponse("hello"))
    monkeypatch.setattr("citefact.llm.client.litellm.completion_cost", lambda r: 0.01)
    r = call_llm([{"role": "user", "content": "hi"}], model=DEFAULT_MODEL)
    assert r.text == "hello" and r.cost_usd == 0.01 and r.finish_reason == "stop"


def test_cost_failure_degrades_to_zero(monkeypatch):
    monkeypatch.setattr("citefact.llm.client.litellm.completion", lambda **k: _FakeResponse())
    monkeypatch.setattr("citefact.llm.client.litellm.completion_cost",
                        lambda r: (_ for _ in ()).throw(RuntimeError("no pricing")))
    assert call_llm([{"role": "user", "content": "x"}], model="ollama/x").cost_usd == 0.0


def test_resolve_model_precedence(monkeypatch):
    monkeypatch.delenv("CITEFACT_MODEL", raising=False)
    assert resolve_model(None, None) == DEFAULT_MODEL
    monkeypatch.setenv("CITEFACT_MODEL", "openai/gpt-x")
    assert resolve_model(None, None) == "openai/gpt-x"
    assert resolve_model("ollama/llama3", None) == "ollama/llama3"


def test_resolve_model_provider_beats_env(monkeypatch):
    monkeypatch.setenv("CITEFACT_MODEL", "openai/gpt-x")
    assert resolve_model(None, "anthropic") == DEFAULT_MODEL


def test_resolve_model_unknown_provider_raises(monkeypatch):
    monkeypatch.delenv("CITEFACT_MODEL", raising=False)
    with pytest.raises(ValueError):
        resolve_model(None, "unknownprov")


def test_supports_prompt_caching():
    assert supports_prompt_caching("anthropic/claude-sonnet-5") is True
    assert supports_prompt_caching("openai/gpt-x") is False


def test_unsupported_params_are_dropped_globally():
    """claude-sonnet-5 (and the Opus 4.7+ family) reject non-default
    temperature/top_p/top_k outright. litellm.drop_params makes litellm
    strip parameters unsupported by the target model instead of raising
    UnsupportedParamsError before the request is even sent; providers and
    models that do support temperature keep receiving it."""
    import litellm

    import citefact.llm.client  # noqa: F401  (importing configures litellm)

    assert litellm.drop_params is True


def test_litellm_uses_local_cost_map_no_network_at_import():
    """litellm fetches a remote model-price map from GitHub at import time
    unless LITELLM_LOCAL_MODEL_COST_MAP is set; that stalls startup on slow
    networks and violates the no-network-beyond-the-provider rule. The
    client module must pin the env var before importing litellm."""
    import os

    import citefact.llm.client  # noqa: F401

    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"
