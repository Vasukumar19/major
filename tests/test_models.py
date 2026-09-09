"""Checkpoint-1 tests: model abstraction. No network (fake transport)."""
import pytest

from patchforge.models.provider import (
    Generation,
    GeminiProvider,
    ModelProvider,
    OpenRouterError,
    OpenRouterProvider,
)
from patchforge.models.router import GPT_4O_MINI, LLAMA_8B, ModelRouter


def _resp(text="hello", cost=0.0001, n_choices=1, req_id="req-1"):
    return {
        "id": req_id,
        "model": "openai/gpt-4o-mini",
        "choices": [{"message": {"content": text}} for _ in range(n_choices)],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": cost},
    }


def test_generate_one_records_all_fields():
    seen = {}

    def fake(payload, key):
        seen["payload"] = payload
        assert key == "k"
        return _resp()

    g = OpenRouterProvider(model="m", api_key="k", transport=fake).generate_one(
        "hi", system="sys", max_tokens=5, temperature=0.0
    )
    assert isinstance(g, Generation)
    assert g.text == "hello"
    assert (g.input_tokens, g.output_tokens) == (10, 3)
    assert g.cost_usd == pytest.approx(0.0001) and not g.cost_estimated
    assert g.request_id == "req-1" and g.latency_s >= 0
    assert seen["payload"]["model"] == "m"
    assert "n" not in seen["payload"], "must not depend on batched n"


def test_generate_many_is_sequential():
    calls = []

    def fake(payload, key):
        calls.append(payload)
        return _resp(text=f"r{len(calls)}")

    gs = OpenRouterProvider(model="m", api_key="k", transport=fake).generate_many(
        "hi", n=3, temperature=0.8
    )
    assert [g.text for g in gs] == ["r1", "r2", "r3"]
    assert len(calls) == 3
    with pytest.raises(ValueError):
        OpenRouterProvider(model="m", api_key="k", transport=fake).generate_many("hi", n=0)


def test_missing_cost_flagged_estimated():
    def fake(payload, key):
        d = _resp()
        del d["usage"]["cost"]
        return d

    g = OpenRouterProvider(model="m", api_key="k", transport=fake).generate_one("hi")
    assert g.cost_usd == 0.0 and g.cost_estimated


def test_bad_shape_raises():
    def fake(payload, key):
        return {"no": "choices"}

    with pytest.raises(OpenRouterError):
        OpenRouterProvider(model="m", api_key="k", transport=fake).generate_one("hi")


def test_missing_key_raises_without_transport():
    with pytest.raises(OpenRouterError):
        OpenRouterProvider(model="m", api_key="", transport=None)


def test_gemini_provider_payload_and_response():
    seen = {}

    def fake(payload, key):
        seen["payload"] = payload
        assert key == "google-key"
        return {
            "candidates": [{"content": {"parts": [{"text": "hello"}]}}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
        }

    result = GeminiProvider(
        model="google/gemini-2.5-flash",
        api_key="google-key",
        transport=fake,
    ).generate_one("hi", system="sys", max_tokens=8, temperature=0.2)

    assert result.text == "hello"
    assert result.model == "gemini-2.5-flash"
    assert (result.input_tokens, result.output_tokens) == (7, 2)
    assert seen["payload"]["systemInstruction"]["parts"][0]["text"] == "sys"
    assert seen["payload"]["contents"][0]["parts"][0]["text"] == "hi"
    assert seen["payload"]["generationConfig"]["maxOutputTokens"] == 8


def test_router_mapping_and_caching(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r = ModelRouter(role_models={"repair": LLAMA_8B}, default=GPT_4O_MINI)
    assert r.model_for("repair") == LLAMA_8B
    assert r.model_for("unknown-role") == GPT_4O_MINI
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert r.provider_for("repair") is r.provider_for("repair")


def test_router_selects_gemini_provider(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    router = ModelRouter(default="google/gemini-2.5-flash")

    assert isinstance(router.provider_for("repair"), GeminiProvider)


def test_provider_is_abstract():
    with pytest.raises(TypeError):
        ModelProvider()
