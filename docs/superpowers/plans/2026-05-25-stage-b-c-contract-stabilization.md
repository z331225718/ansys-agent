# Stage B/C Contract Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the controlled AEDT workflow agent contract before adding more demos: real MCP execution, node lifecycle governance, knowledge assets, validation boundaries, and experimental Layout/BRD isolation.

**Architecture:** Keep the proven path `workflow JSON -> validator -> NodeExecutor -> AEDT -> validation/report/audit`. Close the public MCP entrypoint so it can use either fake or real adapters, add lifecycle metadata to the catalog so planners know what is stable, and isolate Layout/BRD work as experimental without deleting the real demo progress.

**Tech Stack:** Python 3.12, PyAEDT, FastMCP, YAML node catalog, pytest, existing `aedt_agent.mcp`, `aedt_agent.nodes`, `aedt_agent.workflow`, `aedt_agent.knowledge`, and `aedt_agent.demo` packages.

---

## Current Diagnosis

The two external reports are mostly right on the weak contracts:

- `src/aedt_agent/mcp/server.py` still hardcodes `create_fake_kernel()`, so external MCP clients cannot select real AEDT.
- `nodes/catalog/*.yaml` has no lifecycle/status field, so stable HFSS nodes and experimental Layout/BRD nodes are indistinguishable.
- Layout/BRD implementation exists mainly in `src/aedt_agent/demo/import_cutout.py` and `src/aedt_agent/demo/layout_ports.py`, while `NodeExecutor` still routes layout node IDs through `_layout_placeholder_node()`.
- `knowledge/api_semantics/api_semantics.seed.jsonl` and `knowledge/common_traps` do not yet represent the engineering knowledge we learned during Stage A/B/C.
- Reports need sharper language: current validation is strong structural validation plus limited result-file validation, not full electromagnetic correctness.

This plan does not roll back Stage C. It freezes new demo expansion and turns the existing work into a clearer, testable contract.

---

## File Structure

Modify these files:

- `src/aedt_agent/mcp/tools.py`  
  Add `create_real_kernel()` and shared kernel factory helpers.

- `src/aedt_agent/mcp/server.py`  
  Add adapter selection from explicit argument or environment variable.

- `src/aedt_agent/demo/config.py`  
  Reuse existing AEDT version/root config for real MCP adapter defaults where practical.

- `src/aedt_agent/nodes/models.py`  
  Add lifecycle/status fields to `NodeDefinition`.

- `src/aedt_agent/nodes/registry.py`  
  Add lifecycle-filtered node listing and whitelist methods.

- `src/aedt_agent/nodes/catalog.py`  
  Expose lifecycle in catalog DTOs used by planner/demo/report code.

- `nodes/catalog/*.yaml`  
  Add `status` and `track` fields. HFSS core nodes become `candidate` or `stable`; Layout/BRD nodes become `experimental`.

- `src/aedt_agent/chat/workflow_planner.py`  
  Ensure default planning ignores experimental nodes unless request explicitly selects a layout workflow/template.

- `src/aedt_agent/mcp/node_executor.py`  
  Make placeholder layout execution visibly experimental and prevent it from being mistaken for stable execution.

- `knowledge/common_traps/*.yaml`  
  Add the missing common traps and refine existing traps.

- `knowledge/api_semantics/api_semantics.seed.jsonl`  
  Fill high-value API records first, especially APIs used in current workflows and demo failures.

- `src/aedt_agent/benchmark/context_builder.py`  
  Ensure filled constraints/traps appear in generated context.

- `docs/aedt-agent-stage-c-progress-report.md`, `docs/aedt-agent-executive-report.md`, `benchmarks/reports/*.html` generation scripts  
  Update wording to distinguish structural validation, result-file validation, and electromagnetic validation.

Create these tests:

- `tests/test_mcp_real_kernel_config.py`
- `tests/test_node_lifecycle.py`
- `tests/test_planner_lifecycle_filter.py`
- `tests/test_knowledge_assets.py`
- `tests/test_validation_positioning.py`

---

## Task 1: Add Real/Fake MCP Kernel Selection

**Files:**
- Modify: `src/aedt_agent/mcp/tools.py`
- Modify: `src/aedt_agent/mcp/server.py`
- Test: `tests/test_mcp_real_kernel_config.py`

