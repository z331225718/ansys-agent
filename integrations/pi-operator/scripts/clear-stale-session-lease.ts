import { resolve } from "node:path";

import { clearTrustedStaleOperatorSessionLease, clearTrustedStaleRecoveryMarker } from "../src/session-store.js";

const values = process.argv.slice(2);
if (values.length !== 6 || values[0] !== "--session-dir" || values[2] !== "--session-id" || !["--lease-token", "--recovery-marker-token"].includes(values[4]!)) {
  throw new Error("usage: --session-dir <dir> --session-id <id> (--lease-token|--recovery-marker-token) <exact-token>");
}
if (values[4] === "--lease-token") await clearTrustedStaleOperatorSessionLease(resolve(values[1]!), values[3]!, values[5]!);
else await clearTrustedStaleRecoveryMarker(resolve(values[1]!), values[3]!, values[5]!);
process.stdout.write(`${JSON.stringify({ status: "cleared", session_id: values[3] })}\n`);
