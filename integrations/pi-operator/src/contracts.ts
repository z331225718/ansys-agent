import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";

import { Ajv2020 } from "ajv/dist/2020.js";

const require = createRequire(import.meta.url);
const addFormats = require("ajv-formats").default as (ajv: Ajv2020) => void;

const envelopeSchemaId = "urn:aedt-agent:operator:v1:envelope";
const operationSchemaIds: Record<string, string> = {
  "system.handshake": "urn:aedt-agent:operator:v1:operation:system.handshake",
  "template.catalog": "urn:aedt-agent:operator:v1:operation:template.catalog",
  "system.preflight": "urn:aedt-agent:operator:v1:operation:system.preflight",
  "graph.status": "urn:aedt-agent:operator:v1:operation:graph.status",
  "replay.delta": "urn:aedt-agent:operator:v1:operation:replay.delta",
  "graph.visualize": "urn:aedt-agent:operator:v1:operation:graph.visualize",
  "artifact.query_bounded": "urn:aedt-agent:operator:v1:operation:artifact.query_bounded",
  "approval.get_pending": "urn:aedt-agent:operator:v1:operation:approval.get_pending",
  "approval.prepare_decision": "urn:aedt-agent:operator:v1:operation:approval.prepare_decision",
  "mission.start": "urn:aedt-agent:operator:v1:operation:mission.start",
  "graph.advance": "urn:aedt-agent:operator:v1:operation:graph.advance",
  "intervention.apply": "urn:aedt-agent:operator:v1:operation:intervention.apply",
  "graph.stop.prepare": "urn:aedt-agent:operator:v1:operation:graph.stop.prepare",
};

export type ContractValidation = { ok: true } | { ok: false; message: string };

export class ContractValidator {
  readonly schemaBundleDigest: string;
  private readonly ajv: Ajv2020;

  constructor(schemaRoot: string) {
    const manifest = readJson(resolve(schemaRoot, "bundle-manifest.json"));
    this.schemaBundleDigest = String(manifest.bundle_digest);
    this.ajv = new Ajv2020({ allErrors: false, strict: true });
    addFormats(this.ajv);
    for (const relativePath of manifest.schemas as string[]) {
      this.ajv.addSchema(readJson(resolve(schemaRoot, relativePath)));
    }
  }

  validateEnvelope(value: unknown): ContractValidation {
    return this.validate(envelopeSchemaId, value);
  }

  validateRequest(value: unknown): ContractValidation {
    const envelope = this.validateEnvelope(value);
    if (!envelope.ok) {
      return envelope;
    }
    if (!isRequest(value)) {
      return { ok: false, message: "expected a request envelope" };
    }
    const schemaId = operationSchemaIds[value.operation];
    if (!schemaId) {
      return { ok: false, message: `unsupported operation: ${value.operation}` };
    }
    return this.validate(schemaId, value.arguments);
  }

  validateSuccessData(operation: string, value: unknown): ContractValidation {
    const schemaId = operationSchemaIds[operation];
    if (!schemaId) return { ok: false, message: `unsupported operation: ${operation}` };
    return this.validate(`${schemaId}#successData`, value);
  }

  validateTerminalResponse(value: unknown): ContractValidation {
    const envelope = this.validateEnvelope(value);
    if (!envelope.ok) return envelope;
    if (!isResponse(value)) return { ok: false, message: "expected a response envelope" };
    if (value.ok !== true) return { ok: true };
    return this.validateSuccessData(value.operation, value.data);
  }

  private validate(schemaId: string, value: unknown): ContractValidation {
    const validator = this.ajv.getSchema(schemaId);
    if (!validator) {
      throw new Error(`missing registered schema: ${schemaId}`);
    }
    if (validator(value)) {
      return { ok: true };
    }
    return { ok: false, message: this.ajv.errorsText(validator.errors, { separator: "; " }) };
  }
}

function readJson(path: string): Record<string, unknown> {
  return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}

function isRequest(value: unknown): value is { type: "request"; operation: string; arguments: unknown } {
  return typeof value === "object" && value !== null && (value as { type?: unknown }).type === "request"
    && typeof (value as { operation?: unknown }).operation === "string";
}

function isResponse(value: unknown): value is { type: "response"; operation: string; ok: boolean; data: unknown } {
  return typeof value === "object" && value !== null && (value as { type?: unknown }).type === "response"
    && typeof (value as { operation?: unknown }).operation === "string"
    && typeof (value as { ok?: unknown }).ok === "boolean";
}
