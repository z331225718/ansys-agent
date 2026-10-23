import assert from "node:assert/strict";
import { test } from "node:test";

import { shouldExitAfterInputFailure } from "../src/input-lifecycle.js";

test("only EOF or a signal makes an input failure a controlled observer shutdown", () => {
  assert.equal(shouldExitAfterInputFailure(false, "eof", false), true);
  assert.equal(shouldExitAfterInputFailure(false, "open", true), true);
  assert.equal(shouldExitAfterInputFailure(true, "open", false), true);
  assert.equal(shouldExitAfterInputFailure(false, "errored", false), false);
  assert.equal(shouldExitAfterInputFailure(false, "open", false), false);
});
