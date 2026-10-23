import assert from "node:assert/strict";
import { test } from "node:test";

import { guardedFetch } from "../src/provider-transport.js";

test("OpenAI-compatible transport rejects an unapproved origin and disables redirects", async () => {
  let options: RequestInit | undefined;
  const original = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    options = init;
    return new Response("ok");
  }) as typeof fetch;
  const guarded = guardedFetch(original, "https://llm.company.example");
  await guarded("https://llm.company.example/v1/chat/completions", { redirect: "follow" });
  assert.equal(options?.redirect, "error");
  await assert.rejects(guarded("https://unapproved.example/v1/chat/completions"), /not approved/);
});
