# P3 Exit Evidence

P3 remains observer-only. This checklist is the release record; a checked automated item does not authorize R2/R3.

## Deterministic Evidence

| Evidence | Status | Command or record |
|---|---|---|
| TypeScript read-only gate | passed locally | `npm test` reports 44 passing tests, including the 10-scenario gate, two-process Session resume, EOF lifecycle, provider profile allowlist/digest, redirect-denial, and trusted stale lease/maintenance-marker recovery coverage |
| TypeScript type check | passed locally | `npm run lint` |
| Python operator API and Bridge | passed locally | `.venv\\Scripts\\python.exe -m pytest tests/operator_api tests/operator_bridge -q` reports 48 passed, including every R2 rejection and production-entry R3 fail-closed probe |
| Python dependency lock | passed locally | `uv lock --check` after adding the `operator` extra; `uv.lock` now records `jsonschema==4.26.0` in `provides-extras` and metadata |
| SBOM generation | passed locally | `npm run sbom`; retain its CycloneDX output with the release record |
| Recorded fixture | passed locally | `p3-recorded-manifest.json` inventory digest, per-call structured expectations, and SQLite sidecar hashes |
| Policy provenance | passed locally | production Host, gate, and restart probe all load `resources/observer-skill.md`; the same SHA-256 digest binds the Session and every telemetry event |

## Release-Blocking Evidence

- [x] Local DeepSeek provider smoke (2026-07-31): the production CLI bound Session `deepseek-smoke-005` to graph `abd4121b-d31a-481a-925e-f4038a1addc7`, invoked only `operator_graph_status`, reported `running`, exited `0` on stdin EOF, and preserved the SQLite SHA-256 hash. The telemetry sequence was `host_started -> tool_call -> tool_result -> turn_settled -> host_stopped(stop_reason=stdin_eof)` and included only provider-profile metadata; no prompt, key, or tool payload was retained. The same smoke passed after the Host's exact-origin and redirect-denial transport guard was installed. This is local validation only, not the approved-workbench release record.
- [x] 公司 workbench/provider smoke：不适用。部署范围已明确固定为可达的 DeepSeek 官方 API，且模型固定为 `deepseek-v4-flash`；修改 runtime adapter 后必须重跑本机真实 smoke，保留 telemetry metadata，不保留 prompt 或 tool payload。
- [ ] Send `SIGINT` during an active streamed turn. Confirm `abort`, Bridge close, telemetry `host_stopped`, and lease release; then resume the same Session in a fresh process. Windows implementation must use the reviewed console broker design in [`p3-windows-signal-gate-design.md`](./p3-windows-signal-gate-design.md), not Node signal emulation, EOF, SIGTERM, or `Stop-Process`.
- [ ] Force-kill a Host after a settled turn, perform the documented trusted stale-lease cleanup, and prove the next process rehydrates the bound graph. The cleanup command now checks exact token/session, dead PID, and an unchanged lock before deletion; automatic stale-lock takeover is intentionally forbidden. Repeat with an uncommitted transcript and record the expected fail-closed recovery result.
- [ ] Run `gate:p3-windows-evidence` on Windows. Its watcher must subscribe to `Win32_ProcessStartTrace` and `Win32_ProcessStopTrace` before Bridge launch, emit a ready record, pass short-lived-child canaries, bind the protocol-serving Python PID returned by `system.handshake`, and validate a reviewed, identity-bound Bridge process-tree manifest. The local venv topology observed on 2026-08-02 is launcher Python -> one base-runtime Python plus one `conhost.exe`; it is diagnostic evidence only, not an allowlist. The final gate must reject extra nodes, grandchildren, missing/ambiguous lifetimes, identity mismatch, incomplete drain, and every surviving tree PID. WMI failure, overflow, missing ready/stop event, or non-Windows execution is a failure, never a skip.
- [x] Capture rejection for all R2 requests and schema-boundary rejection for R3 reserved operation names; preserve the exact read-only handshake capability list.
- [ ] Build a signed dedicated P3 operator offline bundle (the existing Desktop ZIP lacks `integrations/pi-operator`, `schemas/operator/v1`, Node cache, and P3 fixture), then install it on a clean network-disabled Windows workstation. Attach lockfile/SBOM/artifact hashes and the complete readonly smoke result.

P3 may exit only when every release-blocking item is evidenced and independently reviewed. P3.6 remains a separate write-foundation gate.
