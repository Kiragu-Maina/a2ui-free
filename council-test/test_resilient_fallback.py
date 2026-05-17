"""Verify resilient fallback: inject 2 failing children before 1 live one;
council should walk past the failures, quarantine them, and return the live result."""

import asyncio
import os
import sys
from pathlib import Path

for line in Path(".env.test").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

SHARED = Path(__file__).resolve().parent.parent / "A2UI" / "samples" / "agent" / "adk" / "_shared"
sys.path.insert(0, str(SHARED))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

from council_llm import CouncilLlm, _QUARANTINE, _is_quarantined
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.models.lite_llm import LiteLlm
from google.genai import types as genai_types


class _Failing(BaseLlm):
    """Always raises a typed error that the quarantine classifier should catch."""

    def __init__(self, model: str, error_msg: str):
        super().__init__(model=model)
        object.__setattr__(self, "_error_msg", error_msg)

    @classmethod
    def supported_models(cls):
        return []

    async def generate_content_async(self, request: LlmRequest, stream: bool = False):
        raise RuntimeError(self._error_msg)
        yield  # noqa (make this an async generator)


real_child = LiteLlm(
    model="openai/meta/llama-3.3-70b-instruct",
    api_key=os.environ["NVIDIA_NIM_API_KEY"],
    api_base="https://integrate.api.nvidia.com/v1",
)

council = CouncilLlm(
    strategy="resilient",
    children=[
        _Failing("fake/rate-limited", "RateLimitError: 429 too many requests"),
        _Failing("fake/server-error", "Internal Server Error: 503 service unavailable"),
        real_child,
    ],
)

req = LlmRequest(
    model=council.model,
    contents=[genai_types.Content(role="user", parts=[genai_types.Part.from_text(text="Say only: ok")])],
)


async def main():
    print("=== run 1 (should fall through 2 failures to real child) ===")
    final = None
    async for r in council.generate_content_async(req, stream=False):
        final = r
    text = final.content.parts[0].text if final and final.content else repr(final)
    print(f"  RESULT: {text!r}")
    print(f"  quarantine map: {list(_QUARANTINE.keys())}")
    print(f"  fake/rate-limited quarantined? {_is_quarantined('fake/rate-limited')}")
    print(f"  fake/server-error quarantined? {_is_quarantined('fake/server-error')}")

    print("\n=== run 2 (should skip quarantined immediately, no log lines) ===")
    final = None
    async for r in council.generate_content_async(req, stream=False):
        final = r
    text = final.content.parts[0].text if final and final.content else repr(final)
    print(f"  RESULT: {text!r}")


asyncio.run(main())
