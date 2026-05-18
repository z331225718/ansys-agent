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
