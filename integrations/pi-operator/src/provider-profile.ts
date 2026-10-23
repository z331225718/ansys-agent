import { createHash } from "node:crypto";

export type ProviderAdapter = "deepseek";
export type ProviderProfile = {
  adapter: ProviderAdapter;
  providerId: string;
  baseUrl: string;
  model: string;
  apiKeyEnv: string;
  digest: string;
  originDigest: string;
};

const DEEPSEEK_MODELS = new Set(["deepseek-v4-flash"]);

export function loadProviderProfile(environment: NodeJS.ProcessEnv, requestedModel: string): ProviderProfile {
  if (environment.AEDT_AGENT_PI_PROVIDER !== "deepseek") throw new Error("AEDT_AGENT_PI_PROVIDER must explicitly select deepseek");
  for (const name of ["AEDT_AGENT_PI_BASE_URL", "AEDT_AGENT_PI_ALLOWED_ORIGINS", "AEDT_AGENT_PI_ALLOWED_MODELS", "AEDT_AGENT_PI_API_KEY_ENV"]) {
    if (environment[name]) throw new Error(`${name} is not supported by the fixed DeepSeek observer adapter`);
  }
  return fixedDeepSeekProfile(environment, requestedModel);
}

function fixedDeepSeekProfile(environment: NodeJS.ProcessEnv, requestedModel: string): ProviderProfile {
  const model = configuredModel(environment, requestedModel);
  if (!DEEPSEEK_MODELS.has(model)) throw new Error("AEDT_AGENT_PI_MODEL is not an approved DeepSeek observer model");
  return profile({ adapter: "deepseek", providerId: "deepseek", baseUrl: "https://api.deepseek.com", model, apiKeyEnv: "DEEPSEEK_API_KEY" });
}

function configuredModel(environment: NodeJS.ProcessEnv, requestedModel: string): string {
  const model = requireValue(environment.AEDT_AGENT_PI_MODEL, "AEDT_AGENT_PI_MODEL");
  if (model !== requestedModel) throw new Error("--model must exactly match AEDT_AGENT_PI_MODEL");
  return model;
}

function requireValue(value: string | undefined, name: string): string {
  if (!value?.trim()) throw new Error(`${name} is required`);
  return value.trim();
}

function profile(value: Omit<ProviderProfile, "digest" | "originDigest">): ProviderProfile {
  const canonical = JSON.stringify({ compat_policy_version: 1, adapter: value.adapter, provider_id: value.providerId, base_url: value.baseUrl, model: value.model, api_key_env: value.apiKeyEnv, context_window: 128000, max_tokens: 8192 });
  return { ...value, digest: sha256(canonical), originDigest: sha256(new URL(value.baseUrl).origin) };
}

function sha256(value: string): string { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }
