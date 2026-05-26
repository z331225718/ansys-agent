# BRD Experimental Workflow Node Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current BRD/MCM import-cutout demo into a clearly bounded experimental workflow track with reusable node logic, explicit evidence artifacts, and a real MCP smoke path.

**Architecture:** Keep Layout/BRD nodes `experimental` and opt-in only. Extract reusable services from `src/aedt_agent/demo/import_cutout.py` without breaking the existing demo, route experimental MCP node execution through those services only where the contract is clear, and add a real-MCP smoke that proves an external agent can execute controlled nodes against AEDT. Stop before solve by default because the target board workflow is too heavy for routine local runs.

**Tech Stack:** Python 3.12, PyAEDT/HFSS 3D Layout, PyEDB, FastMCP kernel layer, YAML node catalog, workflow templates, pytest, existing `aedt_agent.demo`, `aedt_agent.mcp`, `aedt_agent.workflow`, and `aedt_agent.nodes` packages.

---

## Current State

- `workflow_templates/import_brd_cutout_sparam_tdr.json` defines the desired experimental workflow:
  `import_layout_file -> select_layout_nets -> create_layout_cutout -> configure_layout_stackup -> locate_layout_port_candidates -> create_layout_ports -> create_layout_setup`.
- `src/aedt_agent/demo/import_cutout.py` contains the working real pipeline for model build.
- `src/aedt_agent/demo/layout_ports.py` contains the learned port rule:
  BGA/IC endpoint uses component-cylinder ports; connector pin endpoint uses `oEditor.ToggleViaPin(["NAME:elements", "<component>-<pin>"])` and then sets `HFSS Type` to `Gap`.
- `src/aedt_agent/mcp/node_executor.py` still treats Layout/BRD node execution as an experimental placeholder.
- Default catalog and planner now exclude experimental nodes; BRD workflow is only available when experimental nodes are explicitly enabled.

This plan does not add a new demo UI and does not run heavy solve by default.

---

## File Structure

Create:

- `src/aedt_agent/layout/__init__.py`  
  Package marker for reusable Layout/BRD services.

- `src/aedt_agent/layout/models.py`  
  Dataclasses for import/cutout request, net selection, candidate report, port action plan, and model-build summary.

- `src/aedt_agent/layout/import_cutout.py`  
  Reusable core operations extracted from demo code: request normalization, layout discovery, net matching, PyEDB cutout, stackup import, port candidate discovery, setup creation.

- `src/aedt_agent/layout/ports.py`  
  Reusable port planning and application functions moved from `demo/layout_ports.py`.

- `scripts/run_mcp_real_smoke.py`  
  Small script that uses `create_kernel(adapter="real")` and executes a minimal controlled HFSS node sequence through the MCP kernel path.

- `docs/brd-experimental-workflow.md`  
  Chinese technical note explaining current BRD experimental node boundaries, artifacts, and known limits.

Modify:

- `src/aedt_agent/demo/import_cutout.py`  
  Keep public demo API stable, but delegate reusable logic to `aedt_agent.layout`.

- `src/aedt_agent/demo/layout_ports.py`  
  Leave compatibility imports or thin wrappers so existing tests and demo code do not break.

- `src/aedt_agent/mcp/node_executor.py`  
  Route experimental Layout/BRD node execution to reusable services when enough state is available; keep clear experimental postcheck metadata.

- `src/aedt_agent/mcp/node_schemas.py`  
  Add missing input/output contract fields needed for real experimental Layout/BRD node execution.

- `nodes/catalog/*.yaml` for Layout/BRD nodes  
  Tighten summaries, input fields, output fields, and known limits.

- `workflow_templates/import_brd_cutout_sparam_tdr.json`  
  Keep template model-build only; add metadata that solve/postprocess are future steps.

- `tests/test_import_cutout_demo.py`  
  Ensure existing demo behavior remains stable after extraction.

- `tests/test_layout_port_candidates.py`  
  Move or mirror tests against the new reusable layout package.

- `tests/test_node_executor.py`  
  Add tests for experimental node execution using fake/reusable state.

- `tests/test_mcp_real_kernel_config.py` or new `tests/test_mcp_real_smoke_script.py`  
  Add coverage for the real MCP smoke script command construction without launching AEDT.

