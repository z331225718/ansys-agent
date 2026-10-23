import { appendFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";

export type ObserverTelemetryEvent = {
  event: "host_started" | "tool_call" | "tool_result" | "turn_settled" | "host_stopped";
  session_id: string;
  graph_run_id: string;
  policy_digest: string;
  operation?: string;
  is_error?: boolean;
  stop_reason?: "stdin_eof" | "user_exit" | "signal" | "error";
  provider_profile_digest?: string;
  provider_adapter?: "deepseek";
  provider_model?: string;
  provider_origin_digest?: string;
};

/** Append-only audit records intentionally exclude prompts, tool arguments, and bridge payloads. */
export class ObserverTelemetry {
  private pending = Promise.resolve();
  private closed = false;

  public constructor(private readonly file: string) {}

  public record(event: ObserverTelemetryEvent): Promise<void> {
    if (this.closed) return Promise.reject(new Error("observer telemetry is closed"));
    const line = JSON.stringify({ ...event, recorded_at: new Date().toISOString() }) + "\n";
    this.pending = this.pending.then(async () => {
      await mkdir(dirname(resolve(this.file)), { recursive: true });
      await appendFile(this.file, line, { encoding: "utf8", mode: 0o600 });
    });
    return this.pending;
  }

  public async close(): Promise<void> {
    this.closed = true;
    await this.pending;
  }
}
