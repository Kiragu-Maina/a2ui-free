# Python A2A agent server (restaurant_finder + _shared model factory)
# Build context: repo root (the a2ui-free repo)
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Astral's package manager)
COPY --from=ghcr.io/astral-sh/uv:0.5.10 /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy the workspace bits the restaurant_finder agent needs:
#  - the sample itself
#  - the _shared model_factory + council_llm code
#  - the A2UI Python SDK that restaurant_finder declares as a path source
COPY A2UI/samples/agent/adk/restaurant_finder /app/A2UI/samples/agent/adk/restaurant_finder
COPY A2UI/samples/agent/adk/_shared           /app/A2UI/samples/agent/adk/_shared
COPY A2UI/agent_sdks/python                   /app/A2UI/agent_sdks/python
# a2ui-agent-sdk's build hook walks up from its source dir looking for a
# sibling `specification/` directory at the repo root. Without it, uv sync
# fails with "Could not find repository root".
COPY A2UI/specification                       /app/A2UI/specification

# Working directory is the restaurant_finder folder so relative paths
# (restaurant_data.json, images/) resolve.
WORKDIR /app/A2UI/samples/agent/adk/restaurant_finder

# Drop in the public launcher.
COPY site/agent/start.py /app/A2UI/samples/agent/adk/restaurant_finder/start.py

# Install dependencies. uv sync respects pyproject.toml + uses the editable
# a2ui-agent-sdk path source defined in the upstream pyproject.toml.
# Extra deps the public launcher needs explicitly:
RUN uv sync --no-dev \
    && uv pip install python-dotenv uvicorn

EXPOSE 10002

CMD ["uv", "run", "python", "start.py"]