---

## Task 1: Document BRD Experimental Node Contract

**Files:**
- Create: `docs/brd-experimental-workflow.md`
- Modify: `nodes/catalog/import_layout_file.yaml`
- Modify: `nodes/catalog/select_layout_nets.yaml`
- Modify: `nodes/catalog/create_layout_cutout.yaml`
- Modify: `nodes/catalog/configure_layout_stackup.yaml`
- Modify: `nodes/catalog/locate_layout_port_candidates.yaml`
- Modify: `nodes/catalog/create_layout_ports.yaml`
- Modify: `nodes/catalog/create_layout_setup.yaml`
- Test: `tests/test_workflow_templates.py`
- Test: `tests/test_node_catalog.py`

- [ ] **Step 1: Write failing documentation/catalog tests**

Add to `tests/test_node_catalog.py`:

```python
def test_layout_nodes_are_documented_as_experimental_with_limits():
    catalog = NodeCatalog.from_directory(Path("nodes/catalog"), include_experimental=True)
    layout_nodes = [
        catalog.get("import_layout_file"),
        catalog.get("select_layout_nets"),
        catalog.get("create_layout_cutout"),
        catalog.get("configure_layout_stackup"),
        catalog.get("locate_layout_port_candidates"),
        catalog.get("create_layout_ports"),
        catalog.get("create_layout_setup"),
    ]

    for metadata in layout_nodes:
        serialized = metadata.to_dict()
        assert serialized["status"] == "experimental"
        assert serialized["track"] == "layout-brd"
        assert "experimental" in serialized["description"].lower() or "experimental" in serialized["ui_hints"].get("badge", "").lower()
```

Add to `tests/test_workflow_templates.py`:

```python
def test_import_cutout_template_declares_model_build_only_limit():
    template = WorkflowTemplate.from_file(Path("workflow_templates/import_brd_cutout_sparam_tdr.json"))

    assert "model-build" in template.tags
    assert any("stops before analyze" in limit.lower() or "without running solve" in limit.lower() for limit in template.known_limits)
    assert template.workflow.metadata["experimental"] is True
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_node_catalog.py::test_layout_nodes_are_documented_as_experimental_with_limits tests/test_workflow_templates.py::test_import_cutout_template_declares_model_build_only_limit -q
```

Expected: FAIL because layout catalog descriptions/UI hints and template metadata do not yet consistently expose this contract.

- [ ] **Step 3: Add BRD workflow document**

Create `docs/brd-experimental-workflow.md`:

```markdown
# BRD/MCM Experimental Workflow

## 定位

BRD/MCM workflow 是 Stage C 的 experimental track，不属于默认 HFSS core catalog。它用于高频板级场景：导入 Cadence BRD/MCM，解析 net，执行 PyEDB cutout，导入 stackup，定位端口候选，创建 3D Layout port 和 setup。

## 当前节点边界

| 节点 | 当前职责 | 稳定性 |
| --- | --- | --- |
| import_layout_file | 打开 BRD/MCM 或 EDB，读取可用 net inventory | experimental |
| select_layout_nets | 将用户输入或 wildcard 解析为实际 signal/reference nets | experimental |
| create_layout_cutout | 调用 PyEDB cutout，保留 signal/reference nets 和 cutout artifact | experimental |
| configure_layout_stackup | 在 HFSS 3D Layout 中导入 stackup XML | experimental |
| locate_layout_port_candidates | 根据组件、pin、net、位置生成端口候选报告 | experimental |
| create_layout_ports | 根据候选端点创建 component cylinder port 或 ToggleViaPin Gap port | experimental |
| create_layout_setup | 创建 DC-to-67GHz 宽频 setup/sweep，默认不 analyze | experimental |

## 当前验收

- model-build only：默认不运行 analyze。
- 输出 artifact 包括 cutout summary、port candidate report、port action plan、AEDT project path。
- 端口规则来自本机 AEDT 录制脚本和人工检查，不声明跨板通用稳定。

## 已知限制

- 不同 BRD 的 component naming、pin naming、stackup layer naming 可能需要板级规则。
- 端口候选选择仍是启发式，复杂拓扑如串联电容、AC coupling、connector breakout 需要后续规则。
- TDR/S 参数后处理需要 solve，当前工作流默认跳过。
```