- [ ] **Step 1: Write failing tests for explicit fake/real kernel selection**

Create `tests/test_mcp_real_kernel_config.py`:

```python
from pathlib import Path

import pytest

from aedt_agent.mcp import server as server_module
from aedt_agent.mcp.tools import create_fake_kernel, create_kernel


def test_create_kernel_defaults_to_fake(tmp_path):
    kernel = create_kernel(adapter="fake", node_catalog_dir=Path("nodes/catalog"))

    session = kernel.create_session("Project", "Design")

    assert session["project_id"] == "Project"
    assert session["design_id"] == "Design"


def test_create_kernel_rejects_unknown_adapter():
    with pytest.raises(ValueError, match="adapter must be fake or real"):
        create_kernel(adapter="bogus", node_catalog_dir=Path("nodes/catalog"))


def test_create_server_passes_adapter_to_kernel(monkeypatch):
    captured = {}

    class FakeFastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            def decorator(fn):
                return fn
            return decorator

    def fake_create_kernel(*, adapter, node_catalog_dir, **kwargs):
        captured["adapter"] = adapter
        captured["node_catalog_dir"] = node_catalog_dir
        return create_fake_kernel(node_catalog_dir)

    monkeypatch.setitem(__import__("sys").modules, "fastmcp", type("FastMCPModule", (), {"FastMCP": FakeFastMCP}))
    monkeypatch.setattr(server_module, "create_kernel", fake_create_kernel)

    server_module.create_server(adapter="real", node_catalog_dir=Path("nodes/catalog"))

    assert captured["adapter"] == "real"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_real_kernel_config.py -q
```

Expected: FAIL because `create_kernel` does not exist and `create_server()` has no adapter argument.

- [ ] **Step 3: Implement shared kernel factory**

In `src/aedt_agent/mcp/tools.py`, add imports and functions:

```python
import os

from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter
```

Add below `create_fake_kernel()`:

```python
def create_real_kernel(
    node_catalog_dir: Path,
    audit_path: Path | None = None,
    dev_mode: bool = False,
    timeout_seconds: float = 120.0,
    version: str | None = None,
    non_graphical: bool = True,
    ansysem_root: str = "",
    awp_root: str = "",
) -> McpToolKernel:
    registry = NodeRegistry.from_directory(node_catalog_dir)

    def adapter_factory(project_id: str, design_id: str) -> PyaedtAdapter:
        return PyaedtAdapter(
            project_id=project_id,
            design_id=design_id,
            version=version or os.environ.get("AEDT_AGENT_AEDT_VERSION", "2026.1"),
            non_graphical=non_graphical,
            ansysem_root=ansysem_root or os.environ.get("AEDT_AGENT_ANSYSEM_ROOT", ""),
            awp_root=awp_root or os.environ.get("AEDT_AGENT_AWP_ROOT", ""),
        )

    session_manager = SessionManager(adapter_factory)
    queue = ExecutionQueue(timeout_seconds=timeout_seconds)
    node_executor = NodeExecutor(
        registry=registry,
        session_manager=session_manager,
        queue=queue,
        audit_logger=None if audit_path is None else AuditLogger(audit_path),
    )
    return McpToolKernel(
        registry=registry,
        session_manager=session_manager,
        node_executor=node_executor,
        queue=queue,
        ast_guard=AstGuard(),
        dev_mode=dev_mode,
    )


def create_kernel(
    adapter: str,
    node_catalog_dir: Path,
    audit_path: Path | None = None,
    dev_mode: bool = False,
    timeout_seconds: float = 120.0,
    version: str | None = None,
    non_graphical: bool = True,
    ansysem_root: str = "",
    awp_root: str = "",
) -> McpToolKernel:
    if adapter == "fake":
        return create_fake_kernel(node_catalog_dir=node_catalog_dir, audit_path=audit_path, dev_mode=dev_mode)
    if adapter == "real":
        return create_real_kernel(
            node_catalog_dir=node_catalog_dir,
            audit_path=audit_path,
            dev_mode=dev_mode,
            timeout_seconds=timeout_seconds,
            version=version,
            non_graphical=non_graphical,
            ansysem_root=ansysem_root,
            awp_root=awp_root,
        )
    raise ValueError("adapter must be fake or real")
```

- [ ] **Step 4: Wire server adapter configuration**

