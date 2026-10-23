import { createWriteStream } from "node:fs";

const protocol = createWriteStream(null, { fd: 3, autoClose: false });
let calls = 0;
let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  while (buffer.includes("\n")) {
    const end = buffer.indexOf("\n");
    const request = JSON.parse(buffer.slice(0, end));
    buffer = buffer.slice(end + 1);
    calls += 1;
    const response = `${JSON.stringify({
      schema_version: "1.0", type: "response", request_id: request.request_id, trace_id: request.trace_id,
      operation: request.operation, ok: true, completed_at: "2026-07-26T08:00:00Z", resource: null,
      data: { schema_version: "1.0", schema_bundle_digest: `sha256:${"0".repeat(64)}`,
        runtime_pid: process.pid,
        runtime_argv_digest: `sha256:${"0".repeat(64)}`,
        capabilities: ["system.handshake"], limits: { max_protocol_frame_bytes: 1, max_tool_result_bytes: 1 } }, error: null,
    })}\n`;
    if (calls === 1) setTimeout(() => protocol.write(response), 50);
    else protocol.write(response);
  }
});