- [ ] **Step 4: Tighten catalog metadata**

For each Layout/BRD YAML listed above:

```yaml
status: experimental
track: layout-brd
notes:
  - Experimental Layout/BRD node; not included in default HFSS core planning.
  - Requires explicit experimental catalog enablement.
```

For `create_layout_setup.yaml`, add:

```yaml
notes:
  - Model-build default; solve_layout is intentionally not part of the default BRD demo on resource-limited machines.
  - High-speed broadband workflows should cover DC to 67GHz when TDR is required.
```

For `create_layout_ports.yaml`, add:

```yaml
notes:
  - Connector pin ports use ToggleViaPin plus HFSS Type Gap based on local AEDT UI recording.
  - BGA/IC endpoints may require component solder-ball cylinder settings supplied by user or board rules.
```

- [ ] **Step 5: Add template experimental metadata**

In `workflow_templates/import_brd_cutout_sparam_tdr.json`, set:

```json
"metadata": {
  "stage": "C",
  "demo": true,
  "experimental": true,
  "mode": "model-build-only"
}
```

- [ ] **Step 6: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_node_catalog.py tests/test_workflow_templates.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add docs/brd-experimental-workflow.md nodes/catalog workflow_templates/import_brd_cutout_sparam_tdr.json tests/test_node_catalog.py tests/test_workflow_templates.py
git commit -m "docs: define BRD experimental workflow contract"
```

---

## Task 2: Extract Reusable Layout Models and Port Services

**Files:**
- Create: `src/aedt_agent/layout/__init__.py`
- Create: `src/aedt_agent/layout/models.py`
- Create: `src/aedt_agent/layout/ports.py`
- Modify: `src/aedt_agent/demo/layout_ports.py`
- Test: `tests/test_layout_port_candidates.py`

- [ ] **Step 1: Write failing tests for reusable layout port package**

Add to `tests/test_layout_port_candidates.py`:

```python
from aedt_agent.layout.ports import plan_layout_port_actions as reusable_plan_layout_port_actions


def test_reusable_layout_port_planner_matches_demo_strategy():
    candidates = {
        "status": "ready",
        "signal_nets": ["SRDS_3_RX1_P", "SRDS_3_RX1_N"],
        "reference_nets": ["GND"],
        "recommended_endpoints": [
            {
                "name": "U1",
                "components": ["U1"],
                "component_type": "ic",
                "partname": "BGA_DEVICE",
                "pins": [
                    {"pin": "A1", "net": "SRDS_3_RX1_P", "position": [0, 0], "padstack": "BALL20"},
                    {"pin": "A2", "net": "SRDS_3_RX1_N", "position": [1, 0], "padstack": "BALL20"},
                    {"pin": "A3", "net": "GND", "position": [0.5, 0], "padstack": "BALL20"},
                ],
            },
            {
                "name": "J33",
                "components": ["J33"],
                "component_type": "io",
                "partname": "CONNECTOR",
                "pins": [
                    {"pin": "25", "net": "SRDS_3_RX1_N", "position": [10, 0], "padstack": "PIN"},
                    {"pin": "26", "net": "SRDS_3_RX1_P", "position": [11, 0], "padstack": "PIN"},
                    {"pin": "24", "net": "GND", "position": [10.5, 0], "padstack": "PIN"},
                ],
            },
        ],
    }

    plan = reusable_plan_layout_port_actions(candidates, impedance=50, solderball={"diameter": "20mil", "height": "10mil"})

    strategies = [action["strategy"] for action in plan["port_actions"]]
    assert "component_cylinder_port" in strategies
    assert "toggle_via_pin_gap_port" in strategies
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_port_candidates.py::test_reusable_layout_port_planner_matches_demo_strategy -q
```

Expected: FAIL because `aedt_agent.layout.ports` does not exist.

- [ ] **Step 3: Create layout package and move port service**

Create `src/aedt_agent/layout/__init__.py`:

```python
"""Reusable Layout/BRD experimental workflow services."""
```

Create `src/aedt_agent/layout/ports.py` by moving the implementation from `src/aedt_agent/demo/layout_ports.py`:

```python
from __future__ import annotations

