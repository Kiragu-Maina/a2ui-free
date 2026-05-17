"""End-to-end test: A2UI build_model() council against live NVIDIA NIM."""

import asyncio
import os
import sys
from pathlib import Path

# Load env
for line in Path(".env.test").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

# Make the shared module importable
SHARED = Path(__file__).resolve().parent.parent / "A2UI" / "samples" / "agent" / "adk" / "_shared"
sys.path.insert(0, str(SHARED))

os.environ["LLM_MODEL"] = "council/majority-vote"
os.environ["COUNCIL_MODELS"] = ",".join([
    "nvidia/meta/llama-4-maverick-17b-128e-instruct",
    "nvidia/mistralai/ministral-14b-instruct-2512",
    "nvidia/openai/gpt-oss-20b",
])

from model_factory import build_model
from google.adk.models.llm_request import LlmRequest
from google.genai import types as genai_types

council = build_model()
print(f"Built {type(council).__name__}: strategy={council._strategy}")
for c in council._children:
    print(f"  child: {type(c).__name__} model={c.model}")

req = LlmRequest(
    model=council.model,
    contents=[
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text="What is 2+2? Answer with ONLY the number.")],
        )
    ],
)


async def run():
    final = None
    async for resp in council.generate_content_async(req, stream=False):
        final = resp
    if final is None:
        print("NO RESPONSE")
        return
    if getattr(final, "error_code", None):
        print(f"ERROR {final.error_code}: {final.error_message}")
        return
    try:
        text = final.content.parts[0].text  # type: ignore[union-attr]
    except Exception:
        text = repr(final)
    print(f"\n=== Council verdict ===\n{text!r}")


asyncio.run(run())
