"""Best-config test: hybrid + synthesize-best, NVIDIA Llama 3.3 70B as both
the tool model and the council judge. Council members are fast cross-provider
free models; judge cleans up reasoning traces and emits clean A2UI JSON."""

import asyncio
import os
import sys
from pathlib import Path

for line in Path(".env.test").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

os.environ["DISCOVERED_MODELS_PATH"] = str(Path("discovered_models.json").resolve())
os.environ["LLM_MODEL"] = "hybrid/synthesize-best"
os.environ["TOOL_MODEL"] = "nvidia/meta/llama-3.3-70b-instruct"
os.environ["COUNCIL_JUDGE"] = "nvidia/meta/llama-3.3-70b-instruct"
os.environ["COUNCIL_SIZE"] = "3"
os.environ["COUNCIL_PROVIDERS"] = "nvidia,groq,cerebras,mistral"

SAMPLE = Path("../A2UI/samples/agent/adk/restaurant_finder").resolve()
sys.path.insert(0, str(SAMPLE))
sys.path.insert(0, str(SAMPLE.parent / "_shared"))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("LiteLLM").setLevel(logging.WARNING)


async def main():
    from agent import RestaurantAgent

    agent = RestaurantAgent(base_url="http://localhost:10000")
    print("\n=== Config ===")
    print(f"  hybrid: {agent._text_runner.agent.model.model}")
    m = agent._text_runner.agent.model
    print(f"  tool: {m._tool.model}")
    print(f"  council: {m._council.model} (size={len(m._council._children)}, strategy={m._council._strategy})")
    print(f"  judge: {m._council._judge.model}")

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
                        if type(p.root).__name__ == "DataPart":
                            a2ui_parts += 1
            print(f"  RESULT: {a2ui_parts} A2UI parts, {text_parts} text parts")
            for kind, snippet in chunks[:6]:
                print(f"    [{kind}] {snippet}")
        except Exception as e:
            print(f"  RAISED: {type(e).__name__}: {str(e)[:300]}")

    await run("ui-mode-hybrid-judge", ui_version="0.9", use_streaming=False)


asyncio.run(main())