# Move existing content from aedt_agent.demo.layout_ports here unchanged.
```

Then replace `src/aedt_agent/demo/layout_ports.py` with compatibility exports:

```python
from __future__ import annotations

from aedt_agent.layout.ports import apply_edb_layout_port_actions
from aedt_agent.layout.ports import apply_layout_port_actions
from aedt_agent.layout.ports import plan_layout_port_actions

__all__ = ["plan_layout_port_actions", "apply_layout_port_actions", "apply_edb_layout_port_actions"]
```

- [ ] **Step 4: Add layout models**

Create `src/aedt_agent/layout/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LayoutNetSelection:
    signal_nets: list[str]
    reference_nets: list[str]
    requested_signal_pattern: str = ""
    requested_reference_pattern: str = ""


@dataclass(frozen=True)
class LayoutPortCandidateReport:
    status: str
    signal_nets: list[str]
    reference_nets: list[str]
    recommended_endpoints: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class LayoutModelBuildSummary:
    layout_file: Path
    cutout_path: Path
    aedt_project: Path
    signal_nets: list[str]
    reference_nets: list[str]
    port_names: list[str] = field(default_factory=list)
    setup_name: str = ""
    solve_skipped: bool = True
```

- [ ] **Step 5: Update imports**

In `src/aedt_agent/demo/import_cutout.py` and `src/aedt_agent/mcp/node_executor.py`, replace imports from `aedt_agent.demo.layout_ports` with:

```python
from aedt_agent.layout.ports import apply_edb_layout_port_actions, apply_layout_port_actions, plan_layout_port_actions
```

- [ ] **Step 6: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_port_candidates.py tests/test_import_cutout_demo.py tests/test_node_executor.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/aedt_agent/layout src/aedt_agent/demo/layout_ports.py src/aedt_agent/demo/import_cutout.py src/aedt_agent/mcp/node_executor.py tests/test_layout_port_candidates.py
git commit -m "refactor: extract reusable layout port services"
```

---

## Task 3: Extract Reusable Import/Cutout Service Boundaries

**Files:**
- Create: `src/aedt_agent/layout/import_cutout.py`
- Modify: `src/aedt_agent/demo/import_cutout.py`
- Test: `tests/test_import_cutout_demo.py`
- Test: `tests/test_layout_import_cutout_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_layout_import_cutout_service.py`:

```python
from pathlib import Path

from aedt_agent.layout.import_cutout import normalize_net_patterns
from aedt_agent.layout.import_cutout import resolve_matching_nets


def test_normalize_net_patterns_accepts_string_and_list():
    assert normalize_net_patterns("SRDS_3_RX1_*") == ["SRDS_3_RX1_*"]
    assert normalize_net_patterns(["SRDS_3_RX1_P", "SRDS_3_RX1_N"]) == ["SRDS_3_RX1_P", "SRDS_3_RX1_N"]


def test_resolve_matching_nets_supports_wildcard_and_exact_names():
    available = ["GND", "SRDS_3_RX1_P", "SRDS_3_RX1_N", "SRDS_0_TX0_P"]

    assert resolve_matching_nets(["SRDS_3_RX1_*"], available) == ["SRDS_3_RX1_N", "SRDS_3_RX1_P"]
    assert resolve_matching_nets(["GND"], available) == ["GND"]
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_import_cutout_service.py -q
```

Expected: FAIL because `aedt_agent.layout.import_cutout` does not exist.

- [ ] **Step 3: Create reusable net-matching functions**

Create `src/aedt_agent/layout/import_cutout.py`:

```python
from __future__ import annotations

import fnmatch
from typing import Any


def normalize_net_patterns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise TypeError("net patterns must be a string or list")


def resolve_matching_nets(patterns: list[str], available_nets: list[str]) -> list[str]:
    resolved: set[str] = set()
    for pattern in patterns:
        if any(token in pattern for token in "*?[]"):
            resolved.update(net for net in available_nets if fnmatch.fnmatchcase(net, pattern))
        elif pattern in available_nets:
            resolved.add(pattern)
    return sorted(resolved)
```

- [ ] **Step 4: Delegate demo net parsing**

In `src/aedt_agent/demo/import_cutout.py`, replace local string/list net parsing helpers with:

