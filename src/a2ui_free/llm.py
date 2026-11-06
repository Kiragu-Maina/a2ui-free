"""Talks to any language model with only the standard library.

A model is named by one string, so changing provider is a config change:

    ollama/llama3.2:3b              a model on this machine
    openai/gpt-5.2                  groq/..., nvidia/..., mistral/..., cerebras/...
    anthropic/claude-sonnet-5-5     gemini/gemini-3-flash
    openai-compatible:https://my.host/v1|my-model

Or pass any function that takes (system, user) and returns text. Several names
joined with commas form a chain: when one fails, the next one is tried, and a
provider that just failed is skipped for a while instead of being hammered.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol


class LlmError(Exception):
    """A failed call. `retryable` says whether another provider might help."""

    def __init__(self, message: str, *, retryable: bool = True, cool_off: float = 0):
        super().__init__(message)
        self.retryable = retryable
        self.cool_off = cool_off


class Llm(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


# Providers speaking the OpenAI chat format: base address, key variable.
OPENAI_COMPATIBLE: dict[str, tuple[str, str | None]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "lmstudio": ("http://localhost:1234/v1", None),
    "llamacpp": ("http://localhost:8080/v1", None),
    "vllm": ("http://localhost:8000/v1", None),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_NIM_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "sambanova": ("https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY"),
}


def _post(url: str, body: dict, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "user-agent": "a2ui-free", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(300).decode("utf-8", "replace")
        if error.code == 429:
            raise LlmError(f"rate limited: {detail}", cool_off=120) from None
        if error.code >= 500:
            raise LlmError(f"server error {error.code}", cool_off=60) from None
        # A bad key or a wrong model name. Another attempt here cannot help,
        # but the next provider in a chain still can.
        raise LlmError(f"refused {error.code}: {detail}", cool_off=3600) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LlmError(f"could not reach the model: {error}", cool_off=30) from None
    except ValueError:
        raise LlmError("the reply was not JSON", cool_off=30) from None


@dataclass
class OpenAiCompatible:
    name: str
    base_url: str
    model: str
    api_key: str | None = None
    timeout: float = 240
    temperature: float = 0.2
    max_tokens: int = 2000

    def complete(self, system: str, user: str) -> str:
        reply = _post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
            {"authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            self.timeout,
        )
        try:
            return reply["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LlmError("the reply had no text", cool_off=30) from None


@dataclass
class Anthropic:
    name: str
    model: str
    api_key: str
    timeout: float = 240
    max_tokens: int = 2000

    def complete(self, system: str, user: str) -> str:
        reply = _post(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self.model,
                "system": system,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": user}],
            },
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            self.timeout,
        )
        try:
            return "".join(p.get("text", "") for p in reply["content"])
        except (KeyError, TypeError, AttributeError):
            raise LlmError("the reply had no text", cool_off=30) from None


@dataclass
class Gemini:
    name: str
    model: str
    api_key: str
    timeout: float = 240

    def complete(self, system: str, user: str) -> str:
        reply = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
            },
            {"x-goog-api-key": self.api_key},
            self.timeout,
        )
        try:
            return "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LlmError("the reply had no text", cool_off=30) from None


@dataclass
class FunctionLlm:
    fn: Callable[[str, str], str]
    name: str = "function"

    def complete(self, system: str, user: str) -> str:
        return self.fn(system, user)


def _key(variable: str) -> str:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise LlmError(f"set {variable}", retryable=False)
    return value


def connect(spec: str) -> Llm:
    """Turns a model name into something that can be called."""
    spec = spec.strip()
    if spec.startswith("openai-compatible:"):
        base, _, model = spec.removeprefix("openai-compatible:").partition("|")
        if not base or not model:
            raise LlmError("write openai-compatible:https://host/v1|model", retryable=False)
        return OpenAiCompatible(spec, base, model, os.environ.get("A2UI_LLM_API_KEY") or None)
    provider, _, model = spec.partition("/")
    if not model:
        raise LlmError(f"{spec!r} needs a provider, like ollama/llama3.2:3b", retryable=False)
    if provider == "anthropic":
        return Anthropic(spec, model, _key("ANTHROPIC_API_KEY"))
    if provider == "gemini":
        return Gemini(spec, model, _key("GEMINI_API_KEY"))
    if provider not in OPENAI_COMPATIBLE:
        raise LlmError(f"unknown provider {provider}. Use openai-compatible:URL|model", retryable=False)
    base, variable = OPENAI_COMPATIBLE[provider]
    return OpenAiCompatible(spec, base, model, _key(variable) if variable else None)


class Chain:
    """Tries models in order, resting any that just failed."""

    def __init__(self, models: list[Llm], clock: Callable[[], float] = time.monotonic):
        if not models:
            raise LlmError("no models to try", retryable=False)
        self.models = models
        self.clock = clock
        self.resting: dict[str, float] = {}
        self.name = ",".join(m.name for m in models)
        self.used = ""

    def complete(self, system: str, user: str) -> str:
        failures: list[str] = []
        now = self.clock()
        awake = [m for m in self.models if self.resting.get(m.name, 0) <= now]
        for model in awake or self.models:
            try:
                reply = model.complete(system, user)
                self.used = model.name
                return reply
            except LlmError as error:
                failures.append(f"{model.name}: {error}")
                if error.cool_off:
                    self.resting[model.name] = self.clock() + error.cool_off
            except Exception as error:  # A custom function must not end the chain.
                failures.append(f"{model.name}: {type(error).__name__}")
        raise LlmError("every model failed: " + "; ".join(failures))


def chain(spec: str | Llm | Callable[[str, str], str] | None = None) -> Chain:
    """Builds a chain from a comma list, the A2UI_LLM variable, or a function."""
    if spec is None:
        spec = os.environ.get("A2UI_LLM", "")
        if not spec:
            raise LlmError("name a model, or set A2UI_LLM", retryable=False)
    if isinstance(spec, str):
        return Chain([connect(s) for s in spec.split(",") if s.strip()])
    if callable(spec) and not hasattr(spec, "complete"):
        return Chain([FunctionLlm(spec)])
    return Chain([spec])  # type: ignore[list-item]
