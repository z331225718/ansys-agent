import time
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
    assert status["real_aedt_from_browser"] is True


def test_demo_service_plans_validates_and_runs_fake_template(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    plan = service.plan({"user_request": "create a microstrip s-parameter simulation at 5GHz"})
    workflow = plan["generated_workflow"]
    validation = service.validate({"workflow": workflow})
    run = service.run({"template_id": "microstrip_sparameter", "parameters": {"frequency": "5GHz"}})

    assert plan["selected_template"] == "microstrip_sparameter"
    assert validation["passed"] is True
    assert run["status"] == "succeeded"
    assert [step["step_id"] for step in run["steps"]] == ["substrate", "trace", "setup", "sweep"]
    assert Path(run["artifacts"]["workflow_run"]).exists()
    assert Path(run["artifacts"]["report"]).exists()


def test_demo_service_real_run_job_can_use_fake_adapter(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    started = service.start_real_run({"template_id": "microstrip_sparameter", "adapter": "fake"})
    deadline = time.time() + 10
    status = started
    while status["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.1)
        status = service.real_run_status(started["job_id"])

    assert status["status"] == "succeeded"
    assert status["adapter"] == "fake"
    assert status["graphical"] is True
    assert status["model_validation"]["passed"] is True
    assert Path(status["artifacts"]["workflow_run"]).exists()
    assert Path(status["artifacts"]["stdout"]).exists()
