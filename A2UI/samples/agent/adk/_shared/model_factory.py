# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Provider-agnostic model factory for A2UI sample agents.

Reads a single env var `LLM_MODEL` of the form `provider/model-id` and returns
a model object usable as `LlmAgent(model=...)`.

Supported providers
-------------------
- `gemini/<model-id>`     -> google.adk.models.Gemini (native)
- `cloudflare/<model-id>` -> LiteLlm against the CF Workers AI OpenAI-compat endpoint
                             Requires CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN.
- `nvidia/<model-id>`     -> LiteLlm against the NVIDIA NIM OpenAI-compat endpoint
                             (integrate.api.nvidia.com/v1). Requires NVIDIA_NIM_API_KEY.
- `openai/<model-id>`     -> LiteLlm (OPENAI_API_KEY)
- `anthropic/<model-id>`  -> LiteLlm (ANTHROPIC_API_KEY)
- `ollama_chat/<model-id>`-> LiteLlm (local Ollama)
- `council/<strategy>`    -> CouncilLlm fanning out to N children defined by
                             COUNCIL_MODELS (comma-separated provider/model-id
                             specs). Optional COUNCIL_JUDGE picks the synthesizer.
                             Strategies: single-query, ranked-fallback,
                             majority-vote, synthesize-best.
- `hybrid/<strategy>`     -> HybridToolCouncilLlm. Tool-decision turns go to
                             TOOL_MODEL (single, tool-capable model);
                             post-tool turns go to an auto-pool council with
                             the given strategy (default "resilient").
                             For agents like restaurant_finder where one model
                             decides tool calls and another set produces the
                             final UI JSON. Required: TOOL_MODEL env var.
- `auto/<strategy>`       -> Auto-council picked from the live pool in
                             discovered_models.json (written by
                             discover_free_models.py weekly cron). Diversified
                             across providers (round-robin), fastest first.
                             Knobs: COUNCIL_SIZE (default 3, or 15 for
                             "resilient"), COUNCIL_PROVIDERS (allow-list),
                             COUNCIL_JUDGE.
                             Special strategy `resilient`: ranked-fallback over
                             the full pool with per-process quarantine of
                             rate-limited / 5xx members. Goal: "almost always
                             get a result" by exhausting providers before
                             failing.
- `<anything-else>/<...>` -> LiteLlm; LiteLLM resolves the provider from the prefix.

Overrides (apply to any LiteLLM-routed provider):
- `LLM_API_KEY`  -> overrides the provider-default key env var
- `LLM_API_BASE` -> overrides the endpoint URL

