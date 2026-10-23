/**
 * Pi's OpenAI-compatible adapter does not expose a transport hook. Install a
 * process-local guard while the Host is alive so provider requests cannot
 * follow a redirect or leave the approved origin.
 */
export function installProviderFetchGuard(approvedOrigin: string): () => void {
  const original = globalThis.fetch;
  globalThis.fetch = guardedFetch(original, approvedOrigin);
  return () => { globalThis.fetch = original; };
}

export function guardedFetch(original: typeof fetch, approvedOrigin: string): typeof fetch {
  return async (input, init) => {
    const url = requestUrl(input);
    if (url.origin !== approvedOrigin) throw new Error("provider request origin is not approved");
    return original(input, { ...init, redirect: "error" });
  };
}

function requestUrl(input: RequestInfo | URL): URL {
  if (typeof input === "string") return new URL(input);
  if (input instanceof URL) return input;
  return new URL(input.url);
}
