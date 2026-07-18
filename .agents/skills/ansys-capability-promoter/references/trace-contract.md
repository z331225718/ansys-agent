# Trace Contract

A promotable trace must satisfy every condition:

- It is retrieved by trace ID from the configured `CapabilityTraceStore`; an arbitrary JSON object is never input.
- `sealed` is `true`, the terminal state and terminal event are `verified`, the content digest matches, and the store verifies the server-held HMAC authentication.
- Its events show proposal, validation, preview, apply, and successful readback. A reversible edit also records approval before apply.
- The store has redacted credentials, approval tokens, session keys, passwords, and environment secrets.

Reject traces ending in `failed`, `rejected`, `expired`, `rolled_back`, or `rollback_failed`. A successful rollback proves safety behavior, not the intended capability.

The public promoter accepts only a trace ID from its configured default store. It does not accept arbitrary trace JSON or a caller-selected trace root. Candidate artifacts may retain the trace ID and seal digest for audit. They must omit trace-specific project/design identities, object paths, operation values, and local source paths.