```python
from aedt_agent.layout.import_cutout import normalize_net_patterns, resolve_matching_nets
```

Keep existing public functions and output keys unchanged.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_layout_import_cutout_service.py tests/test_import_cutout_demo.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aedt_agent/layout/import_cutout.py src/aedt_agent/demo/import_cutout.py tests/test_layout_import_cutout_service.py tests/test_import_cutout_demo.py
git commit -m "refactor: extract layout net resolution service"
```

---

## Task 4: Route Experimental NodeExecutor Outputs Through Reusable Services

**Files:**
- Modify: `src/aedt_agent/mcp/node_executor.py`
- Modify: `src/aedt_agent/mcp/node_schemas.py`
- Test: `tests/test_node_executor.py`

- [ ] **Step 1: Write failing NodeExecutor tests for experimental output contracts**

Add to `tests/test_node_executor.py`:

```python
def test_node_executor_select_layout_nets_resolves_wildcards_when_inventory_available(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "select_layout_nets",
        {
            "signal_nets": "SRDS_3_RX1_*",
            "reference_nets": "GND",
            "available_nets": ["GND", "SRDS_3_RX1_P", "SRDS_3_RX1_N", "SRDS_0_TX0_P"],
        },
    )

    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.output["signal_nets"] == ["SRDS_3_RX1_N", "SRDS_3_RX1_P"]
    assert result.output["reference_nets"] == ["GND"]
    assert result.output["postcheck"]["experimental"] is True
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_node_executor.py::test_node_executor_select_layout_nets_resolves_wildcards_when_inventory_available -q
```

Expected: FAIL because `available_nets` may be rejected or ignored.

- [ ] **Step 3: Expand schema**

In `src/aedt_agent/mcp/node_schemas.py`, add optional input for `select_layout_nets`:

```python
"available_nets": list,
```

- [ ] **Step 4: Update `_layout_placeholder_node` for select_layout_nets**

In `src/aedt_agent/mcp/node_executor.py`, for `select_layout_nets`, use:

```python
from aedt_agent.layout.import_cutout import normalize_net_patterns, resolve_matching_nets

available_nets = [str(item) for item in inputs.get("available_nets", [])]
signal_patterns = normalize_net_patterns(inputs.get("signal_nets"))
reference_patterns = normalize_net_patterns(inputs.get("reference_nets"))
signal_nets = resolve_matching_nets(signal_patterns, available_nets) if available_nets else signal_patterns
reference_nets = resolve_matching_nets(reference_patterns, available_nets) if available_nets else reference_patterns
```

Return these resolved lists in the existing output shape.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_node_executor.py tests/test_layout_import_cutout_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/aedt_agent/mcp/node_executor.py src/aedt_agent/mcp/node_schemas.py tests/test_node_executor.py
git commit -m "feat: resolve layout nets in experimental node executor"
```

---

## Task 5: Add Real MCP Smoke Script

**Files:**
- Create: `scripts/run_mcp_real_smoke.py`
- Test: `tests/test_mcp_real_smoke_script.py`

- [ ] **Step 1: Write failing smoke script tests**

Create `tests/test_mcp_real_smoke_script.py`:

```python
import subprocess
import sys


def test_mcp_real_smoke_help_lists_adapter_and_graphical_options():
    result = subprocess.run(
        [sys.executable, "scripts/run_mcp_real_smoke.py", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "--adapter" in result.stdout
    assert "--graphical" in result.stdout
    assert "--include-experimental" in result.stdout
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_real_smoke_script.py -q
```

Expected: FAIL because the script does not exist.

- [ ] **Step 3: Create smoke script**

Create `scripts/run_mcp_real_smoke.py`:

