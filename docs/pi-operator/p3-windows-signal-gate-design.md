# P3 Windows Signal Gate Design

This gate is release evidence for the observer Host only. It neither starts a Mission nor authorizes R2/R3.

## Required Signal Path

Windows must not use Node `child.kill("SIGINT")`, stdin EOF, `SIGTERM`, or `Stop-Process` as a substitute for Ctrl+C.
`CTRL_C_EVENT` cannot be directed at a nonzero process group, and a new process group disables Ctrl+C.

The checked-in broker must therefore own Host creation and signal delivery:

1. PowerShell 5.1 `Add-Type` broker calls `CreateProcessW` for the real `node.exe dist/src/main.js` Host.
2. It uses only `CREATE_NEW_CONSOLE | CREATE_UNICODE_ENVIRONMENT`, with inherited redirected stdio and optional hidden window.
3. It must not use `CREATE_NEW_PROCESS_GROUP`, `DETACHED_PROCESS`, or `CREATE_NO_WINDOW`.
4. After the mock has flushed a streaming delta and the Host has rendered it, the broker performs `FreeConsole`, `AttachConsole(hostPid)`, `SetConsoleCtrlHandler(NULL, TRUE)`, verifies the console PID set, then calls `GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)`.
5. A console with unexpected PIDs, Attach failure, a failed control event, or a broker crash is a failure, never a skip.

The target workstation must permit PowerShell FullLanguage and `Add-Type`. A constrained/AppLocker/WDAC workstation needs the same code as a signed, hash-pinned C# broker instead.

## Broker Contract

The checked-in `p3-console-broker.ps1` must receive only `-RequestFile` and `-ControlDirectory`.
The request contains schema version, canonical absolute `node_exe`, `host_entry`, working directory,
Host argv, and a UUID nonce; provider keys remain process environment only. The control directory must
be pre-created, empty, private, and free of reparse points.

The broker atomically writes `ready.json`, then accepts only a nonce-matching `ctrl-c.json`, and writes
either `signal.json`, `exit.json`, or `failure.json`. `signal.json` records only that
`GenerateConsoleCtrlEvent` returned; it never claims delivery or P3 success. Unknown console PIDs,
stale control files, mismatched nonce, early Host exit, or any Win32 API failure are fail-closed.

Before integrating the real Host, the broker requires a separately labeled native smoke fixture that
records a real `SIGINT` handler invocation and exits naturally. That fixture is implementation evidence,
not P3 exit evidence.

## Evidence Conditions

The gate passes its SIGINT segment only when all conditions hold:

- a local HTTPS OpenAI-compatible SSE mock has flushed a delta and has not ended the response;
- Host stdout received that delta before trigger creation;
- the mock observes peer close caused by the client abort;
- Host exits naturally with code `0` and telemetry contains exactly one `host_stopped` with `stop_reason=signal`;
- exact Host and Python Bridge PID/CreationDate instances are absent after independent polling;
- Session lease and maintenance marker are gone;
- database and all SQLite sidecar hashes are unchanged;
- a fresh Host with the same Session/provider profile reaches `observer>` and completes a read-only `graph.status` observation.

`host_started` and `turn_settled` alone are not recovery proof. The completed recovery turn, tool telemetry, process lifetimes, lease state, and database inventory are all required.

## Evidence Contents

Preserve only bounded metadata: broker source SHA-256, created flags, OS/PowerShell/.NET/Node versions, console PID list, trigger nonce, control API result, process lifetimes, SSE close time, telemetry sequence, lease state, and SQLite hashes. Do not retain prompt text, API keys, Authorization headers, or provider payloads.
