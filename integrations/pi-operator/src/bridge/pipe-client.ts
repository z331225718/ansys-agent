import { randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import type { Readable, Writable } from "node:stream";

import { ContractValidator } from "../contracts.js";
import { BridgeClientError } from "./errors.js";
import { parseStrictJsonFrame, StrictNdjsonFramer } from "./framing.js";

type PendingCall = {
  operation: string;
  resolve: (value: Record<string, unknown>) => void;
  reject: (error: Error) => void;
  timeout: NodeJS.Timeout;
};
type ExpiredCall = Pick<PendingCall, "operation"> & { expiresAt: number };

export type BridgeClientOptions = {
  command: string;
  args: string[];
  schemaRoot: string;
  timeoutMs?: number;
  onDiagnostic?: (stream: "stdout" | "stderr", text: string) => void;
  bridgeEnv?: NodeJS.ProcessEnv;
};

export class BridgePipeClient {
  private readonly validator: ContractValidator;
  private readonly timeoutMs: number;
  private readonly pending = new Map<string, PendingCall>();
  private readonly expired = new Map<string, ExpiredCall>();
  private framer = new StrictNdjsonFramer();
  private process: ChildProcess | undefined;
  private failed = false;

  constructor(private readonly options: BridgeClientOptions) {
    this.validator = new ContractValidator(options.schemaRoot);
    this.timeoutMs = options.timeoutMs ?? 10_000;
  }

  get schemaBundleDigest(): string {
    return this.validator.schemaBundleDigest;
  }

  /** PID of the sole trusted Bridge child; never expose its stdio or process handle. */
  get processId(): number | undefined {
    return this.process?.pid;
  }

  async start(): Promise<void> {
    if (this.process && !this.failed) return;
    if (this.process || this.failed) await this.restart();
    const process = spawn(this.options.command, this.options.args, {
      stdio: ["pipe", "pipe", "pipe", "pipe"],
      windowsHide: true,
      env: this.options.bridgeEnv ?? sanitizedBridgeEnvironment(),
    });
    const responsePipe = process.stdio[3] as Readable | null;
    if (!process.stdin || !responsePipe) {
      process.kill();
      throw new BridgeClientError("protocol_error", "bridge did not expose a dedicated response pipe");
    }
    this.process = process;
    responsePipe.on("data", (chunk: Buffer) => this.onResponseData(chunk));
    responsePipe.on("end", () => this.onResponseEnd());
    responsePipe.on("error", (error) => this.fail(new BridgeClientError("protocol_error", error.message)));
    this.drainDiagnostic(process.stdout, "stdout");
    this.drainDiagnostic(process.stderr, "stderr");
    process.on("error", (error) => {
      if (this.process === process) this.fail(new BridgeClientError("bridge_exit", error.message));
    });
    process.on("exit", (code, signal) => {
      if (this.process === process && !this.failed) {
        this.fail(new BridgeClientError("bridge_exit", `bridge exited (${code ?? signal ?? "unknown"})`));
      }
    });
  }

  async call(
    operation: string,
    argumentsValue: Record<string, unknown>,
    caller?: { pi_session_id?: string; pi_tool_call_id?: string },
  ): Promise<Record<string, unknown>> {
    await this.start();
    if (this.failed || !this.process?.stdin) throw new BridgeClientError("bridge_exit", "bridge is unavailable");
    const requestId = randomUUID();
    const request = {
      schema_version: "1.0",
      type: "request",
      request_id: requestId,
      trace_id: randomUUID(),
      operation,
      sent_at: new Date().toISOString(),
      ...(caller ? { caller } : {}),
      arguments: argumentsValue,
    };
    const validation = this.validator.validateRequest(request);
    if (!validation.ok) throw new BridgeClientError("protocol_error", `invalid client request: ${validation.message}`);
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const timeout = setTimeout(() => this.expire(requestId), this.timeoutMs);
      this.pending.set(requestId, { operation, resolve, reject, timeout });
      (this.process!.stdin as Writable).write(`${JSON.stringify(request)}\n`, (error) => {
        if (error) this.fail(new BridgeClientError("bridge_exit", error.message));
      });
    });
  }

  async close(): Promise<void> {
    const process = this.process;
    this.process = undefined;
    if (!process || process.exitCode !== null) return;
    await new Promise<void>((resolve) => {
      process.once("exit", () => resolve());
      process.stdin?.end();
      setTimeout(() => {
        process.kill();
        resolve();
      }, 1_000).unref();
    });
    this.failed = false;
    this.framer = new StrictNdjsonFramer();
    this.expired.clear();
  }

  private async restart(): Promise<void> {
    const process = this.process;
    this.process = undefined;
    if (process && process.exitCode === null) {
      process.stdin?.end();
      process.kill();
    }
    this.failed = false;
    this.framer = new StrictNdjsonFramer();
    this.expired.clear();
  }

  private onResponseData(chunk: Buffer): void {
    try {
      for (const frame of this.framer.push(chunk)) this.route(parseStrictJsonFrame(frame));
    } catch (error) {
      this.fail(new BridgeClientError("protocol_error", error instanceof Error ? error.message : "invalid response"));
    }
  }

  private onResponseEnd(): void {
    try {
      this.framer.finish();
      if (!this.failed) this.fail(new BridgeClientError("bridge_exit", "response pipe closed"));
    } catch (error) {
      this.fail(new BridgeClientError("protocol_error", error instanceof Error ? error.message : "truncated response"));
    }
  }

  private route(message: Record<string, unknown>): void {
    const validation = this.validator.validateEnvelope(message);
    if (!validation.ok) throw new Error(`invalid response envelope: ${validation.message}`);
    const requestId = message.request_id;
    if (typeof requestId !== "string") throw new Error("response has no request id");
    const pending = this.pending.get(requestId);
    if (!pending) {
      const expired = this.expired.get(requestId);
      if (!expired || expired.expiresAt < Date.now() || message.operation !== expired.operation || message.type !== "response") {
        throw new Error(`response has unknown request id: ${requestId}`);
      }
      const terminal = this.validator.validateTerminalResponse(message);
      if (!terminal.ok) throw new Error(`invalid late terminal response: ${terminal.message}`);
      this.expired.delete(requestId);
      return;
    }
    if (message.operation !== pending.operation) throw new Error("response operation does not match request");
    if (message.type === "event") return;
    if (message.type !== "response") throw new Error("response pipe received a non-terminal message");
    const terminal = this.validator.validateTerminalResponse(message);
    if (!terminal.ok) throw new Error(`invalid terminal response: ${terminal.message}`);
    clearTimeout(pending.timeout);
    this.pending.delete(requestId);
    if (message.ok !== true) {
      const error = message.error as { code?: unknown; message?: unknown } | null;
      const code = typeof error?.code === "string" ? error.code : "protocol_error";
      const detail = typeof error?.message === "string" ? error.message : "bridge rejected request";
      pending.reject(new BridgeClientError(code, detail));
      return;
    }
    pending.resolve(message);
  }

  private expire(requestId: string): void {
    const pending = this.pending.get(requestId);
    if (!pending) return;
    this.pending.delete(requestId);
    this.expired.set(requestId, { operation: pending.operation, expiresAt: Date.now() + Math.max(this.timeoutMs * 2, 60_000) });
    pending.reject(new BridgeClientError("timeout", `bridge request timed out: ${pending.operation}`));
  }

  private drainDiagnostic(stream: Readable | null, name: "stdout" | "stderr"): void {
    stream?.on("data", (chunk: Buffer) => this.options.onDiagnostic?.(name, chunk.toString("utf8").slice(0, 4096)));
  }

  private fail(error: BridgeClientError): void {
    if (this.failed) return;
    this.failed = true;
    for (const [requestId, pending] of this.pending) {
      clearTimeout(pending.timeout);
      this.pending.delete(requestId);
      const code = isWriteOperation(pending.operation) ? "outcome_unknown" : error.code;
      pending.reject(new BridgeClientError(code === "outcome_unknown" ? code : error.code, error.message));
    }
  }
}

function isWriteOperation(operation: string): boolean {
  return ["mission.start", "graph.advance", "intervention.apply", "graph.stop.prepare"].includes(operation);
}

function sanitizedBridgeEnvironment(): NodeJS.ProcessEnv {
  const names = ["PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "PYTHONPATH"];
  return Object.fromEntries(names.flatMap((name) => process.env[name] === undefined ? [] : [[name, process.env[name]]])) as NodeJS.ProcessEnv;
}
