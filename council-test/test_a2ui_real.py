"""End-to-end test: actually invoke the restaurant_finder A2UI agent against
a free council/single model and verify it emits A2UI-validated JSON."""

import asyncio
import os
import sys
from pathlib import Path

# Load env
for line in Path(".env.test").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

os.environ["DISCOVERED_MODELS_PATH"] = str(Path("discovered_models.json").resolve())

# Add restaurant_finder dir to sys.path so its module-local imports work
SAMPLE = Path("../A2UI/samples/agent/adk/restaurant_finder").resolve()
sys.path.insert(0, str(SAMPLE))
sys.path.insert(0, str(SAMPLE.parent / "_shared"))

# Stub the missing tools/prompt_builder bits restaurant_finder pulls in
# (those import google.adk.runners etc — already installed)


async def main():
    # Pick a single proven model first (less variability than council)
    os.environ["LLM_MODEL"] = "nvidia/meta/llama-3.3-70b-instruct"

    from agent import RestaurantAgent

    agent = RestaurantAgent(base_url="http://localhost:10000")
    print(f"Built RestaurantAgent")
    print(f"  text runner model:  {agent._text_runner.agent.model.model}")
    for v, runner in agent._ui_runners.items():
        print(f"  ui[{v}] runner model: {runner.agent.model.model}")

    query = "Find me 2 chinese restaurants in San Francisco"

    async def run(label, **kwargs):
        print(f"\n=== {label} ===")
        a2ui_parts, text_parts = 0, 0
        chunks = []
        try:
            async for event in agent.stream(query=query, session_id=label, **kwargs):
                if event.get("is_task_complete"):
                    for p in event.get("parts", []) or []:
                        root = p.root
                        cls = type(root).__name__
                        if cls == "DataPart":
                            a2ui_parts += 1
                            chunks.append(("data", str(root.data)[:250]))
                        else:
                            text_parts += 1
                            chunks.append(("text", (getattr(root, "text", "") or "")[:250]))
                else:
                    for p in event.get("parts", []) or []:
                        root = p.root
                        if type(root).__name__ == "DataPart":
                            a2ui_parts += 1
                            chunks.append(("data-stream", str(root.data)[:250]))
            print(f"  RESULT: {a2ui_parts} A2UI parts, {text_parts} text parts")
            for kind, snippet in chunks[:5]:
                print(f"    [{kind}] {snippet}")
        except Exception as e:
            print(f"  RAISED: {type(e).__name__}: {str(e)[:300]}")

    # Test 1: text mode (no UI surface) — exercises tool calling + plain text generation
    await run("text-mode", use_streaming=False)

    # Test 2: UI mode v0.9 — exercises A2UI schema generation + validation
    await run("ui-mode-v0.9", ui_version="0.9", use_streaming=False)


asyncio.run(main())
