# T3 offline acceptance evidence

- Date: 2026-10-24
- Branch: feature/reviewed-brd-dsh-loop
- Status: offline acceptance passed; real AEDT not started
- Real-AEDT flags: RUN_REAL_LIVE_AEDT=0, RUN_REAL_AEDT=0, RUN_REAL_PYEDB=0, ANSYS_AGENT_RUN_REAL_AEDT=0

## Full suite

~~~text
pytest tests --ignore=tests/test_offline_release_scripts.py
1465 passed, 31 skipped
~~~

The ignored file contains the pre-existing Windows PowerShell 5.1 offline-release
baseline failure documented in docs/evidence/README.md; it is outside M1 T0-T3.

## Config validation

~~~text
mission validate-loop-config --config config\optimization_loops\reviewed_brd_remote.example.json --no-check-paths
status=passed, failed_checks=[]
~~~

The machine-local files `reviewed_brd_remote.json` and
`local_real_aedt.json` are not present in this checkout, so the real production
paths could not be validated. A synthetic fixture with real source/working/report
paths under a temp run_root passes `check_paths=True`
(`test_validate_reviewed_loop_with_machine_paths`). T4 must still run the real
config check on the AEDT machine before any solve.

## Recovery and DB semantics

`tests/test_reviewed_loop_recovery_t3.py` proves:

- a real SQLite graph in waiting_approval keeps graph/node/handoff/approval data
  across a fresh runtime and a fresh AssistantWorkflowManager;
- `AssistantWorkflowManager.status()` is read-only: repeated calls do not change
  step_count, node count, mission count, handoff count, or the working bundle
  content hash;
- the workflow DB comes from `AEDT_AGENT_WORKFLOW_DB` and the factory receives
  that exact path;
- unknown graph_run_id / wrong DB fails with KeyError;
- a preview ID from a previous manager instance cannot be reused by a new one;
- the human gate is approved through the CLI ApprovalService, not through an
  Assistant token or an implicit advance;
- the default Assistant runtime factory loads the exact
  `local_real_aedt` profile (not safe-recorded) from the configured path.

## Report evidence

`tests/test_brd_optimization_report_t3.py` uses a three-round fixture
(pass / fail / failed) and verifies:

- CSV has one row per round with status, action, parameters, SDD values;
- HTML and optimization_progress.json carry the same bounded metrics, Diff1
  observation port, confirmed orientation, objective cost;
- every referenced artifact exists under run_root;
- optimization_artifacts.json stores SHA256 and size for each referenced
  artifact, and the test recomputes each SHA256 independently.

## Known boundaries

- Real AEDT solve, real .aedt/.aedb paths, and real reviewed config are T4.
- Artifact manifest hashes the main .aedt only where it is referenced; it does
  not claim a full .aedb bundle digest.
- The single DSH mode is for engineering work; production Mission lifecycle is
  denied in DSH by production-guard.mjs and stays in CLI/dashboard.
