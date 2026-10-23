import { createWriteStream } from "node:fs";

const protocol = createWriteStream(null, { fd: 3, autoClose: false });
process.stdin.once("data", () => protocol.write("native library noise\n"));
