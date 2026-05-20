/**
 * Friendly labels for the seven free-tier providers in the discovered_models
 * manifest. The keys match the provider prefix the model_factory emits
 * (e.g. "cerebras/gpt-oss-120b" -> prefix "cerebras").
 *
 * Each entry includes a one-line tagline so a non-developer hovering a
 * council card learns what's special about that vendor without leaving
 * the page.
 */

export interface ProviderInfo {
  label: string;
  tagline: string;
}

const PROVIDERS: Record<string, ProviderInfo> = {
  cerebras: {
    label: "Cerebras",
    tagline: "LPU-accelerated open-source models",
  },
  groq: {
    label: "Groq",
    tagline: "LPU silicon, sub-second latency",
  },
  mistral: {
    label: "Mistral",
    tagline: "Paris-based, open weights",
  },
  nvidia: {
    label: "NVIDIA NIM",
    tagline: "Perpetual free tier, 131k context",
  },
  cloudflare: {
    label: "Cloudflare Workers AI",
    tagline: "Models on Cloudflare's edge",
  },
  google: {
    label: "Google",
    tagline: "Gemini free tier",
  },
  gemini: {
    label: "Google Gemini",
    tagline: "Native Gemini API",
  },
  deepseek: {
    label: "DeepSeek",
    tagline: "Reasoning-focused open-source",
  },
};

const FALLBACK: ProviderInfo = {
  label: "Unknown provider",
  tagline: "",
};

/**
 * Parse a model id like "cerebras/gpt-oss-120b" or "nvidia/meta/llama-3.3-70b"
 * into a friendly label, a short model code, and the provider tagline.
 */
export function describeModel(modelId: string | undefined | null): {
  provider: string;
  providerInfo: ProviderInfo;
  modelCode: string;
} {
  if (!modelId) {
    return { provider: "?", providerInfo: FALLBACK, modelCode: "(unknown)" };
  }
  const slash = modelId.indexOf("/");
  const provider = slash >= 0 ? modelId.slice(0, slash) : modelId;
  const modelCode = slash >= 0 ? modelId.slice(slash + 1) : modelId;
  return {
    provider,
    providerInfo: PROVIDERS[provider] ?? FALLBACK,
    modelCode,
  };
}
