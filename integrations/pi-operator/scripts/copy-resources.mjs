import { cp, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const destination = resolve(packageRoot, "dist", "resources");
await mkdir(destination, { recursive: true });
await cp(resolve(packageRoot, "resources"), destination, { recursive: true });
await cp(resolve(packageRoot, "scripts", "p3-process-watcher.ps1"), resolve(packageRoot, "dist", "scripts", "p3-process-watcher.ps1"));
