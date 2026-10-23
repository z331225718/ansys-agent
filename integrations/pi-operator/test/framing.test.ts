import assert from "node:assert/strict";
import { test } from "node:test";

import { parseStrictJsonFrame, StrictNdjsonFramer } from "../src/bridge/framing.js";

test("strict response framer rejects duplicate keys even when JSON.parse would overwrite one", () => {
  assert.throws(
    () => parseStrictJsonFrame(Buffer.from('{"request_id":"one","request_id":"two"}\n', "utf8")),
    /duplicate JSON key/,
  );
});

test("strict response framer rejects an oversized unterminated frame before retaining it", () => {
  const framer = new StrictNdjsonFramer();
  assert.throws(() => framer.push(Buffer.alloc(1024 * 1024 + 1, 0x61)), /maximum size/);
});

test("strict response framer treats escaped and literal equivalent keys as duplicates", () => {
  assert.throws(
    () => parseStrictJsonFrame(Buffer.from('{"a":1,"\\u0061":2}\n', "utf8")),
    /duplicate JSON key/,
  );
});
