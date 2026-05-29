# Stage C.5 Recorded Workflow Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the recorded AEDT workflow at `/home/zzmjay/code/aedt/recoard_workflow.py` into structured Stage C.5 evidence and a PyAEDT-first migration map for single-iteration BRD via optimization.

**Architecture:** Treat the recorded script as a reference trace, not production code. Add a parser that extracts key operations, design constants, optimization variables, reports, and raw fallback points. Add a CLI that writes JSON/HTML analysis artifacts. Use PyAEDT/PyEDB wrapper names wherever a stable wrapper exists, and explicitly mark lower-level raw AEDT calls only when no wrapper is identified.

**Tech Stack:** Python 3.12, pathlib, regex, pytest, existing reporting style.

---

## Scope

This plan implements analysis and bridge artifacts only:

- Parse the recorded script.
- Identify key Stage C.5 operations.
- Extract BRD path, AEDB path, project path, selected nets, component, setup/sweep, differential pairs, reports, variable `r_cut_L3`, and ART03 void operations.
- Generate a PyAEDT/PyEDB migration map.
- Generate a Chinese HTML analysis report.

This plan does not:

- Launch AEDT.
- Execute the recorded script.
- Modify BRD/AEDB/AEDT files.
- Run solve.
- Perform optimization iterations.

## Files

- Create `src/aedt_agent/layout/recorded_workflow.py`  
  Parser and migration map builder.

- Create `src/aedt_agent/reporting/recorded_workflow_report.py`  
  Chinese HTML renderer for the recorded workflow analysis.

- Create `scripts/analyze_stage_c_recorded_workflow.py`  
  CLI for JSON/HTML artifacts.

- Create `tests/test_recorded_workflow_bridge.py`  
  Unit and CLI tests using a compact recorded-script fixture.

- Modify `docs/superpowers/specs/2026-05-27-brd-via-optimization-agent-design.md`  
  Add the bridge command and note that recorded `oEditor/oModule` calls are reference/fallback only.

## PyAEDT-First Mapping

Expected migration map:

| Recorded operation | Preferred wrapper | Fallback |
| --- | --- | --- |
| `oTool.ImportExtracta` | `Hfss3dLayout.import_brd()` or existing PyEDB import/cutout path | raw ImportExtracta |
| `oEditor.CutOutSubDesign` | PyEDB `edb.cutout()` used by current BRD workflow | raw CutOutSubDesign |
| `oEditor.CreatePortsOnComponentsByNet` | `Hfss3dLayout.create_ports_on_component_by_nets()` | raw CreatePortsOnComponentsByNet |
| `oEditor.CreateEdgePort` | `Hfss3dLayout.create_edge_port()` | raw CreateEdgePort |
| `oModule.Add` setup | `Hfss3dLayout.create_setup()` | raw SolveSetups.Add |
| `oModule.AddSweep` | `Hfss3dLayout.create_linear_step_sweep()` | raw AddSweep |
| `oDesign.Analyze` | `Hfss3dLayout.analyze()` | raw Analyze |
| `oProject.SaveAs` | `Hfss3dLayout.save_project()` | raw SaveAs |
| `oModule.CreateReport` S/TDR | PyAEDT post/report API if stable in local version | raw ReportSetup.CreateReport |
| `oEditor.CreateCircleVoid/CreateRectangleVoid` | no stable wrapper identified yet | raw void commands isolated behind action schema |
| `oModule.SetDiffPairs` | no stable wrapper identified yet | raw SetDiffPairs isolated behind action schema |

## Task 1: Parser

- [x] Write failing tests for `analyze_recorded_workflow()` against a compact fixture containing ImportExtracta, CutOutSubDesign, CreatePortsOnComponentsByNet, CreateEdgePort, setup/sweep, reports, `r_cut_L3`, voids, SaveAs, and Analyze.
- [x] Implement `analyze_recorded_workflow(path: Path) -> dict[str, Any]`.
- [x] Verify parser tests pass.

## Task 2: Report and CLI

- [x] Write failing tests for `render_recorded_workflow_html()` and `scripts/analyze_stage_c_recorded_workflow.py`.
- [x] Implement the HTML renderer and CLI.
- [x] Verify tests pass.

## Task 3: Docs and Verification

- [x] Add command documentation to the BRD via optimization spec.
- [ ] Run:

```bash
.venv/bin/python -m pytest tests/test_recorded_workflow_bridge.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_contract_stabilization.py
git diff --check
```

- [x] Commit:

```bash
git add \
  src/aedt_agent/layout/recorded_workflow.py \
  src/aedt_agent/reporting/recorded_workflow_report.py \
  scripts/analyze_stage_c_recorded_workflow.py \
  tests/test_recorded_workflow_bridge.py \
  docs/superpowers/specs/2026-05-27-brd-via-optimization-agent-design.md \
  docs/superpowers/plans/2026-05-29-stage-c5-recorded-workflow-bridge.md

git commit -m "feat: analyze recorded Stage C workflow"
```

## Done Criteria

- The recorded workflow can be analyzed without AEDT.
- The output identifies high-level Stage C.5 operations and extracted parameters.
- The migration map prefers PyAEDT/PyEDB wrappers and marks raw fallbacks explicitly.
- The analysis artifacts are suitable for implementing the next real single-iteration runner.
