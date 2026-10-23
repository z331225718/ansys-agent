import { Agent, type AgentMessage, type AgentOptions } from "@earendil-works/pi-agent-core";

import { BridgePipeClient } from "./bridge/pipe-client.js";
import { createReadonlyOperatorTools, READ_ONLY_OPERATOR_SYSTEM_PROMPT } from "./readonly-tools.js";

/**
 * Profiles are process configuration, not a capability selected by the model.
 * Only observer is implemented until the Python controlled-recorded composition
 * root and write guards exist.
 */
export type PiAgentHostProfile = "observer" | "controlled-recorded" | "live-approved";

export type PiAgentHostConfig = {
  profile: PiAgentHostProfile;
};

export function createPiAgentHost(
  client: BridgePipeClient,
  options: AgentOptions,
  config: PiAgentHostConfig,
): Agent {
  if (config.profile !== "observer") {
    throw new Error(`Pi Agent Host profile is not implemented: ${config.profile}`);
  }

  const agent = new Agent({
    ...options,
    initialState: {
      ...options.initialState,
      systemPrompt: options.initialState?.systemPrompt || READ_ONLY_OPERATOR_SYSTEM_PROMPT,
    },
    transformContext: async (messages, signal) => {
      const transformed = options.transformContext ? await options.transformContext(messages, signal) : messages;
      return transformed.map(redactUserMessageForProvider);
    },
    toolExecution: "parallel",
  });
  agent.state.tools = createReadonlyOperatorTools(client);
  return agent;
}

function redactUserMessageForProvider(message: AgentMessage): AgentMessage {
  if (message.role !== "user") return message;
  const text = typeof message.content === "string"
    ? message.content
    : message.content.filter((item) => item.type === "text").map((item) => item.text).join("\n");
  if (!containsSensitiveContent(text)) return message;
  return {
    ...message,
    content: "[Sensitive user content was withheld from the model. Ask the user to use the trusted controller.]",
  };
}

function containsSensitiveContent(value: string): boolean {
  return /\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+)/i.test(value)
    || /(?:secret|token|password|api[_-]?key)\s*[:=]/i.test(value)
    || /(?:[A-Za-z]:[\\/]|\\\\|\/)[^\s]+/.test(value);
}
