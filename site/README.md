# a2ui-free.alkenacode.dev

Public deployment of the [restaurant_finder](../A2UI/samples/agent/adk/restaurant_finder)
A2UI agent backed by the [`_shared/model_factory.py`](../A2UI/samples/agent/adk/_shared/model_factory.py)
free-tier council.

## Architecture

```
browser  ->  nginx (host)  ->  client container (Lit shell, static)        [4904]
                         \->  agent container  (Python ADK + uvicorn)      [4905]
                                  |
                                  +--> NVIDIA NIM / Groq / Cerebras / Mistral / Cloudflare
```

The browser fetches `https://a2ui-free.alkenacode.dev/agent/.well-known/agent-card.json`
to bootstrap the A2A client. nginx strips the `/agent/` prefix and forwards
to the Python server on `127.0.0.1:4905`. Subsequent A2A calls follow the
agent card's `url` field, which the Python launcher (`start.py`) sets to
the public `PUBLIC_URL`.

## Deploy

```bash
cp site/.env.example site/.env
# fill in the free-tier keys (NVIDIA, Groq, Cerebras, Mistral, Cloudflare)
docker compose -f site/docker-compose.yml --env-file site/.env up -d --build
```

Then enable the nginx vhost at `site/nginx/a2ui-free.alkenacode.dev.conf` and
issue TLS via `certbot --nginx -d a2ui-free.alkenacode.dev`.

## Why a custom launcher

`site/agent/start.py` differs from the upstream `restaurant_finder/__main__.py`
in three ways:

1. Skips the hard `GEMINI_API_KEY` pre-check. With `LLM_MODEL=hybrid/...` the
   model factory routes through the free-tier council; the upstream check
   assumes Gemini is the active model and fails the boot otherwise.
2. Reads `PUBLIC_URL` and uses it as the agent's `base_url` so the AgentCard
   advertises the public domain rather than the container's bind address.
3. Reads `CORS_ORIGIN` so the deploy can widen the allow-origin regex past
   the upstream's localhost-only default.

No A2UI source file is modified. Everything lives in this `site/` subdir.
