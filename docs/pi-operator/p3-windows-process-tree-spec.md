# P3 Windows Process-Tree Gate

## Purpose

The Windows evidence gate audits the Bridge launch tree while P3 remains read-only. It does not provide OS isolation and does not grant any R2 or R3 capability.

## Observed Baseline

The local Python virtual environment launches a short-lived launcher process. The launcher creates two direct children: the Python process that serves the Bridge protocol and a Windows console host. Evidence run `interactive-005` bound the inner Python PID to `system.handshake.runtime_pid` and observed unique stop events for all three PIDs.

This baseline is not a runtime allowlist. The final gate uses only a reviewed manifest created for the deployment image.

## Reviewed Manifest Contract

The deployment manifest is versioned and digest-bound. It contains no command line, absolute path, environment value, user identity, database payload, or prompt. It pins:

- launcher role: configured Bridge executable image SHA-256;
- runtime role: expected base Python image SHA-256 and canonical Bridge argument digest;
- console role: approved system console-host image SHA-256;
- topology: one launcher, exactly one runtime child, zero or one console child, and no grandchildren.

The gate obtains `runtime_pid` from the schema-validated handshake, queries live process identity while that PID remains alive, and compares only hashes and role metadata with the manifest. A manifest is never learned from a failing run or updated automatically after a Python or Windows update.

## Pass Conditions

1. Both WMI subscriptions are ready before Bridge launch; lead and tail canaries complete.
2. Launcher and runtime starts are unique, have the expected parent relation, and have unambiguous PID lifetimes.
3. Every observed tree node conforms to the reviewed role, image digest, argument digest, depth, and cardinality constraints.
4. Closing the client yields one stop record per tree node; after watcher drain, every node is absent from CIM.
5. The gate emits `status: "passed"` and exits zero. The same evidence passes three consecutive independent executions.

## Fail-Closed Cases

Missing WMI start/stop, PID reuse ambiguity, an unknown process, an unexpected second runtime, a grandchild, a mismatched digest, an unavailable identity query, watcher overflow, partial NDJSON, or an uncleared process all produce nonzero exit and `status: "failed"` evidence.
