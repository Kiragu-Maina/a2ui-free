"""Test resilient mode: deep pool, real providers, force a few rate-limit
hits to exercise quarantine + fallback."""

import asyncio
import os
import sys
from pathlib import Path

for line in Path(".env.test").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

os.environ["DISCOVERED_MODELS_PATH"] = str(Path("discovered_models.json").resolve())
SHARED = Path(__file__).resolve().parent.parent / "A2UI" / "samples" / "agent" / "adk" / "_shared"
sys.path.insert(0, str(SHARED))

os.environ["LLM_MODEL"] = "auto/resilient"
os.environ["COUNCIL_SIZE"] = "10"
os.environ["COUNCIL_PROVIDERS"] = "nvidia,groq,cerebras,mistral"

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

from model_factory import build_model
from council_llm import _QUARANTINE
from google.adk.models.llm_request import LlmRequest
from google.genai import types as genai_types

council = build_model()
print(f"\nBuilt {type(council).__name__}: strategy={council._strategy}, size={len(council._children)}")
for i, c in enumerate(council._children):
    print(f"  [{i+1:2}] {c.model}")


async def fire(label, prompt):
    print(f"\n=== {label} ===")
    req = LlmRequest(
        model=council.model,
        contents=[genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=prompt)])],
    )
    final = None
    async for r in council.generate_content_async(req, stream=False):
        final = r
    if final is None or getattr(final, "error_code", None):
        print(f"  FAILED: {getattr(final, 'error_message', '?')}")
    else:
        try:
            text = final.content.parts[0].text
        except Exception:
            text = repr(final)[:200]
        print(f"  RESULT: {text!r}")
    if _QUARANTINE:
        print(f"  quarantined ({len(_QUARANTINE)}):")
        for m, until in _QUARANTINE.items():
            import time
            print(f"    {m}  ({int(until - time.time())}s left)")


async def main():
    await fire("first call", "Reply with only: A")
    await fire("second call", "Reply with only: B")
    await fire("third call (rapid -- may hit rate limits)", "Reply with only: C")


asyncio.run(main())
