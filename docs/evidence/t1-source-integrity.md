# T1 source-integrity evidence

- Date: 2026-10-24
- Branch: feature/reviewed-brd-dsh-loop
- T1 commit: 38335ef8
- Baseline commit: 000ac965 (T0) + 15065d73 (workbench merge)

## Offline baseline

- 1432 passed, 31 skipped (T0, excluding tests/test_offline_release_scripts.py)
- 1443 passed, 31 skipped (after T1, same exclusion)

## T1 guarantees

- source_project_path, working_project_path, run_root, report_dir are explicit.
- working differs from source and is contained by run_root; report_dir is contained by run_root.
- source must be an existing .aedt file.
- path comparison uses resolve and os.path.normcase; working is rejected for case and relative aliases.
- source SHA256 and size are recorded in loop_context at prepare.
- final report worker re-hashes source and raises non-retryable WorkerReportedError on mismatch or missing baseline.
- diagnosis artifact paths are carried in WorkerReportedError.details.
- fail_optimization keeps structured FAILED output, original artifact refs, and source_integrity status.
- graph_run_id resume validation no longer returns early; safety checks still run.

## Known boundaries

- source .aedb full bundle digest is not recomputed at report time; only source .aedt SHA256 is verified.
- no real AEDT solve was executed in T1.
