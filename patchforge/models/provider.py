"""Single entry point for all LLM calls. OpenRouter only (Phase 1).

Proven Phase-B constraints baked in:
- OpenRouter ignores/normalizes n>1 (returns 1 choice), so candidate
  generation is sequential: generate_many() loops generate_one().
- No new dependencies: stdlib urllib only.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class Generation:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    request_id: str = ""
    cost_estimated: bool = False


class ModelProvider(ABC):
    @abstractmethod
    def generate_one(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> Generation:
        ...

    def generate_many(
        self,
        prompt: str,
        n: int,
        system: str = "You are a helpful assistant.",
        max_tokens: int = 1024,
        temperature: float = 0.8,
    ) -> list[Generation]:
        """Bounded sequential sampling. Never relies on batched n>1."""
        if n < 1:
            raise ValueError("n must be >= 1")
        return [
            self.generate_one(prompt, system, max_tokens, temperature)
            for _ in range(n)
        ]


class OpenRouterError(RuntimeError):
    pass


def _default_transport(payload: dict, api_key: str) -> dict:
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:500]
        except Exception:
            detail = ""
        raise OpenRouterError(f"HTTP {e.code}: {detail}") from e


class OpenRouterProvider(ModelProvider):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        transport=None,
    ):
        self.model = model
        key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        if not key and transport is None:
            raise OpenRouterError("Set OPENROUTER_API_KEY in the environment.")
        self._api_key = key
        self._transport = transport or _default_transport

    def generate_one(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> Generation:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        start = time.time()
        data = self._transport(payload, self._api_key)
        latency = time.time() - start
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise OpenRouterError(f"Unexpected response shape: {str(data)[:300]}") from e
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        return Generation(
            text=text,
            model=data.get("model", self.model),
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cost_usd=float(cost) if cost is not None else 0.0,
            latency_s=latency,
            request_id=str(data.get("id", "")),
            cost_estimated=cost is None,
        )


class GeminiProvider(ModelProvider):
    """Native Google AI Studio provider using the Gemini REST API."""

    def __init__(self, model: str, api_key: str | None = None, transport=None):
        self.model = model.removeprefix("google/")
        key = api_key or os.getenv("GOOGLE_API_KEY", "")
        if not key and transport is None:
            raise OpenRouterError("Set GOOGLE_API_KEY in the environment.")
        self._api_key = key
        self._transport = transport or self._default_transport

    def _default_transport(self, payload: dict, api_key: str) -> dict:
        url = GEMINI_URL.format(model=self.model) + "?key=" + urllib.parse.quote(api_key)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode()[:500]
            except Exception:
                detail = ""
            raise OpenRouterError(f"Gemini HTTP {e.code}: {detail}") from e

    def generate_one(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> Generation:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        start = time.time()
        data = self._transport(payload, self._api_key)
        candidate = (data.get("candidates") or [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts)
        if not text:
            raise OpenRouterError(f"Unexpected Gemini response shape: {str(data)[:500]}")
        usage = data.get("usageMetadata", {})
        return Generation(
            text=text,
            model=self.model,
            input_tokens=int(usage.get("promptTokenCount", 0) or 0),
            output_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
            latency_s=time.time() - start,
        )
