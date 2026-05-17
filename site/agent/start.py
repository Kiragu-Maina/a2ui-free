"""Public-facing launcher for the restaurant_finder A2A agent.

Differs from upstream restaurant_finder/__main__.py in three ways:
  1. Skips the hard GEMINI_API_KEY pre-check. The shared model_factory routes
     through LLM_MODEL; the pre-check is only correct for the upstream Gemini
     default. If GOOGLE_AI_KEY is set, it is copied into GEMINI_API_KEY so the
     native google-genai client also works.
  2. Reads PUBLIC_URL and uses it as the agent's base_url so the AgentCard
     advertises the public domain rather than the container's bind address.
  3. CORS_ORIGIN env var lets the deploy widen the allow-origin regex past the
     localhost-only default.

Working directory must be the restaurant_finder folder so that relative paths
to restaurant_data.json and images/ resolve correctly.
"""

import logging
import os
import sys

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

load_dotenv()

# Allow GOOGLE_AI_KEY (the name Kiragu's local council-test/.env.test uses) to
# stand in for GEMINI_API_KEY, which is what the underlying google-genai
# library reads. Same key, different name.
for src in ("GOOGLE_AI_KEY", "GOOGLE_API_KEY"):
    if not os.getenv("GEMINI_API_KEY") and os.getenv(src):
        os.environ["GEMINI_API_KEY"] = os.environ[src]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Import the agent after env munging so the model_factory sees LLM_MODEL.
from agent import RestaurantAgent  # noqa: E402
from agent_executor import RestaurantAgentExecutor  # noqa: E402


def main() -> None:
    public_url = os.getenv("PUBLIC_URL", "http://localhost:10002").rstrip("/")
    cors_origin = os.getenv("CORS_ORIGIN", r"http://localhost:\d+")
    listen_host = os.getenv("LISTEN_HOST", "0.0.0.0")
    listen_port = int(os.getenv("LISTEN_PORT", "10002"))

    logger.info("public_url=%s listen=%s:%d", public_url, listen_host, listen_port)
    logger.info("CORS allow_origin_regex=%s", cors_origin)
    logger.info("LLM_MODEL=%s", os.getenv("LLM_MODEL", "(default)"))

    agent = RestaurantAgent(base_url=public_url)
    agent_executor = RestaurantAgentExecutor(agent)
    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent.agent_card,
        http_handler=request_handler,
    )
    app = server.build()
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=cors_origin,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory="images"), name="static")

    uvicorn.run(app, host=listen_host, port=listen_port)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Agent server failed to start")
        sys.exit(1)
