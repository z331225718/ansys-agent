const MAX_PROTOCOL_FRAME_BYTES = 1024 * 1024;

export class StrictNdjsonFramer {
  private remainder = Buffer.alloc(0);

  push(chunk: Buffer): Buffer[] {
    const firstNewline = chunk.indexOf(0x0a);
    const firstFrameBytes = this.remainder.length + (firstNewline < 0 ? chunk.length : firstNewline + 1);
    if (firstFrameBytes > MAX_PROTOCOL_FRAME_BYTES) {
      throw new Error("protocol frame exceeds the maximum size");
    }
    this.remainder = Buffer.concat([this.remainder, chunk]);
    if (this.remainder.length > MAX_PROTOCOL_FRAME_BYTES && !this.remainder.includes(0x0a)) {
      throw new Error("protocol frame exceeds the maximum size");
    }
    const frames: Buffer[] = [];
    while (true) {
      const end = this.remainder.indexOf(0x0a);
      if (end < 0) return frames;
      const frame = this.remainder.subarray(0, end + 1);
      this.remainder = this.remainder.subarray(end + 1);
      if (frame.length > MAX_PROTOCOL_FRAME_BYTES || frame.length === 1 || frame.includes(0x0d)) {
        throw new Error("invalid NDJSON response frame");
      }
      frames.push(frame);
    }
  }

  finish(): void {
    if (this.remainder.length !== 0) throw new Error("truncated NDJSON response frame");
  }
}

export function parseStrictJsonFrame(frame: Buffer): Record<string, unknown> {
  const text = frame.subarray(0, -1).toString("utf8");
  if (text.includes("\ufffd")) throw new Error("response is not valid UTF-8");
  assertNoDuplicateObjectKeys(text);
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("response is not valid JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("response must be a JSON object");
  }
  return value as Record<string, unknown>;
}

function assertNoDuplicateObjectKeys(source: string): void {
  let position = 0;
  const skipWhitespace = () => {
    while (/\s/.test(source[position] ?? "")) position += 1;
  };
  const parseString = (): string => {
    const start = position;
    if (source[position] !== '"') throw new Error("invalid JSON string");
    position += 1;
    while (position < source.length) {
      const character = source[position];
      if (character === '"') {
        position += 1;
        return JSON.parse(source.slice(start, position)) as string;
      }
      if (character === "\\") {
        position += 1;
        if (source[position] === "u") position += 4;
      }
      position += 1;
    }
    throw new Error("unterminated JSON string");
  };
  const parseValue = (): void => {
    skipWhitespace();
    const character = source[position];
    if (character === "{") {
      position += 1;
      skipWhitespace();
      const keys = new Set<string>();
      if (source[position] === "}") {
        position += 1;
        return;
      }
      while (true) {
        skipWhitespace();
        const key = parseString();
        if (keys.has(key)) throw new Error(`duplicate JSON key: ${key}`);
        keys.add(key);
        skipWhitespace();
        if (source[position] !== ":") throw new Error("invalid JSON object");
        position += 1;
        parseValue();
        skipWhitespace();
        if (source[position] === "}") {
          position += 1;
          return;
        }
        if (source[position] !== ",") throw new Error("invalid JSON object");
        position += 1;
      }
    }
    if (character === "[") {
      position += 1;
      skipWhitespace();
      if (source[position] === "]") {
        position += 1;
        return;
      }
      while (true) {
        parseValue();
        skipWhitespace();
        if (source[position] === "]") {
          position += 1;
          return;
        }
        if (source[position] !== ",") throw new Error("invalid JSON array");
        position += 1;
      }
    }
    if (character === '"') {
      parseString();
      return;
    }
    const remainder = source.slice(position);
    const literal = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(remainder);
    if (!literal) throw new Error("invalid JSON value");
    position += literal[0].length;
  };

  parseValue();
  skipWhitespace();
  if (position !== source.length) throw new Error("trailing JSON content");
}
