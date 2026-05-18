# Stage C.1 Demo/API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Stage C.1 demo/API layer that exposes nodes, templates, planning, validation, fake-adapter execution, and report links through a small HTTP service and lightweight web page.

**Architecture:** Add a focused `aedt_agent.demo` package. `config.py` loads committed example config plus ignored local config. `service.py` wraps existing Stage C modules and owns JSON-shaped API results. `web.py` uses Python standard library HTTP server and static HTML, avoiding new runtime dependencies.

**Tech Stack:** Python 3.12 standard library, existing `aedt_agent` modules, pytest, no FastAPI/Flask dependency.

---

## File Structure

- Modify: `docs/superpowers/specs/2026-05-17-stage-c1-demo-api-design.md`
  - Chinese Stage C.1 design spec.
- Create: `config/demo_config.example.json`
  - Committed blank demo configuration.
- Create: `src/aedt_agent/demo/__init__.py`
  - Package marker.
- Create: `src/aedt_agent/demo/config.py`
  - Load and merge example/local config safely.
- Create: `src/aedt_agent/demo/service.py`
  - Python API service used by HTTP server and tests.
- Create: `src/aedt_agent/demo/web.py`
  - Standard-library HTTP route dispatch and HTML rendering.
- Create: `scripts/run_stage_c1_demo_server.py`
  - Local start command for demo server.
- Create: `tests/test_stage_c1_demo_config.py`
  - Config behavior tests.
- Create: `tests/test_stage_c1_demo_service.py`
  - API service behavior tests.
- Create: `tests/test_stage_c1_demo_web.py`
  - Route dispatch and HTML contract tests.
- Modify: `docs/aedt-agent-stage-c-progress-report.md`
  - Add Stage C.1 pointer after implementation.

## Task 1: 中文 spec 与空配置

**Files:**
- Modify: `docs/superpowers/specs/2026-05-17-stage-c1-demo-api-design.md`
- Create: `config/demo_config.example.json`
- Test: `tests/test_stage_c1_demo_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_stage_c1_demo_config.py`:

```python
from pathlib import Path

from aedt_agent.demo.config import DemoConfig, load_demo_config


def test_load_demo_config_uses_blank_example_values(tmp_path):
    example = tmp_path / "demo_config.example.json"
    example.write_text(
        '{"planner":{"mode":"deterministic","provider":"","model":"","base_url":"","api_key":""},'
        '"server":{"host":"127.0.0.1","port":8765},'
        '"execution":{"default_adapter":"fake","run_dir":"benchmarks/runs/stage_c1_demo_latest"}}\n',
        encoding="utf-8",
    )

    config = load_demo_config(example_path=example, local_path=tmp_path / "missing.local.json")

    assert isinstance(config, DemoConfig)
    assert config.planner.mode == "deterministic"
    assert config.planner.api_key == ""
    assert config.execution.default_adapter == "fake"


def test_load_demo_config_local_overrides_without_requiring_secret(tmp_path):
    example = tmp_path / "demo_config.example.json"
    local = tmp_path / "demo_config.local.json"
    example.write_text(
        '{"planner":{"mode":"deterministic","provider":"","model":"","base_url":"","api_key":""},'
        '"server":{"host":"127.0.0.1","port":8765},'
        '"execution":{"default_adapter":"fake","run_dir":"benchmarks/runs/stage_c1_demo_latest"}}\n',
        encoding="utf-8",
    )
    local.write_text('{"server":{"port":9000},"planner":{"mode":"llm","model":"deepseek-v4-flash"}}\n', encoding="utf-8")

    config = load_demo_config(example_path=example, local_path=local)

    assert config.server.port == 9000
    assert config.planner.mode == "llm"
    assert config.planner.model == "deepseek-v4-flash"
    assert config.planner.api_key == ""
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_stage_c1_demo_config.py -q`

Expected: fail with `ModuleNotFoundError: No module named 'aedt_agent.demo'`.

- [ ] **Step 3: Implement config package**

Create `config/demo_config.example.json` with blank secrets. Create `src/aedt_agent/demo/__init__.py` and `src/aedt_agent/demo/config.py` with dataclasses `PlannerConfig`, `ServerConfig`, `ExecutionConfig`, `DemoConfig`, plus `load_demo_config()`.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_stage_c1_demo_config.py -q`

Expected: `2 passed`.

## Task 2: API service layer

**Files:**
- Create: `src/aedt_agent/demo/service.py`
- Test: `tests/test_stage_c1_demo_service.py`

- [ ] **Step 1: Write failing service tests**

Create tests that assert:

```python
from pathlib import Path

from aedt_agent.demo.service import DemoService