Modify `src/aedt_agent/mcp/server.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

from aedt_agent.mcp.tools import create_kernel


def create_server(
    node_catalog_dir: Path = Path("nodes/catalog"),
    adapter: str | None = None,
    audit_path: Path | None = None,
    dev_mode: bool = False,
):
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the mcp extra to run the FastMCP server: pip install -e .[mcp]") from exc

    selected_adapter = adapter or os.environ.get("AEDT_AGENT_MCP_ADAPTER", "fake")
    kernel = create_kernel(
        adapter=selected_adapter,
        node_catalog_dir=node_catalog_dir,
        audit_path=audit_path,
        dev_mode=dev_mode,
    )
    server = FastMCP("aedt-agent")
```

Keep the existing tool functions unchanged below that block.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_real_kernel_config.py tests/test_pyaedt_adapter_contract.py tests/test_node_executor.py -q
```

Expected: PASS. Real AEDT tests remain gated by their existing environment flags.

- [ ] **Step 6: Commit**

```bash
git add src/aedt_agent/mcp/tools.py src/aedt_agent/mcp/server.py tests/test_mcp_real_kernel_config.py
git commit -m "feat: allow MCP server to select real AEDT adapter"
```

---

## Task 2: Add Node Lifecycle Governance

**Files:**
- Modify: `src/aedt_agent/nodes/models.py`
- Modify: `src/aedt_agent/nodes/registry.py`
- Modify: `src/aedt_agent/nodes/catalog.py`
- Modify: `nodes/catalog/*.yaml`
- Test: `tests/test_node_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle tests**

Create `tests/test_node_lifecycle.py`:

```python
from pathlib import Path

from aedt_agent.nodes.registry import NodeRegistry


def test_catalog_nodes_have_status_and_track():
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    assert registry.nodes
    for node in registry.list_nodes():
        assert node.status in {"experimental", "candidate", "stable", "deprecated"}
        assert node.track in {"hfss-core", "hfss-demo", "layout-brd", "postprocess"}


def test_default_stable_catalog_excludes_experimental_layout_nodes():
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    default_nodes = registry.list_nodes(include_experimental=False)
    default_ids = {node.node_id for node in default_nodes}

    assert "create_substrate" in default_ids
    assert "create_layout_cutout" not in default_ids
    assert "import_layout_file" not in default_ids


def test_experimental_catalog_can_be_requested():
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))

    all_ids = {node.node_id for node in registry.list_nodes(include_experimental=True)}

    assert "create_layout_cutout" in all_ids
    assert "create_layout_ports" in all_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_node_lifecycle.py -q
```

Expected: FAIL because `NodeDefinition` has no `status` or `track`, and registry filtering does not exist.

- [ ] **Step 3: Add lifecycle fields to `NodeDefinition`**

Modify `src/aedt_agent/nodes/models.py`:

```python
@dataclass(frozen=True)
class NodeDefinition:
    node_id: str
    summary: str
    allowed_apis: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    prerequisites: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    status: str = "experimental"
    track: str = "hfss-core"

    @property
    def is_experimental(self) -> bool:
        return self.status == "experimental"
```

Update `from_dict()`:

```python
status = str(data.get("status", "experimental"))
track = str(data.get("track", "hfss-core"))
if status not in {"experimental", "candidate", "stable", "deprecated"}:
    raise ValueError(f"invalid node status: {status}")
if track not in {"hfss-core", "hfss-demo", "layout-brd", "postprocess"}:
    raise ValueError(f"invalid node track: {track}")
return cls(
    node_id=str(data["node_id"]),
    summary=str(data["summary"]),
    allowed_apis=_list_of_strings(data, "allowed_apis"),
    inputs=_mapping(data, "inputs"),
    outputs=_mapping(data, "outputs"),
    prerequisites=_list_of_strings(data, "prerequisites"),
    examples=_list_of_strings(data, "examples"),
    status=status,
    track=track,
)
```

- [ ] **Step 4: Add registry filtering**

Modify `src/aedt_agent/nodes/registry.py`:

```python
    def list_nodes(self, include_experimental: bool = True) -> list[NodeDefinition]:
        nodes = [self.nodes[node_id] for node_id in sorted(self.nodes)]
        if include_experimental:
            return nodes
        return [node for node in nodes if node.status in {"candidate", "stable"}]

    def api_whitelist(self, node_ids: list[str] | None = None, include_experimental: bool = True) -> list[str]:
        if node_ids is None:
            selected = self.list_nodes(include_experimental=include_experimental)
        else:
            selected = [self.get(node_id) for node_id in node_ids]
```

- [ ] **Step 5: Add YAML lifecycle metadata**

Add these fields to all HFSS geometry/setup/boundary nodes:

```yaml
status: candidate
track: hfss-core
```

Use `stable` only for nodes already covered by real smoke and repeated unit tests:

```yaml
status: stable
track: hfss-core
```

Recommended initial stable nodes:

- `create_substrate`
- `create_conductor_or_geometry_group`
- `select_face`
- `create_setup`
- `create_sweep_or_export`

Mark reporting nodes:

```yaml
status: candidate
track: postprocess
```

Mark all Layout/BRD nodes:

```yaml
status: experimental
track: layout-brd
```

- [ ] **Step 6: Include lifecycle in catalog output**

In `src/aedt_agent/nodes/catalog.py`, include `status` and `track` in any dict/DTO serialization. The returned dict for a node must contain:

```python
{
    "node_id": node.node_id,
    "summary": node.summary,
    "status": node.status,
    "track": node.track,
    "allowed_apis": node.allowed_apis,
    "inputs": node.inputs,
    "outputs": node.outputs,
}
```

- [ ] **Step 7: Run lifecycle and catalog tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_node_lifecycle.py tests/test_node_catalog.py tests/test_workflow_templates.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/aedt_agent/nodes/models.py src/aedt_agent/nodes/registry.py src/aedt_agent/nodes/catalog.py nodes/catalog tests/test_node_lifecycle.py
git commit -m "feat: add lifecycle governance to node catalog"
```

---

## Task 3: Keep Experimental Layout/BRD Out of Default Planning

**Files:**
- Modify: `src/aedt_agent/chat/workflow_planner.py`
- Modify: `src/aedt_agent/mcp/node_executor.py`
- Test: `tests/test_planner_lifecycle_filter.py`

- [ ] **Step 1: Write failing planner lifecycle tests**

Create `tests/test_planner_lifecycle_filter.py`:

```python
from pathlib import Path

from aedt_agent.chat.workflow_planner import ChatPlannerInput, ChatWorkflowPlanner
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.templates import WorkflowTemplateCatalog


def _input(request: str) -> ChatPlannerInput:
    return ChatPlannerInput(
        user_request=request,
        node_catalog=NodeCatalog.from_directory(Path("nodes/catalog")),
        workflow_templates=WorkflowTemplateCatalog.from_directory(Path("workflow_templates")),
    )


def test_default_antenna_request_does_not_select_layout_template():
    result = ChatWorkflowPlanner().plan(_input("做一个天线 S11 仿真"))

    assert result.selected_template != "import_brd_cutout_sparam_tdr"


def test_brd_keywords_can_select_experimental_layout_template():
    result = ChatWorkflowPlanner().plan(_input("导入 brd，选择 SRDS_3_RX1 差分线，cutout 后看 TDR"))

    assert result.selected_template == "import_brd_cutout_sparam_tdr"
```

- [ ] **Step 2: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner_lifecycle_filter.py -q
```

Expected: first test may pass; second confirms explicit BRD path remains available.

- [ ] **Step 3: Make layout placeholders visibly experimental**

In `src/aedt_agent/mcp/node_executor.py`, update `_layout_placeholder_node()` returned `postcheck`:

```python
"postcheck": {
    "passed": True,
    "checks": [f"{node_id}_accepted"],
    "experimental": True,
    "note": "Layout/BRD node is experimental in the MCP NodeExecutor path; real demo execution is implemented in a dedicated pipeline.",
},
```

This prevents reports from treating layout placeholders as stable model execution.

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner_lifecycle_filter.py tests/test_import_cutout_demo.py tests/test_node_executor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aedt_agent/chat/workflow_planner.py src/aedt_agent/mcp/node_executor.py tests/test_planner_lifecycle_filter.py
git commit -m "chore: isolate experimental layout workflow from default planning"
```

---

## Task 4: Fill High-Value Knowledge Assets

**Files:**
- Modify: `knowledge/api_semantics/api_semantics.seed.jsonl`
- Modify/Create: `knowledge/common_traps/*.yaml`
- Modify: `src/aedt_agent/benchmark/context_builder.py`
- Test: `tests/test_knowledge_assets.py`

- [ ] **Step 1: Write failing knowledge asset tests**

Create `tests/test_knowledge_assets.py`:

```python
import json
from pathlib import Path

import yaml


HIGH_VALUE_APIS = {
    "Hfss.modeler.create_box",
    "Hfss.modeler.create_rectangle",
    "Hfss.lumped_port",
    "Hfss.wave_port",
    "Hfss.create_setup",
    "Hfss.create_linear_count_sweep",
    "Hfss.assign_radiation_boundary_to_objects",
    "Hfss3dLayout.oeditor.ToggleViaPin",
}


def test_high_value_api_semantics_are_not_empty():
    records = {}
    for line in Path("knowledge/api_semantics/api_semantics.seed.jsonl").read_text().splitlines():
        if line.strip():
            item = json.loads(line)
            records[item["fqname"]] = item

    missing = HIGH_VALUE_APIS - set(records)
    assert not missing

    for fqname in HIGH_VALUE_APIS:
        item = records[fqname]
        assert json.loads(item["params_json"]), fqname
        assert json.loads(item["constraints_json"]), fqname
        assert json.loads(item["common_errors_json"]), fqname
        assert json.loads(item["source_refs_json"]), fqname


def test_required_common_traps_exist():
    required = {
        "waveport_no_background_contact",
        "airbox_too_small",
        "missing_ground_plane",
        "wrong_face_selected_for_port",
        "sweep_range_misses_target_frequency",
        "material_or_unit_mismatch",
        "boundary_assigned_to_wrong_object",
    }
    found = {path.stem for path in Path("knowledge/common_traps").glob("*.yaml")}

    assert required <= found


def test_common_traps_have_detection_rules():
    for path in Path("knowledge/common_traps").glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        assert data["trap_id"] == path.stem
        assert data["description"]
        assert data["detection_rule"]
        assert data["avoidance"]
```

- [ ] **Step 2: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_knowledge_assets.py -q
```

Expected: FAIL because several API records and trap files are missing or empty.

- [ ] **Step 3: Add missing common trap YAML files**

Create these files with concrete rules:

`knowledge/common_traps/waveport_no_background_contact.yaml`:

```yaml
trap_id: waveport_no_background_contact
description: Wave ports in HFSS must be assigned to an exterior face or a sheet that reaches the simulation background; buried internal faces frequently fail or solve incorrectly.
detection_rule: validate_wave_port_assignment_touches_background
avoidance:
  - Prefer lumped ports for microstrip demo workflows unless a true exterior wave port face is modeled.
  - When using wave ports, select the terminal face at the air/background boundary and verify the integration line.
applies_to:
  - Hfss.wave_port
  - create_wave_port
```

`knowledge/common_traps/sweep_range_misses_target_frequency.yaml`:

```yaml
trap_id: sweep_range_misses_target_frequency
description: The requested analysis or optimization frequency must fall inside the sweep range; otherwise plots and tuning logic can look valid while missing the target.
detection_rule: validate_target_frequency_inside_sweep
avoidance:
  - Parse target resonance, setup frequency, sweep start, and sweep stop separately.
  - For tuning, use the optimization target when judging resonance, not necessarily the setup frequency.
applies_to:
  - Hfss.create_linear_count_sweep
  - create_sweep_or_export
```

`knowledge/common_traps/material_or_unit_mismatch.yaml`:

```yaml
trap_id: material_or_unit_mismatch
description: Geometry may be created with correct dimensions but wrong units or missing conductor materials, causing invalid electromagnetic behavior.
detection_rule: validate_units_and_conductor_materials
avoidance:
  - Assign PEC or copper to signal traces and ground conductors.
  - Keep template dimensions in one declared unit system and convert derived lengths explicitly.
applies_to:
  - Hfss.modeler.create_box
  - Hfss.modeler.create_rectangle
  - Hfss.assign_material
```

`knowledge/common_traps/boundary_assigned_to_wrong_object.yaml`:

```yaml
trap_id: boundary_assigned_to_wrong_object
description: Radiation and PerfectE boundaries can be syntactically created on the wrong object or sheet; object existence alone is not enough validation.
detection_rule: validate_boundary_assignment_matches_expected_object
avoidance:
  - Assign radiation to the airbox/open region, not to conductors or substrate.
  - Assign PerfectE/PEC to conductor sheets only when material assignment is insufficient.
applies_to:
  - Hfss.assign_radiation_boundary_to_objects
  - Hfss.assign_perfecte_to_sheets
```

- [ ] **Step 4: Fill high-value API semantics**

Edit the corresponding JSONL records in `knowledge/api_semantics/api_semantics.seed.jsonl`. Each high-value API must include:

```json
"params_json": "[{\"name\":\"assignment\",\"type\":\"str|int|list\",\"required\":true}]",
"constraints_json": "[\"assignment must refer to an existing object, sheet, face, pin, or terminal depending on API\", \"units must be explicit when values are strings\"]",
"common_errors_json": "[\"object or face not found\", \"invalid frequency string\", \"port assigned to wrong geometry\"]",
"common_traps_json": "[\"material_or_unit_mismatch\"]",
"source_refs_json": "[\"pyaedt source\", \"local real AEDT smoke\", \"recorded AEDT script when applicable\"]"
```

Do not claim official documentation for entries that came from local experiments. Use `"source_refs_json": "[\"local real AEDT smoke\"]"` for learned demo behavior.

- [ ] **Step 5: Rebuild SQLite knowledge DB if required**

Run:

```bash
.venv/bin/python -m aedt_agent.knowledge.build_sqlite
```

Expected: `knowledge/api_semantics/api_semantics.sqlite` is updated without errors.

- [ ] **Step 6: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_knowledge_assets.py tests/test_knowledge_provider.py tests/test_benchmark_context_builder.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add knowledge src/aedt_agent/benchmark/context_builder.py tests/test_knowledge_assets.py
git commit -m "data: fill core AEDT API semantics and traps"
```

---

## Task 5: Clarify Validation Boundaries in Reports

**Files:**
- Modify: `docs/aedt-agent-stage-c-progress-report.md`
- Modify: `docs/aedt-agent-executive-report.md`
- Modify: report generation scripts that emit validation language
- Test: `tests/test_validation_positioning.py`

- [ ] **Step 1: Write failing report language test**

Create `tests/test_validation_positioning.py`:

```python
from pathlib import Path


REPORT_FILES = [
    Path("docs/aedt-agent-stage-c-progress-report.md"),
    Path("docs/aedt-agent-executive-report.md"),
]


def test_reports_describe_validation_layers_without_overclaiming():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in REPORT_FILES if path.exists())

    assert "结构性验证" in combined
    assert "结果文件验证" in combined
    assert "电磁语义验证" in combined
    assert "不是完整电磁正确性证明" in combined
```

- [ ] **Step 2: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_validation_positioning.py -q
```

Expected: FAIL until wording is updated.

- [ ] **Step 3: Update report wording**

Add this section to both report docs:

```markdown
## Validation 边界

当前系统的验证分三层：

1. 结构性验证：检查对象、材料、端口、边界、setup、sweep、report 是否按 workflow 预期创建。
2. 结果文件验证：检查 Touchstone、CSV、TDR 等文件是否存在、可解析，且频率范围覆盖用户目标。
3. 电磁语义验证：只在少数模板中使用启发式规则，例如谐振点是否接近目标频率；这不是完整电磁正确性证明。

因此，当前结论应表述为“受控 workflow 能稳定生成并验证 AEDT 模型结构和基础结果文件”，不应表述为“自动保证仿真设计物理正确”。
```

- [ ] **Step 4: Update HTML/report generator text**

Search and replace overclaiming strings:

```bash
rg -n "电磁正确|semantic|物理正确|验证通过|real AEDT smoke" docs benchmarks src scripts
```

For generated report code, use the same three-layer wording.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_validation_positioning.py tests/test_stage_c1_demo_web.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs scripts src tests/test_validation_positioning.py
git commit -m "docs: clarify validation boundaries"
```

---

## Task 6: Add a Stabilization Dashboard Check

**Files:**
- Create: `scripts/check_contract_stabilization.py`
- Test: `tests/test_contract_stabilization_check.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_contract_stabilization_check.py`:

```python
from scripts.check_contract_stabilization import collect_contract_status


def test_contract_status_reports_required_sections():
    status = collect_contract_status()

    assert "mcp_adapter_modes" in status
    assert "node_lifecycle" in status
    assert "knowledge_assets" in status
    assert "validation_positioning" in status


def test_contract_status_has_no_layout_nodes_in_default_catalog():
    status = collect_contract_status()

    assert status["node_lifecycle"]["experimental_layout_nodes"]
    assert not status["node_lifecycle"]["default_layout_nodes"]
```

- [ ] **Step 2: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_contract_stabilization_check.py -q
```

Expected: FAIL because the script does not exist.

- [ ] **Step 3: Create check script**

Create `scripts/check_contract_stabilization.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from aedt_agent.nodes.registry import NodeRegistry


def collect_contract_status() -> dict:
    registry = NodeRegistry.from_directory(Path("nodes/catalog"))
    all_nodes = registry.list_nodes(include_experimental=True)
    default_nodes = registry.list_nodes(include_experimental=False)

    experimental_layout = [node.node_id for node in all_nodes if node.track == "layout-brd" and node.status == "experimental"]
    default_layout = [node.node_id for node in default_nodes if node.track == "layout-brd"]

    trap_files = list(Path("knowledge/common_traps").glob("*.yaml"))
    api_records = [
        line
        for line in Path("knowledge/api_semantics/api_semantics.seed.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    return {
        "mcp_adapter_modes": {
            "fake": True,
            "real": True,
            "env_var": "AEDT_AGENT_MCP_ADAPTER",
        },
        "node_lifecycle": {
            "total_nodes": len(all_nodes),
            "default_nodes": len(default_nodes),
            "experimental_layout_nodes": experimental_layout,
            "default_layout_nodes": default_layout,
        },
        "knowledge_assets": {
            "api_records": len(api_records),
            "common_traps": len(trap_files),
        },
        "validation_positioning": {
            "structural_validation": True,
            "result_file_validation": True,
            "electromagnetic_semantic_validation": "limited-template-heuristics",
        },
    }


def main() -> None:
    print(json.dumps(collect_contract_status(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run script and tests**

Run:

```bash
.venv/bin/python scripts/check_contract_stabilization.py
.venv/bin/python -m pytest tests/test_contract_stabilization_check.py -q
```

Expected: JSON summary printed; tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_contract_stabilization.py tests/test_contract_stabilization_check.py
git commit -m "chore: add contract stabilization status check"
```

---

## Task 7: Final Verification and GitHub Push

**Files:**
- No new files required unless previous tasks update docs.

- [ ] **Step 1: Run focused contract tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mcp_real_kernel_config.py \
  tests/test_node_lifecycle.py \
  tests/test_planner_lifecycle_filter.py \
  tests/test_knowledge_assets.py \
  tests/test_validation_positioning.py \
  tests/test_contract_stabilization_check.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run Stage C regression tests that should remain stable**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_import_cutout_demo.py \
  tests/test_layout_port_candidates.py \
  tests/test_stage_c1_demo_service.py \
  tests/test_stage_c1_demo_web.py \
  tests/test_node_executor.py \
  tests/test_node_catalog.py \
  tests/test_workflow_templates.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run optional real AEDT smoke only if AEDT is available**

Run this only when AEDT is intentionally available:

```bash
RUN_REAL_AEDT=1 .venv/bin/python -m pytest tests/test_pyaedt_adapter_contract.py tests/test_real_aedt_nodes.py -q -s
```

Expected: PASS or documented environmental failure. Do not block non-AEDT CI on this command.

- [ ] **Step 4: Inspect dirty tree**

Run:

```bash
git status --short
```

Expected: only intended files are modified or untracked.

- [ ] **Step 5: Push to GitHub**

Run:

```bash
git push
```

Expected: branch pushed successfully. Do not include local API keys or secrets.

---

## Self-Review

- Spec coverage: This plan covers the key observations from both reports: real MCP adapter, lifecycle governance, experimental Layout/BRD isolation, knowledge/trap assets, validation wording, and a contract health check.
- Placeholder scan: No task uses TBD/TODO/fill-later language. Each task has concrete files, test names, commands, and expected outcomes.
- Scope control: The plan deliberately freezes new demo features. It does not implement another workflow or UI redesign.
- Type consistency: The lifecycle fields are consistently named `status` and `track`; the registry filter is consistently `include_experimental`.

