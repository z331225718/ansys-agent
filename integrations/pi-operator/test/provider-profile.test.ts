import assert from "node:assert/strict";
import { test } from "node:test";

import { loadProviderProfile } from "../src/provider-profile.js";

const deepseek = { AEDT_AGENT_PI_PROVIDER: "deepseek", AEDT_AGENT_PI_MODEL: "deepseek-v4-flash", DEEPSEEK_API_KEY: "canary-secret" };

test("fixed DeepSeek profile is digest-bound without the key value", () => {
  const profile = loadProviderProfile(deepseek, "deepseek-v4-flash");
  assert.equal(profile.baseUrl, "https://api.deepseek.com");
  assert.equal(profile.providerId, "deepseek");
  assert.equal(profile.digest.includes("canary-secret"), false);
});

test("provider configuration fails closed for corp, overrides, and models other than flash", () => {
  assert.throws(() => loadProviderProfile({ ...deepseek, AEDT_AGENT_PI_PROVIDER: "corp-openai-compatible" }, "deepseek-v4-flash"), /select deepseek/);
  assert.throws(() => loadProviderProfile({ ...deepseek, AEDT_AGENT_PI_BASE_URL: "https://elsewhere.example/v1" }, "deepseek-v4-flash"), /not supported/);
  assert.throws(() => loadProviderProfile({ ...deepseek, AEDT_AGENT_PI_MODEL: "deepseek-v4-pro" }, "deepseek-v4-pro"), /approved/);
  assert.throws(() => loadProviderProfile({}, "deepseek-v4-flash"), /select deepseek/);
});
