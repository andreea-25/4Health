"""Client minimal pentru API-ul HTTP Ollama (https://github.com/ollama/ollama/blob/main/docs/api.md).

Folosim httpx direct (nu SDK-ul) ca să fie clar ce se trimite modelului:
- /api/chat  cu `format` = JSON Schema  -> ieșire structurată (structured outputs)
- /api/embed                             -> embeddings pentru RAG
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ..config import get_settings


class LLMError(RuntimeError):
    """Ollama indisponibil sau răspuns invalid."""


@dataclass
class LLMResult:
    data: Any
    model: str
    latency_ms: int
    raw: str = ""
    usage: dict = field(default_factory=dict)


class LLMClient(Protocol):
    model: str

    def chat_json(self, messages: list[dict], schema: dict, *, model: str | None = None,
                  temperature: float | None = None) -> LLMResult: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, embed_model: str | None = None):
        s = get_settings()
        self.base_url = (base_url or s.ollama_url).rstrip("/")
        self.model = model or s.llm_model
        self.embed_model = embed_model or s.embed_model
        self.temperature = s.llm_temperature
        self.num_ctx = s.llm_num_ctx
        self.timeout = s.llm_timeout_s

    # ------------------------------------------------------------------ chat
    def chat_json(self, messages, schema, *, model=None, temperature=None) -> LLMResult:
        model = model or self.model
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_ctx": self.num_ctx,
            },
            "keep_alive": "10m",
        }
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama nu răspunde ({e.__class__.__name__}). Rulează `ollama serve` "
                           f"și `ollama pull {model}`.") from e
        body = r.json()
        content = body.get("message", {}).get("content", "")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"Modelul nu a returnat JSON valid: {content[:200]}") from e
        return LLMResult(
            data=data,
            model=model,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            raw=content,
            usage={"prompt_tokens": body.get("prompt_eval_count"), "completion_tokens": body.get("eval_count")},
        )

    # ------------------------------------------------------------- embeddings
    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            r = httpx.post(
                f"{self.base_url}/api/embed",
                json={"model": self.embed_model, "input": texts},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Embedding indisponibil: rulează `ollama pull {self.embed_model}`.") from e
        return r.json()["embeddings"]

    # ----------------------------------------------------------------- health
    def health(self) -> dict:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            return {
                "ollama": "ok",
                "models": models,
                "llm_model": self.model,
                "llm_ready": any(m.split(":")[0] == self.model.split(":")[0] and
                                 (":" not in self.model or m == self.model) for m in models),
                "embed_model": self.embed_model,
                "embed_ready": any(m.split(":")[0] == self.embed_model.split(":")[0] for m in models),
            }
        except httpx.HTTPError:
            return {"ollama": "unreachable", "url": self.base_url}


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """Dependință FastAPI; în teste se înlocuiește cu un client fals."""
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