def test_demo_service_lists_nodes_templates_and_reports():
    service = DemoService(Path("."))

    status = service.status()
    nodes = service.nodes()
    templates = service.templates()
    reports = service.reports()

    assert status["default_adapter"] == "fake"
    assert len(nodes["nodes"]) >= 8
    assert {item["template_id"] for item in templates["templates"]} >= {"microstrip_sparameter", "wave_port_setup"}
    assert "stage_c_report" in reports["reports"]


def test_demo_service_plans_validates_and_runs_fake_template(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    plan = service.plan({"user_request": "create a microstrip s-parameter simulation at 5GHz"})
    workflow = plan["generated_workflow"]
    validation = service.validate({"workflow": workflow})
    run = service.run({"template_id": "microstrip_sparameter", "parameters": {"frequency": "5GHz"}})

    assert plan["selected_template"] == "microstrip_sparameter"
    assert validation["passed"] is True
    assert run["status"] == "succeeded"
    assert Path(run["artifacts"]["workflow_run"]).exists()
    assert Path(run["artifacts"]["report"]).exists()
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_stage_c1_demo_service.py -q`

Expected: fail because `aedt_agent.demo.service` does not exist.

- [ ] **Step 3: Implement `DemoService`**

Implement methods: `status()`, `nodes()`, `templates()`, `template(template_id)`, `plan(payload)`, `validate(payload)`, `run(payload)`, `reports()`. Use existing `NodeCatalog`, `WorkflowTemplateCatalog`, `ChatWorkflowPlanner`, `WorkflowValidator`, `WorkflowExecutor`, `FakeAedtAdapter`, `SessionManager`, `NodeExecutor`, and `AuditLogger`.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_stage_c1_demo_service.py -q`

Expected: service tests pass.

## Task 3: Standard-library HTTP server and HTML workspace

**Files:**
- Create: `src/aedt_agent/demo/web.py`
- Test: `tests/test_stage_c1_demo_web.py`

- [ ] **Step 1: Write failing web tests**

Create tests that assert:

```python
from pathlib import Path

from aedt_agent.demo.service import DemoService
from aedt_agent.demo.web import dispatch_demo_request, render_demo_page


def test_render_demo_page_contains_workspace_sections():
    html = render_demo_page()

    assert "AEDT Agent Stage C.1" in html
    assert "Templates" in html
    assert "Workflow Preview" in html
    assert "Run Fake Demo" in html


def test_dispatch_demo_request_serves_api_json(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "run")

    status, headers, body = dispatch_demo_request("GET", "/api/templates", b"", service)

    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert b"microstrip_sparameter" in body
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_stage_c1_demo_web.py -q`

Expected: fail because `aedt_agent.demo.web` does not exist.

- [ ] **Step 3: Implement `web.py`**

Implement `render_demo_page()`, `dispatch_demo_request(method, path, body, service)`, and `run_demo_server(host, port, repo_root, run_dir)`. Use `http.server.ThreadingHTTPServer` and a small request handler.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_stage_c1_demo_web.py -q`

Expected: web tests pass.

## Task 4: Start script and documentation

**Files:**
- Create: `scripts/run_stage_c1_demo_server.py`
- Modify: `docs/aedt-agent-stage-c-progress-report.md`
- Test: `tests/test_stage_c1_demo_web.py`

- [ ] **Step 1: Write failing script contract test**

Extend `tests/test_stage_c1_demo_web.py` to assert `scripts/run_stage_c1_demo_server.py` exists and contains `run_demo_server`.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_stage_c1_demo_web.py::test_stage_c1_demo_start_script_exists -q`

Expected: fail because script does not exist.

- [ ] **Step 3: Implement script**

Create script with CLI args `--host`, `--port`, `--run-dir`, `--config`. It should insert `src` into `sys.path`, load config, print the local URL, and call `run_demo_server()`.

- [ ] **Step 4: Update docs**

Add a Stage C.1 section to `docs/aedt-agent-stage-c-progress-report.md` with:

```bash
.venv/bin/python scripts/run_stage_c1_demo_server.py --port 8765
```

- [ ] **Step 5: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_stage_c1_demo_web.py -q`

Expected: web tests pass.

## Task 5: Full verification and commit

**Files:**
- All Stage C.1 files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_stage_c1_demo_config.py tests/test_stage_c1_demo_service.py tests/test_stage_c1_demo_web.py -q
```

Expected: all Stage C.1 tests pass.

- [ ] **Step 2: Run full tests**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run safety checks**

Run:

```bash
git diff --check
rg -n "sk-[A-Za-z0-9]{20,}|api\\.deepseek\\.com" config src scripts docs tests benchmarks/reports workflow_templates -g '!config/*.local.json'
```

Expected: no whitespace errors and no committed secret/base URL hits.

- [ ] **Step 4: Commit and push**

Commit message:

```bash
git commit -m "Add stage c1 demo api"
git push origin stage-a-grounding-benchmark
```
