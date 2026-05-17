#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Discover live free chat models across configured providers.

Probes every provider you have an API key for with a tiny "Reply: ok" prompt
and emits a JSON manifest of which models are live, slow, or dead.

Designed to run as a weekly cron job. Apps read the manifest at startup and
build their council from the live pool, so dead/rotated models are handled
without code changes.

Output schema (discovered_models.json):
{
  "timestamp": "2026-05-17T10:50:00Z",
  "providers": {
    "nvidia": {
      "live":    [{"id": "...", "latency_ms": 1234}],
      "slow":    [{"id": "...", "reason": "timeout 60s"}],
      "dead":    [{"id": "...", "reason": "HTTP 404"}],
      "checked": 84
    },
    ...
  }
}

Usage
-----
  python discover_free_models.py --out discovered_models.json
  python discover_free_models.py --providers nvidia,cloudflare,groq
  python discover_free_models.py --timeout 30 --parallel 8

Env vars consumed (any missing key skips that provider):
  NVIDIA_NIM_API_KEY
  CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_AI_TOKEN (or CLOUDFLARE_API_TOKEN)
  GROQ_API_KEY
  CEREBRAS_API_KEY
  MISTRAL_API_KEY
  COHERE_API_KEY
  DEEPSEEK_API_KEY
  FIREWORKS_API_KEY
  AI21_API_KEY
  HF_TOKEN
  SAMBANOVA_API_KEY
  GOOGLE_AI_KEY
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Iterable, Optional


NON_CHAT_RE = re.compile(
    r"(embed|embedqa|nemoretriever|nemoguard|guard|safety|reward|nvclip|"
    r"riva-translate|deplot|kosmos|fuyu|vila|neva|cosmos-reason|"
    r"vision|multimodal|video-detector|gliner|parse|llama-guard|"
    r"arctic-embed|bge-m3|recurrentgemma|whisper|tts|audio|image|imagen|"
    r"dall-e|stable-diffusion|midjourney|sdxl|sd3|transcribe|moderation|"
    r"rerank|reranker)",
    re.IGNORECASE,
)

PING_BODY = {
    "messages": [{"role": "user", "content": "Reply: ok"}],
    "max_tokens": 5,
    "stream": False,
}


@dataclass
class ProbeResult:
    model_id: str
    status: str  # "live" | "slow" | "dead"
    latency_ms: int = 0
    reason: str = ""


# --- HTTP helper -------------------------------------------------------------


def _post_chat(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, str]:
    data = json.dumps(body).encode()
    headers = {"User-Agent": "a2ui-probe/1.0", **headers}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:
        return 0, str(e)