```python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.mcp.tools import create_kernel


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a minimal MCP-kernel AEDT smoke through controlled nodes.")
    parser.add_argument("--adapter", choices=["fake", "real"], default="fake")
    parser.add_argument("--project", default=str(REPO_ROOT / "benchmarks/runs/mcp_real_smoke/mcp_real_smoke.aedt"))
    parser.add_argument("--design", default="McpSmoke")
    parser.add_argument("--aedt-version", default="2026.1")
    parser.add_argument("--include-experimental", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--non-graphical", dest="non_graphical", action="store_true")
    mode.add_argument("--graphical", dest="non_graphical", action="store_false")
    parser.set_defaults(non_graphical=True)
    args = parser.parse_args()

    kernel = create_kernel(
        adapter=args.adapter,
        node_catalog_dir=REPO_ROOT / "nodes/catalog",
        version=args.aedt_version,
        non_graphical=args.non_graphical,
        include_experimental=args.include_experimental,
        timeout_seconds=120.0,
    )
    session = kernel.create_session(args.project, args.design)
    substrate = kernel.execute_node(
        "create_substrate",
        {"origin": [0, 0, 0], "size": [10, 10, 0.8], "material": "FR4_epoxy", "name": "Substrate"},
        session["session_id"],
    )
    setup = kernel.execute_node(
        "create_setup",
        {"frequency": "2.4GHz", "name": "Setup1", "max_passes": 1},
        session["session_id"],
    )
    summary = {
        "adapter": args.adapter,
        "session": session,
        "substrate": substrate.to_dict() if hasattr(substrate, "to_dict") else substrate.__dict__,
        "setup": setup.to_dict() if hasattr(setup, "to_dict") else setup.__dict__,
        "available_nodes": kernel.list_available_nodes(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    kernel.release_session(session["session_id"])


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run fake smoke and tests**

Run:

```bash
.venv/bin/python scripts/run_mcp_real_smoke.py --adapter fake
.venv/bin/python -m pytest tests/test_mcp_real_smoke_script.py tests/test_mcp_real_kernel_config.py -q
```

Expected: fake smoke prints JSON with `create_substrate`; tests PASS.

- [ ] **Step 5: Optional real smoke**

Only run this when AEDT should be started:

```bash
.venv/bin/python scripts/run_mcp_real_smoke.py --adapter real --graphical --aedt-version 2026.1
```

Expected: AEDT opens, creates `Substrate` and `Setup1` through MCP kernel. If AEDT license or desktop startup fails, document the failure and do not block unit tests.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_mcp_real_smoke.py tests/test_mcp_real_smoke_script.py
git commit -m "feat: add MCP real adapter smoke script"
```

---

## Task 6: Refresh Reports and Final Verification

**Files:**
- Modify: `docs/brd-experimental-workflow.md`
- Modify: `docs/aedt-agent-stage-c-progress-report.md`
- Modify: `benchmarks/reports/aedt_agent_stage_c_progress_report.html` if generated manually

- [ ] **Step 1: Add Stage C note**

Append to `docs/aedt-agent-stage-c-progress-report.md`:

```markdown
## BRD Experimental Track

BRD/MCM import-cutout remains experimental and opt-in. The reusable boundary is now documented in `docs/brd-experimental-workflow.md`; the default HFSS core catalog does not expose these nodes unless experimental nodes are enabled. The current BRD workflow is model-build only and intentionally stops before analyze on resource-limited machines.
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_layout_import_cutout_service.py \
  tests/test_layout_port_candidates.py \
  tests/test_import_cutout_demo.py \
  tests/test_node_executor.py \
  tests/test_mcp_real_smoke_script.py \
  tests/test_node_catalog.py \
  tests/test_workflow_templates.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run full tests**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS with skipped real-AEDT-gated tests only.

- [ ] **Step 4: Contract check**

Run:

```bash
.venv/bin/python scripts/check_contract_stabilization.py
```

Expected:

```text
"default_layout_nodes": []
```

- [ ] **Step 5: Commit and push**

Run:

```bash
git status --short
git add docs src tests scripts nodes workflow_templates
git commit -m "feat: productize BRD experimental workflow boundary"
git push
```

Expected: branch `stage-a-grounding-benchmark` pushed successfully. Do not commit local scratch files such as `session`.

---

## Self-Review

- Spec coverage: The plan covers documentation, extraction of reusable services, experimental node executor behavior, real MCP smoke, and report refresh.
- Scope control: The plan does not add UI features, does not add solve/postprocess to the BRD default path, and does not promote Layout/BRD nodes out of experimental.
- Placeholder scan: No open-ended “implement later” steps remain; each task has exact files, tests, commands, and expected outcomes.
- Risk note: Real AEDT smoke is optional and environment-gated. The model-build BRD path remains the main automated target.

