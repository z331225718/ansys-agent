from pathlib import Path

from aedt_agent.demo.service import DemoService
from aedt_agent.demo.web import dispatch_demo_request, render_demo_page


def test_render_demo_page_contains_workspace_sections():
    html = render_demo_page()

    assert "AEDT Agent End-to-End Demo" in html
    assert "Microstrip S-Parameter Workflow" in html
    assert "Dipole 天线 S11" in html
    assert "Dipole Antenna S11 Workflow" in html
    assert "用户需求" in html
    assert "做一个微带线 S 参数仿真" in html
    assert "做一个偶极子天线 S11 仿真" in html
    assert "syncRequestToParameters" in html
    assert "changeWorkflow" in html
    assert "derivedDipoleArmLength" in html
    assert "派生单臂长度" in html
    assert "Plan with LLM" in html
    assert "plannerModeMetric" in html
    assert "Create Substrate" in html
    assert "Create Dipole Geometry" in html
    assert "Create Farfield Setup" in html
    assert "Create Ground, Trace" in html
    assert "Create Airbox" in html
    assert "Assign Radiation" in html
    assert "Create Lumped Port P1" in html
    assert "Create Setup" in html
    assert "Create Sweep" in html
    assert "Solve Setup" in html
    assert "Postprocess" in html
    assert "Run Real AEDT" in html
    assert "Run Offline Demo" not in html
    assert "Preview Workflow" not in html
    assert "graphical:true" in html
    assert "Validation Result" in html
    assert "S11 at selected frequency" in html
    assert "S21 at selected frequency" in html
    assert "S-Parameter Sweep" in html
    assert "sparamChart" in html
    assert "LLM 交互" in html
    assert "llmLog" in html
    assert "真实 AEDT Smoke" in html
    assert "4 个真实 AEDT workflow" in html


def test_dispatch_demo_request_serves_advanced_workspace(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "run")

    status, headers, body = dispatch_demo_request("GET", "/advanced", b"", service)

    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert "AEDT Agent 工作台".encode() in body
    assert "Planner Mode".encode() in body


def test_dispatch_demo_request_serves_api_json(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "run")

    status, headers, body = dispatch_demo_request("GET", "/api/templates", b"", service)

    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert b"microstrip_sparameter" in body


def test_dispatch_demo_request_starts_real_run_job_with_fake_adapter(tmp_path):
    service = DemoService(tmp_path, run_dir=tmp_path / "run")

    status, headers, body = dispatch_demo_request(
        "POST",
        "/api/run-real",
        b'{"template_id":"microstrip_sparameter","adapter":"fake","stream_to_terminal":false}',
        service,
    )

    data = body.decode("utf-8")
    assert status == 202
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert "job_id" in data
    assert "stage_c_real_demo_" in data


def test_dispatch_demo_request_serves_report_html(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "run")

    status, headers, body = dispatch_demo_request("GET", "/reports/stage_c_real_smoke_dashboard.html", b"", service)

    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert "Stage C".encode() in body


def test_dispatch_demo_request_serves_run_artifact(tmp_path):
    artifact = tmp_path / "benchmarks/runs/stage_c1_demo_latest/workflow_run.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"status":"succeeded"}\n', encoding="utf-8")
    service = DemoService(tmp_path, run_dir=artifact.parent)

    status, headers, body = dispatch_demo_request("GET", "/benchmarks/runs/stage_c1_demo_latest/workflow_run.json", b"", service)

    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert b"succeeded" in body


def test_stage_c1_demo_start_script_exists():
    script = Path("scripts/run_stage_c1_demo_server.py")
    source = script.read_text(encoding="utf-8")

    assert script.exists()
    assert "run_demo_server" in source
    assert "planner_config=config.planner" in source
    assert "default_adapter=config.execution.default_adapter" in source
    assert "aedt_config=config.aedt" in source
    assert "KeyboardInterrupt" in source
    assert "Stopping demo server." in source
