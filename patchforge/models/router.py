"""Maps logical roles to models. Phase 1: fixed mapping, no learning.

Phase-1 models (spec §14):
    openai/gpt-4o-mini
    meta-llama/llama-3.1-8b-instruct
"""
from __future__ import annotations

import os

from patchforge.models.provider import (
    Generation,
    GeminiProvider,
    ModelProvider,
    OllamaProvider,
    OpenRouterProvider,
)

GPT_4O_MINI = "openai/gpt-4o-mini"
LLAMA_8B = "meta-llama/llama-3.1-8b-instruct"


class ModelRouter:
    def __init__(self, role_models: dict[str, str] | None = None, default: str = GPT_4O_MINI):
        self.role_models = dict(role_models or {})
        self.default = default
        self._providers: dict[str, ModelProvider] = {}

    def model_for(self, role: str) -> str:
        return self.role_models.get(role, self.default)

    def provider_for(self, role: str) -> ModelProvider:
        model = self.model_for(role)
        if model not in self._providers:
            if (
                model.startswith("ollama/")
                or ":" in model
                or model.startswith(("qwen", "llama", "deepseek", "mistral", "gemma", "codellama"))
                or model in ("local", "ollama")
                or "/" not in model
                or os.getenv("PATCHFORGE_LOCAL_LLM") == "1"
            ):
                self._providers[model] = OllamaProvider(model=model)
            elif model.startswith("google/"):
                self._providers[model] = GeminiProvider(model=model)
            else:
                self._providers[model] = OpenRouterProvider(model=model)
        return self._providers[model]


    def generate_one(self, role: str, prompt: str, **kwargs) -> Generation:
        return self.provider_for(role).generate_one(prompt, **kwargs)

    def generate_many(self, role: str, prompt: str, n: int, **kwargs) -> list[Generation]:
        return self.provider_for(role).generate_many(prompt, n, **kwargs)
