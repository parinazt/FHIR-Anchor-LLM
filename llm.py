"""Shared Chat Completions transport for OpenAI and OpenRouter."""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class LLMResponse:
    text: str
    response_id: Optional[str] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    usage: Optional[dict] = None
    elapsed_seconds: float = 0.0
    refusal: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class LLMClient:
    """One stateless API call per generate(); no automatic retries or repair.

    Temperature and JSON mode are opt-in because model support varies.
    Credentials are selected explicitly by provider, never shared across them.
    """

    ENDPOINTS = {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    KEY_ENV = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}

    def __init__(self, model: str, provider: str = "openai", *,
                 api_key: Optional[str] = None, base_url: Optional[str] = None,
                 temperature: Optional[float] = None, json_mode: bool = False,
                 timeout: float = 120.0, client: Any = None):
        if provider not in self.ENDPOINTS:
            raise ValueError("provider must be openai or openrouter")
        if not model.strip():
            raise ValueError("A model identifier is required")
        self.model = model
        self.provider = provider
        self.base_url = base_url or self.ENDPOINTS[provider]
        self.temperature = temperature
        self.json_mode = json_mode
        self.timeout = timeout
        if client is None:
            key = api_key or os.environ.get(self.KEY_ENV[provider])
            if not key:
                raise ValueError(f"Set {self.KEY_ENV[provider]} before running")
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=self.base_url,
                            max_retries=0, timeout=timeout)
        self._client = client

    def configuration(self) -> dict:
        return {"provider": self.provider, "model": self.model,
                "base_url": self.base_url, "temperature": self.temperature,
                "json_mode": self.json_mode, "timeout": self.timeout,
                "max_retries": 0}

    def generate(self, system_prompt: str, user_input: str, *, history: Optional[list] = None) -> LLMResponse:
        parameters = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt},
                         *(history or []),
                         {"role": "user", "content": user_input}],
        }
        if self.temperature is not None:
            parameters["temperature"] = self.temperature
        if self.json_mode:
            parameters["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        response = self._client.chat.completions.create(**parameters)
        elapsed = time.perf_counter() - started
        if not response.choices:
            raise RuntimeError("LLM response contains no choices")
        choice = response.choices[0]
        return LLMResponse(
            text=choice.message.content or "", response_id=response.id,
            model=response.model, finish_reason=choice.finish_reason,
            usage=response.usage.model_dump() if response.usage else None,
            elapsed_seconds=elapsed,
            refusal=getattr(choice.message, "refusal", None),
        )