def _get_json(url: str, headers: dict, timeout: float = 30) -> Optional[dict]:
    headers = {"User-Agent": "a2ui-probe/1.0", **headers}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        # log to stderr but keep returning None so callers handle it uniformly
        print(f"[catalog-fetch] {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _classify(code: int, body: str) -> tuple[str, str]:
    if code == 200:
        return "live", ""
    if code == 0:
        # timeout / network — treat as slow (cold start), not dead
        return "slow", body[:120]
    if code == 429:
        # rate-limited by provider trial quota — model IS live, retry later
        return "slow", "HTTP 429 rate-limited"
    return "dead", f"HTTP {code} {body[:100]}"


# --- per-provider probes -----------------------------------------------------


def _probe_openai_compat(
    *,
    list_url: Optional[str],
    chat_url: str,
    headers: dict,
    timeout: float,
    parallel: int,
    static_models: Optional[Iterable[str]] = None,
    model_extractor: Optional[Callable[[dict], list[str]]] = None,
    per_request_delay: float = 0.0,
) -> dict:
    """Generic probe for any OpenAI-compatible /v1 chat endpoint."""
    if static_models is not None:
        candidates = list(static_models)
    else:
        catalog = _get_json(list_url, headers) if list_url else None
        if not catalog:
            return {"live": [], "slow": [], "dead": [], "checked": 0, "error": "catalog fetch failed"}
        extract = model_extractor or (lambda d: [m["id"] for m in d.get("data", [])])
        try:
            ids = extract(catalog)
        except Exception as e:
            return {"live": [], "slow": [], "dead": [], "checked": 0, "error": f"extract: {e}"}
        candidates = sorted({i for i in ids if not NON_CHAT_RE.search(i)})

    results: list[ProbeResult] = []

    def _do(model_id: str) -> ProbeResult:
        body = {"model": model_id, **PING_BODY}
        t0 = time.time()
        code, text = _post_chat(chat_url, headers, body, timeout)
        ms = int((time.time() - t0) * 1000)
        status, reason = _classify(code, text)
        return ProbeResult(model_id=model_id, status=status, latency_ms=ms, reason=reason)

    if per_request_delay > 0 or parallel <= 1:
        # Serial path with optional inter-request delay (respects strict rate limits)
        for m in candidates:
            results.append(_do(m))
            if per_request_delay > 0:
                time.sleep(per_request_delay)
    else:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            for fut in as_completed({pool.submit(_do, m): m for m in candidates}):
                results.append(fut.result())

    bucket: dict[str, list[dict]] = {"live": [], "slow": [], "dead": []}
    for r in results:
        if r.status == "live":
            bucket["live"].append({"id": r.model_id, "latency_ms": r.latency_ms})
        elif r.status == "slow":
            bucket["slow"].append({"id": r.model_id, "reason": r.reason})
        else:
            bucket["dead"].append({"id": r.model_id, "reason": r.reason})
    for v in bucket.values():
        v.sort(key=lambda x: x["id"])
    bucket["checked"] = len(candidates)
    return bucket


def probe_nvidia(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("NVIDIA_NIM_API_KEY")
    if not key:
        return None
    return _probe_openai_compat(
        list_url="https://integrate.api.nvidia.com/v1/models",
        chat_url="https://integrate.api.nvidia.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
    )


def probe_cloudflare(timeout: float, parallel: int) -> Optional[dict]:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CLOUDFLARE_AI_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not (account and token):
        return None
    base = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1"
    # CF doesn't expose /models on the v1 endpoint; use the schema API.
    catalog = _get_json(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/models/search?per_page=200",
        {"Authorization": f"Bearer {token}"},
    )
    if not catalog:
        return {"live": [], "slow": [], "dead": [], "checked": 0, "error": "catalog fetch failed"}
    models = [m["name"] for m in catalog.get("result", []) if m.get("task", {}).get("name") in ("Text Generation",)]
    return _probe_openai_compat(
        list_url=None,
        chat_url=f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
        static_models=models,
    )


def probe_groq(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    return _probe_openai_compat(
        list_url="https://api.groq.com/openai/v1/models",
        chat_url="https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
    )


def probe_cerebras(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("CEREBRAS_API_KEY")
    if not key:
        return None
    return _probe_openai_compat(
        list_url="https://api.cerebras.ai/v1/models",
        chat_url="https://api.cerebras.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
    )


def probe_mistral(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        return None
    return _probe_openai_compat(
        list_url="https://api.mistral.ai/v1/models",
        chat_url="https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
    )


def probe_deepseek(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    return _probe_openai_compat(
        list_url="https://api.deepseek.com/models",
        chat_url="https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
    )


def probe_fireworks(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("FIREWORKS_API_KEY")
    if not key:
        return None
    return _probe_openai_compat(
        list_url="https://api.fireworks.ai/inference/v1/models",
        chat_url="https://api.fireworks.ai/inference/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
    )


def probe_sambanova(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("SAMBANOVA_API_KEY")
    if not key:
        return None
    return _probe_openai_compat(
        list_url="https://api.sambanova.ai/v1/models",
        chat_url="https://api.sambanova.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
    )


def probe_cohere(timeout: float, parallel: int) -> Optional[dict]:
    # Cohere trial keys are rate-limited to 20 chat calls/min, so we serialize
    # the probe regardless of the caller's --parallel flag. Cohere is not
    # natively OpenAI-compatible; use their compat shim.
    key = os.environ.get("COHERE_API_KEY")
    if not key:
        return None
    return _probe_openai_compat(
        list_url="https://api.cohere.ai/compatibility/v1/models",
        chat_url="https://api.cohere.ai/compatibility/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=1,  # serialize to respect trial rate limit (20 calls/min)
        per_request_delay=4.0,  # ~15 calls/min, well under the 20/min trial cap
    )


def probe_ai21(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("AI21_API_KEY")
    if not key:
        return None
    # AI21 doesn't expose /v1/models; use a known short list.
    return _probe_openai_compat(
        list_url=None,
        chat_url="https://api.ai21.com/studio/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=timeout,
        parallel=parallel,
        static_models=["jamba-mini", "jamba-large", "jamba-1.6-mini", "jamba-1.6-large"],
    )


def probe_google(timeout: float, parallel: int) -> Optional[dict]:
    key = os.environ.get("GOOGLE_AI_KEY")
    if not key:
        return None
    # Google AI Studio uses :generateContent on each model; probe a known list.
    static = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]
    results: list[ProbeResult] = []

    def _do(model_id: str) -> ProbeResult:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={key}"
        body = {"contents": [{"parts": [{"text": "Reply: ok"}]}], "generationConfig": {"maxOutputTokens": 5}}
        t0 = time.time()
        code, text = _post_chat(url, {"Content-Type": "application/json"}, body, timeout)
        ms = int((time.time() - t0) * 1000)
        status, reason = _classify(code, text)
        return ProbeResult(model_id=model_id, status=status, latency_ms=ms, reason=reason)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for fut in as_completed({pool.submit(_do, m): m for m in static}):
            results.append(fut.result())

    bucket: dict[str, list[dict]] = {"live": [], "slow": [], "dead": []}
    for r in results:
        if r.status == "live":
            bucket["live"].append({"id": r.model_id, "latency_ms": r.latency_ms})
        elif r.status == "slow":
            bucket["slow"].append({"id": r.model_id, "reason": r.reason})
        else:
            bucket["dead"].append({"id": r.model_id, "reason": r.reason})
    for v in bucket.values():
        v.sort(key=lambda x: x["id"])
    bucket["checked"] = len(static)
    return bucket


PROBES: dict[str, Callable[[float, int], Optional[dict]]] = {
    "nvidia": probe_nvidia,
    "cloudflare": probe_cloudflare,
    "groq": probe_groq,
    "cerebras": probe_cerebras,
    "mistral": probe_mistral,
    "deepseek": probe_deepseek,
    "fireworks": probe_fireworks,
    "sambanova": probe_sambanova,
    "cohere": probe_cohere,
    "ai21": probe_ai21,
    "google": probe_google,
}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default="discovered_models.json", help="Output manifest path")
    p.add_argument(
        "--providers",
        default=",".join(PROBES.keys()),
        help="Comma-separated provider list (default: all configured)",
    )
    p.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout seconds")
    p.add_argument("--parallel", type=int, default=8, help="Concurrent probes per provider")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    requested = [s.strip() for s in args.providers.split(",") if s.strip()]
    manifest = {
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "providers": {},
    }

    for name in requested:
        probe = PROBES.get(name)
        if probe is None:
            if not args.quiet:
                print(f"[skip] unknown provider: {name}", file=sys.stderr)
            continue
        if not args.quiet:
            print(f"[probe] {name} ...", file=sys.stderr)
        try:
            result = probe(args.timeout, args.parallel)
        except Exception as e:
            result = {"live": [], "slow": [], "dead": [], "checked": 0, "error": str(e)}
        if result is None:
            if not args.quiet:
                print(f"[skip] {name}: no API key configured", file=sys.stderr)
            continue
        manifest["providers"][name] = result
        if not args.quiet:
            print(
                f"  live={len(result.get('live', []))} "
                f"slow={len(result.get('slow', []))} "
                f"dead={len(result.get('dead', []))} "
                f"checked={result.get('checked', 0)}",
                file=sys.stderr,
            )

    with open(args.out, "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    if not args.quiet:
        print(f"\nWrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
