"""End-to-end test: LLM_MODEL=auto/<strategy> reads discovered_models.json
and builds a fresh diverse council. Run against real providers."""

import asyncio
import os
import sys
from pathlib import Path

# Load env
for line in Path(".env.test").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

# Point at the manifest we just generated
os.environ["DISCOVERED_MODELS_PATH"] = str(Path("discovered_models.json").resolve())

SHARED = Path(__file__).resolve().parent.parent / "A2UI" / "samples" / "agent" / "adk" / "_shared"
sys.path.insert(0, str(SHARED))

os.environ["LLM_MODEL"] = "auto/majority-vote"
os.environ["COUNCIL_SIZE"] = "3"
os.environ["COUNCIL_PROVIDERS"] = "nvidia,groq,cerebras"

from model_factory import build_model
from google.adk.models.llm_request import LlmRequest
from google.genai import types as genai_types

council = build_model()
print(f"Built {type(council).__name__}: strategy={council._strategy}")
for c in council._children:
    print(f"  child: model={c.model} base={getattr(c, 'api_base', '')[:60]}")

req = LlmRequest(
    model=council.model,
    contents=[genai_types.Content(role="user", parts=[genai_types.Part.from_text(text="Say only: ok")])],
)


async def run():
    final = None
    async for resp in council.generate_content_async(req, stream=False):
        final = resp
    if final is None or getattr(final, "error_code", None):
        print(f"FAILED: {final and getattr(final, 'error_message', '')}")
        return
    try:
        text = final.content.parts[0].text  # type: ignore[union-attr]
    except Exception:
        text = repr(final)
    print(f"\n=== Auto council verdict ===\n{text!r}")


asyncio.run(run())
