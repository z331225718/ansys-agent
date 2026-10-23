import { type Agent, type AgentOptions } from "@earendil-works/pi-agent-core";

import { createPiAgentHost } from "./agent-host.js";
import { BridgePipeClient } from "./bridge/pipe-client.js";

export function createReadonlyOperatorAgent(client: BridgePipeClient, options: AgentOptions): Agent {
  return createPiAgentHost(client, options, { profile: "observer" });
}
