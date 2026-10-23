import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const schemaRoot = resolve(packageRoot, "..", "..", "schemas", "operator", "v1");
const outputPath = resolve(packageRoot, "src", "generated", "operator-v1.ts");
const manifest = await readJson(resolve(schemaRoot, "bundle-manifest.json"));
const commonSchema = await readJson(resolve(schemaRoot, "common.schema.json"));
const envelopeSchema = await readJson(resolve(schemaRoot, "envelope.schema.json"));
const operationSchemas = new Map();

for (const relativePath of manifest.schemas) {
  if (relativePath === "common.schema.json" || relativePath === "envelope.schema.json") {
    continue;
  }
  operationSchemas.set(relativePath, await readJson(resolve(schemaRoot, relativePath)));
}

const lines = [
  "/* This file is generated from schemas/operator/v1. Do not edit manually. */",
  'import { Type, type TSchema } from "typebox";',
  "",
  `export type OperatorEnvelope = ${schemaToType(envelopeSchema, envelopeSchema)};`,
  "",
];
const entries = [];
const modelSchemas = [];

for (const [relativePath, schema] of operationSchemas) {
  const operation = operationName(relativePath);
  const typeName = `${pascalCase(operation)}Arguments`;
  const successTypeName = `${pascalCase(operation)}SuccessData`;
  lines.push(`export type ${typeName} = ${schemaToType(schema, schema)};`, "");
  lines.push(`export type ${successTypeName} = ${schemaToType(schema.$defs?.successData ?? {}, schema)};`, "");
  entries.push([operation, typeName, successTypeName]);
  modelSchemas.push([operation, modelVisibleSchema(schema)]);
}

lines.push("export interface OperationArgumentsByName {");
for (const [operation, typeName] of entries) {
  lines.push(`  ${JSON.stringify(operation)}: ${typeName};`);
}
lines.push("}", "", "export type OperatorOperation = keyof OperationArgumentsByName;");
lines.push("export type ArgumentsFor<T extends OperatorOperation> = OperationArgumentsByName[T];", "");
lines.push("export interface OperationSuccessDataByName {");
for (const [operation, , successTypeName] of entries) {
  lines.push(`  ${JSON.stringify(operation)}: ${successTypeName};`);
}
lines.push("}", "", "export type SuccessDataFor<T extends OperatorOperation> = OperationSuccessDataByName[T];", "");
lines.push("const modelToolSchemas: Record<OperatorOperation, Record<string, unknown>> = {");
for (const [operation, schema] of modelSchemas) {
  lines.push(`  ${JSON.stringify(operation)}: ${JSON.stringify(schema)},`);
}
lines.push("};", "");
lines.push("export function modelToolSchemaFor<T extends OperatorOperation>(operation: T): TSchema {");
lines.push("  return Type.Unsafe<ArgumentsFor<T>>(structuredClone(modelToolSchemas[operation]));");
lines.push("}", "");

await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${lines.join("\n")}\n`, "utf8");

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

function operationName(relativePath) {
  return relativePath.replace(/^operations\//, "").replace(/\.schema\.json$/, "");
}

function pascalCase(value) {
  return value.replace(/[^A-Za-z0-9]+(.)/g, (_, character) => character.toUpperCase())
    .replace(/^./, (character) => character.toUpperCase());
}

function schemaToType(schema, document) {
  if (typeof schema.$ref === "string") {
    const resolved = resolveReference(schema.$ref, document);
    return schemaToType(resolved.schema, resolved.document);
  }
  if (Object.hasOwn(schema, "const")) {
    return JSON.stringify(schema.const);
  }
  if (Array.isArray(schema.enum)) {
    return schema.enum.map((item) => JSON.stringify(item)).join(" | ");
  }
  if (Array.isArray(schema.oneOf)) {
    return schema.oneOf.map((item) => schemaToType(item, document)).join(" | ");
  }
  if (Array.isArray(schema.anyOf) && !schema.properties) {
    return schema.anyOf.map((item) => schemaToType(item, document)).join(" | ");
  }
  if (schema.type === "array") {
    return `Array<${schemaToType(schema.items ?? {}, document)}>`;
  }
  if (schema.type === "object" || schema.properties) {
    const required = new Set(schema.required ?? []);
    const properties = Object.entries(schema.properties ?? {}).map(([name, propertySchema]) => {
      const optional = required.has(name) ? "" : "?";
      return `${JSON.stringify(name)}${optional}: ${schemaToType(propertySchema, document)};`;
    });
    if (schema.additionalProperties !== false) {
      properties.push("[key: string]: unknown;");
    }
    return `{ ${properties.join(" ")} }`;
  }
  if (schema.type === "string") {
    return "string";
  }
  if (schema.type === "integer" || schema.type === "number") {
    return "number";
  }
  if (schema.type === "boolean") {
    return "boolean";
  }
  if (schema.type === "null") {
    return "null";
  }
  return "unknown";
}

function modelVisibleSchema(schema) {
  const resolved = resolveSchema(schema, schema);
  if (schema.$id?.endsWith(":system.handshake")) {
    return { type: "object", additionalProperties: false, properties: {} };
  }
  return resolved;
}

function resolveSchema(schema, document) {
  if (Array.isArray(schema)) return schema.map((item) => resolveSchema(item, document));
  if (!schema || typeof schema !== "object") return schema;
  if (typeof schema.$ref === "string") {
    const resolved = resolveReference(schema.$ref, document);
    return resolveSchema(resolved.schema, resolved.document);
  }
  const result = {};
  for (const [key, value] of Object.entries(schema)) {
    if (["$schema", "$id", "$defs", "$anchor", "title"].includes(key)) continue;
    result[key] = resolveSchema(value, document);
  }
  return result;
}

function resolveReference(reference, document) {
  const commonPrefix = "urn:aedt-agent:operator:v1:common#/$defs/";
  if (reference.startsWith(commonPrefix)) {
    return {
      schema: commonSchema.$defs[reference.slice(commonPrefix.length)],
      document: commonSchema,
    };
  }
  if (reference.startsWith("#/$defs/")) {
    return {
      schema: document.$defs[reference.slice("#/$defs/".length)],
      document,
    };
  }
  if (reference === "#successData") {
    return { schema: document.$defs.successData, document };
  }
  throw new Error(`unsupported schema reference: ${reference}`);
}
