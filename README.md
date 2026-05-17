# A2UI council + resilience extensions

Customized fork of [google/A2UI](https://github.com/google/A2UI) with a model
factory that runs A2UI agents on free LLM providers via:

- **Provider-agnostic model dispatch** — single env var `LLM_MODEL=<provider>/<id>`
- **Council strategies** — single, ranked-fallback, majority-vote, synthesize-best, resilient
- **Auto-pool councils** — diversified across providers from a manifest written by a weekly cron probe
- **Hybrid wrapper** — single tool-capable model for tool-decision turns, council for the post-tool generation turn
- **Quarantine** — rate-limited or 5xx providers are skipped for 60-300s

## Layout

```
A2UI/                         Fork of google/A2UI@main (depth-1 clone, customized)
  samples/agent/adk/
    _shared/
      model_factory.py        provider/<id>, council/<strategy>, auto/<strategy>, hybrid/<strategy>
      council_llm.py          CouncilLlm + HybridToolCouncilLlm + quarantine
      discover_free_models.py weekly probe across all configured providers
    .env.example              full env-var reference with recommended A2UI default config
    restaurant_finder/        sample agent refactored to call build_model()
    custom-components-example, orchestrator, mcp_app_proxy, rizzcharts/python,
    gemini_enterprise/*, personalized_learning  (all use build_model())

council-test/                 Local test workspace (uv venv, scratch scripts)
  test_council.py             council/majority-vote against live NVIDIA NIM
  test_auto_council.py        auto/majority-vote across NVIDIA+Groq+Cerebras
  test_resilient.py           auto/resilient with deep pool
  test_resilient_fallback.py  forced-failure test exercising the quarantine map
  test_a2ui_real.py           restaurant_finder end-to-end with single NVIDIA model
  test_hybrid_a2ui.py         restaurant_finder with hybrid/resilient
  test_hybrid_judge.py        recommended config: hybrid/synthesize-best with NVIDIA judge
  cron_discover.sh            wrapper installed on VPS for weekly model probe

slides-download/              The slidev deck from the conf talk, exported as PNG
packages/                     Downloaded distributables (npm/PyPI/pub.dev) — gitignored
```

## Recommended config for A2UI agents

Verified passing end-to-end against `restaurant_finder` on first-attempt validation:

```bash
export LLM_MODEL=hybrid/synthesize-best
export TOOL_MODEL=nvidia/meta/llama-3.3-70b-instruct
export COUNCIL_JUDGE=nvidia/meta/llama-3.3-70b-instruct
export COUNCIL_SIZE=3
export COUNCIL_PROVIDERS=nvidia,groq,cerebras,mistral
export NVIDIA_NIM_API_KEY=...
export GROQ_API_KEY=...
export CEREBRAS_API_KEY=...
export MISTRAL_API_KEY=...
```

Run:
```bash
cd A2UI/samples/agent/adk/restaurant_finder
uv run __main__.py
```

## Weekly free-model discovery

`samples/agent/adk/_shared/discover_free_models.py` probes every configured
provider's chat models with a tiny ping. Output is a `discovered_models.json`
manifest bucketing models into `live` / `slow` / `dead`.

On the VPS, `cron_discover.sh` runs weekly (Sundays 03:17 UTC), writes
`/home/shellwire/backend/discovered_models.json`, archives previous manifests
under `.model-discovery/` (90-day retention).

Shellwire's `HealthMonitorService` (in the council module) reads the manifest
at boot and on `@Cron('0 4 * * 1')` and marks any catalog model not in `live`
as `down`. A2UI agents using `LLM_MODEL=auto/*` read the same manifest to
build a fresh diversified council each time.

## Provider env vars (one of each)

| Provider | Env vars |
|---|---|
| NVIDIA NIM | `NVIDIA_NIM_API_KEY` |
| Cloudflare Workers AI | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_AI_TOKEN` (or `CLOUDFLARE_API_TOKEN`) |
| Groq | `GROQ_API_KEY` |
| Cerebras | `CEREBRAS_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| Google Gemini | `GOOGLE_API_KEY` (native) or `GOOGLE_AI_KEY` (probe) |
| OpenAI / Anthropic / others | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` |

See `A2UI/samples/agent/adk/.env.example` for the full reference + example
configs for each mode (single, council, auto, hybrid, resilient).