Legacy fallbacks (preserved so existing setups keep working):
- `LITELLM_MODEL`         used if `LLM_MODEL` is unset (treated as LiteLLM model id)
- `MODEL_NAME` / `MODEL`  used if neither of the above is set; assumed to be Gemini.
"""

from __future__ import annotations

import os
from typing import Optional


def _cloudflare_api_base() -> str:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError(
            "LLM_MODEL is set to a cloudflare/* model but CLOUDFLARE_ACCOUNT_ID "
            "is not set. Required for the Workers AI OpenAI-compat endpoint."
        )
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"


NVIDIA_NIM_API_BASE = "https://integrate.api.nvidia.com/v1"


def _resolve_spec() -> tuple[Optional[str], str]:
    """Return (provider, model_id). provider is None if no slash prefix was given."""
    spec = (
        os.environ.get("LLM_MODEL")
        or os.environ.get("LITELLM_MODEL")
        or os.environ.get("MODEL_NAME")
        or os.environ.get("MODEL")
        or "gemini/gemini-2.5-flash"
    )
    if "/" in spec:
        provider, _, model = spec.partition("/")
        return provider.lower(), model
    return None, spec


def build_model(default: Optional[str] = None):
    """Construct a model object for `LlmAgent(model=...)` from env config.

    Args:
      default: Optional `provider/model-id` to use when no env var is set.
               Overrides the built-in `gemini/gemini-2.5-flash` default.
    """
    if default and not any(
        os.environ.get(k) for k in ("LLM_MODEL", "LITELLM_MODEL", "MODEL_NAME", "MODEL")
    ):
        os.environ["LLM_MODEL"] = default

    provider, model_id = _resolve_spec()

    if provider == "hybrid":
        return _build_hybrid(strategy=model_id or "resilient")

    if provider == "auto":
        # auto/<strategy> -> build a council from the live pool in
        # discovered_models.json. Strategy default is synthesize-best.
        strategy = model_id or "synthesize-best"
        return _build_auto_council(strategy=strategy)

    if provider == "council":
        return _build_council(strategy=model_id)

    # Native Gemini path: keep using google.adk.models.Gemini so the existing
    # google-genai auth flow (GOOGLE_API_KEY / ADC) keeps working unchanged.
    if provider is None or provider == "gemini":
        from google.adk.models import Gemini

        return Gemini(model=model_id)

    # Everything else goes through LiteLLM.
    from google.adk.models.lite_llm import LiteLlm

    kwargs: dict = {}
    api_key = os.environ.get("LLM_API_KEY")
    api_base = os.environ.get("LLM_API_BASE")

    if provider == "cloudflare":
        # LiteLLM doesn't have first-class CF Workers AI routing for the
        # OpenAI-compat endpoint, so we use the openai/ provider with a
        # custom base URL pointing at CF.
        litellm_model = f"openai/{model_id}"
        api_key = api_key or os.environ.get("CLOUDFLARE_API_TOKEN")
        api_base = api_base or _cloudflare_api_base()
        if not api_key:
            raise RuntimeError(
                "cloudflare/* model requested but CLOUDFLARE_API_TOKEN "
                "(or LLM_API_KEY) is not set."
            )
    elif provider == "nvidia":
        # NVIDIA NIM is OpenAI-compatible; route through LiteLLM's openai/
        # provider with the NIM base URL. Model ids carry their own org
        # prefix (e.g. meta/llama-3.3-70b-instruct), preserved verbatim.
        litellm_model = f"openai/{model_id}"
        api_key = api_key or os.environ.get("NVIDIA_NIM_API_KEY")
        api_base = api_base or NVIDIA_NIM_API_BASE
        if not api_key:
            raise RuntimeError(
                "nvidia/* model requested but NVIDIA_NIM_API_KEY "
                "(or LLM_API_KEY) is not set."
            )
    else:
        # Pass through whatever the user wrote (openai/..., anthropic/..., ollama_chat/..., etc.)
        litellm_model = f"{provider}/{model_id}"

    kwargs["model"] = litellm_model
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base

    return LiteLlm(**kwargs)


def _build_council(strategy: str):
    """Construct a CouncilLlm from COUNCIL_MODELS / COUNCIL_JUDGE env vars.

    Recursively calls build_model() for each child spec so children can be any
    supported provider (gemini, cloudflare, openai, ollama_chat, ...).
    """
    from council_llm import CouncilLlm  # local import: optional dependency on ADK

    raw_children = os.environ.get("COUNCIL_MODELS", "")
    specs = [s.strip() for s in raw_children.split(",") if s.strip()]
    if not specs:
        raise RuntimeError(
            "LLM_MODEL=council/* requires COUNCIL_MODELS to be set "
            "(comma-separated provider/model-id specs)."
        )

    children = []
    for spec in specs:
        # Use a temporary env var so build_model() picks up this child spec
        # without permanently mutating LLM_MODEL.
        saved = os.environ.get("LLM_MODEL")
        os.environ["LLM_MODEL"] = spec
        try:
            children.append(build_model())
        finally:
            if saved is None:
                os.environ.pop("LLM_MODEL", None)
            else:
                os.environ["LLM_MODEL"] = saved

    judge_spec = os.environ.get("COUNCIL_JUDGE")
    judge = None
    if judge_spec:
        saved = os.environ.get("LLM_MODEL")
        os.environ["LLM_MODEL"] = judge_spec
        try:
            judge = build_model()
        finally:
            if saved is None:
                os.environ.pop("LLM_MODEL", None)
            else:
                os.environ["LLM_MODEL"] = saved

    return CouncilLlm(strategy=strategy, children=children, judge=judge)


# --- Auto council from discovered_models.json -------------------------------

DISCOVERED_MANIFEST_PATH = os.environ.get(
    "DISCOVERED_MODELS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "discovered_models.json"),
)


# Provider prefixes recognized by build_model() that we can route to from the manifest.
# Cloudflare/Nvidia get explicit handling above; everything else falls through to LiteLLM
# with the bare `<provider>/<model_id>` prefix.
_PROVIDER_PREFIX_MAP = {
    "nvidia": "nvidia",
    "cloudflare": "cloudflare",
    "groq": "groq",
    "cerebras": "cerebras",
    "mistral": "mistral",
    "deepseek": "deepseek",
    "fireworks": "fireworks_ai",  # LiteLLM uses fireworks_ai/
    "sambanova": "sambanova",
    "cohere": "cohere",
    "ai21": "ai21",
    "google": "gemini",
}


def _load_discovered_manifest() -> dict:
    import json as _json
    if not os.path.exists(DISCOVERED_MANIFEST_PATH):
        raise RuntimeError(
            f"LLM_MODEL=auto/* requires {DISCOVERED_MANIFEST_PATH}. "
            "Run samples/agent/adk/_shared/discover_free_models.py first."
        )
    with open(DISCOVERED_MANIFEST_PATH) as fh:
        return _json.load(fh)


def _build_auto_council(strategy: str):
    """Pick a diverse council from live models in discovered_models.json.

    Knobs (all optional):
      COUNCIL_SIZE          int, default 3 (15 when strategy is "resilient")
      COUNCIL_PROVIDERS     comma-separated allow-list (default: all from manifest
                            that have a recognized prefix in _PROVIDER_PREFIX_MAP)
      COUNCIL_JUDGE         provider/model spec; defaults to first picked child
    """
    from council_llm import CouncilLlm  # local import to avoid hard dep on ADK at module load

    manifest = _load_discovered_manifest()
    # Resilient strategy defaults to a deep pool so we can actually exhaust failures
    # before giving up; smaller pools defeat the whole point.
    default_size = "15" if strategy == "resilient" else "3"
    size = int(os.environ.get("COUNCIL_SIZE", default_size))
    allow_raw = os.environ.get("COUNCIL_PROVIDERS", "")
    allow = {s.strip() for s in allow_raw.split(",") if s.strip()} or None

    # Flatten and sort live models by latency (fastest first), grouped per provider.
    per_provider: dict[str, list[dict]] = {}
    for provider, data in manifest.get("providers", {}).items():
        if provider not in _PROVIDER_PREFIX_MAP:
            continue
        if allow is not None and provider not in allow:
            continue
        live = sorted(data.get("live", []), key=lambda x: x.get("latency_ms", 1 << 30))
        if live:
            per_provider[provider] = live

    if not per_provider:
        raise RuntimeError(
            f"No live models found in {DISCOVERED_MANIFEST_PATH} (after filtering). "
            "Re-run the discovery probe."
        )

    # Round-robin one model per provider until we have `size` children.
    queues = {p: list(models) for p, models in per_provider.items()}
    picks: list[tuple[str, str]] = []
    while len(picks) < size and any(queues.values()):
        for provider in list(queues.keys()):
            if not queues[provider]:
                continue
            entry = queues[provider].pop(0)
            picks.append((provider, entry["id"]))
            if len(picks) >= size:
                break

    if not picks:
        raise RuntimeError("Auto-council selection produced 0 children")

    # Build child models via recursive build_model() calls.
    children = []
    for provider, model_id in picks:
        spec = f"{_PROVIDER_PREFIX_MAP[provider]}/{model_id}"
        saved = os.environ.get("LLM_MODEL")
        os.environ["LLM_MODEL"] = spec
        try:
            children.append(build_model())
        finally:
            if saved is None:
                os.environ.pop("LLM_MODEL", None)
            else:
                os.environ["LLM_MODEL"] = saved

    judge = None
    judge_spec = os.environ.get("COUNCIL_JUDGE")
    if judge_spec:
        saved = os.environ.get("LLM_MODEL")
        os.environ["LLM_MODEL"] = judge_spec
        try:
            judge = build_model()
        finally:
            if saved is None:
                os.environ.pop("LLM_MODEL", None)
            else:
                os.environ["LLM_MODEL"] = saved

    import logging
    logging.getLogger(__name__).info(
        "auto-council[%s] picked %d children: %s",
        strategy,
        len(children),
        ", ".join(f"{p}/{m}" for p, m in picks),
    )
    return CouncilLlm(strategy=strategy, children=children, judge=judge)


def _set_llm_model(spec: Optional[str]) -> Optional[str]:
    """Helper: swap LLM_MODEL, returning the previous value for later restore."""
    prev = os.environ.get("LLM_MODEL")
    if spec is None:
        os.environ.pop("LLM_MODEL", None)
    else:
        os.environ["LLM_MODEL"] = spec
    return prev


def _build_hybrid(strategy: str):
    """Build a HybridToolCouncilLlm: tool turns -> single model, UI turns -> council."""
    from council_llm import HybridToolCouncilLlm

    tool_spec = os.environ.get("TOOL_MODEL")
    if not tool_spec:
        raise RuntimeError(
            "LLM_MODEL=hybrid/* requires TOOL_MODEL env var "
            "(e.g. TOOL_MODEL=nvidia/meta/llama-3.3-70b-instruct)."
        )

    # Build tool model
    prev = _set_llm_model(tool_spec)
    try:
        tool_model = build_model()
    finally:
        _set_llm_model(prev)

    # Build council from the auto pool with the given strategy
    prev = _set_llm_model(f"auto/{strategy}")
    try:
        council = build_model()
    finally:
        _set_llm_model(prev)

    return HybridToolCouncilLlm(tool_model=tool_model, council=council)
