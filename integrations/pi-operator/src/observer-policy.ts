import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { READ_ONLY_OPERATOR_SYSTEM_PROMPT } from "./readonly-tools.js";

export type ObserverPolicy = { skill: string; digest: string };

/** Production, gate, and recovery probes must bind exactly the same policy bytes. */
export async function loadObserverPolicy(): Promise<ObserverPolicy> {
  const skill = await readFile(fileURLToPath(new URL("../resources/observer-skill.md", import.meta.url)), "utf8");
  return { skill, digest: observerPolicyDigest(skill) };
}

export function observerPolicyDigest(skill: string): string {
  return "sha256:" + createHash("sha256").update(`${READ_ONLY_OPERATOR_SYSTEM_PROMPT}\n\n${skill}`, "utf8").digest("hex");
}
