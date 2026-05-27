import json
import subprocess
import sys

from aedt_agent.layout.acceptance import build_brd_acceptance_summary
from aedt_agent.reporting.stage_c_brd_report import render_brd_acceptance_html


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_succeeded_run(run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        run_dir / "preflight.json",
        {
            "ok": True,
            "checks": [
                {"id": "pyaedt_package", "status": "passed", "message": "Python package is importable", "detail": {}},
                {"id": "cadence_environment", "status": "warning", "message": "CDSROOT is not configured", "detail": {}},
            ],
            "summary": {"passed": 1, "warning": 1, "failed": 0, "skipped": 0},
        },
    )
    _write_json(
        run_dir / "params.json",
        {
            "layout_file": "/boards/case.brd",
            "signal_nets": "SRDS_3_RX1_P,SRDS_3_RX1_N",
            "reference_nets": "GND",
        },
    )
    _write_json(
        run_dir / "workflow_run.json",
        {
            "workflow_id": "import_brd_cutout_sparam_tdr_v1",
            "status": "succeeded",
            "steps": [
                {"step_id": "import_layout_file", "status": "succeeded", "error_message": ""},
                {"step_id": "create_layout_ports", "status": "succeeded", "error_message": ""},
            ],
            "outputs": {
                "layout_file": "/boards/case.brd",
                "signal_nets": ["SRDS_3_RX1_P", "SRDS_3_RX1_N"],
                "reference_nets": ["GND"],
                "edb_path": "/runs/case_cutout.aedb",
                "aedt_project": "/runs/case_cutout.aedt",
                "touchstone": "",
                "tdr": "",
            },
        },
    )
    _write_json(
        run_dir / "import_cutout_summary.json",
        {
            "status": "succeeded",
            "layout_file": "/boards/case.brd",
            "signal_nets": ["SRDS_3_RX1_P", "SRDS_3_RX1_N"],
            "reference_nets": ["GND"],
            "edb_path": "/runs/case_cutout.aedb",
            "aedt_project": "/runs/case_cutout.aedt",
            "port_action_plan": {
                "port_actions": [
                    {"endpoint": "U1.A1", "strategy": "component_cylinder_port"},
                    {"endpoint": "U2.B2", "strategy": "toggle_via_pin_gap_port"},
                ]
            },
        },
    )
    (run_dir / "stdout.log").write_text("model build completed\n", encoding="utf-8")
    (run_dir / "stderr.log").write_text("", encoding="utf-8")


def test_build_brd_acceptance_summary_collects_successful_run(tmp_path):
    run_dir = tmp_path / "run"
    _write_succeeded_run(run_dir)

    summary = build_brd_acceptance_summary(run_dir)

    assert summary["status"] == "succeeded"
    assert summary["layout_file"] == "/boards/case.brd"
    assert summary["signal_nets"] == ["SRDS_3_RX1_P", "SRDS_3_RX1_N"]
    assert summary["reference_nets"] == ["GND"]
    assert summary["aedt_project"] == "/runs/case_cutout.aedt"
    assert summary["edb_path"] == "/runs/case_cutout.aedb"
    assert summary["port_action_count"] == 2
    assert summary["step_statuses"] == {"import_layout_file": "succeeded", "create_layout_ports": "succeeded"}
    assert summary["optional_results"]["touchstone"] == "not_available"
    assert summary["optional_results"]["tdr"] == "not_available"
    assert summary["warnings"] == ["cadence_environment: CDSROOT is not configured"]
    assert summary["blocking_issues"] == []
    assert "workflow_run.json" in summary["artifacts"]
    assert summary["artifacts"]["stdout.log"].endswith("stdout.log")


def test_build_brd_acceptance_summary_marks_failed_preflight_and_workflow(tmp_path):
    run_dir = tmp_path / "failed"
    _write_succeeded_run(run_dir)
    workflow = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
    workflow["status"] = "failed"
    workflow["steps"][1]["status"] = "failed"
    workflow["steps"][1]["error_message"] = "port creation failed"
    _write_json(run_dir / "workflow_run.json", workflow)
    preflight = json.loads((run_dir / "preflight.json").read_text(encoding="utf-8"))
    preflight["ok"] = False
    preflight["checks"].append({"id": "layout_file", "status": "failed", "message": "Layout file does not exist", "detail": {}})
    _write_json(run_dir / "preflight.json", preflight)

    summary = build_brd_acceptance_summary(run_dir)

    assert summary["status"] == "failed"
    assert "layout_file: Layout file does not exist" in summary["blocking_issues"]
    assert "create_layout_ports: port creation failed" in summary["blocking_issues"]


def test_package_stage_c_brd_run_writes_reports(tmp_path):
    run_dir = tmp_path / "run"
    _write_succeeded_run(run_dir)

    result = subprocess.run(
        [sys.executable, "scripts/package_stage_c_brd_run.py", "--run-dir", str(run_dir), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["status"] == "succeeded"
    assert (run_dir / "acceptance_report.json").exists()
    assert (run_dir / "acceptance_report.html").exists()


def test_package_stage_c_brd_run_returns_nonzero_for_failed_run_unless_allowed(tmp_path):
    run_dir = tmp_path / "failed"
    _write_succeeded_run(run_dir)
    workflow = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
    workflow["status"] = "failed"
    workflow["steps"][1]["status"] = "failed"
    workflow["steps"][1]["error_message"] = "port creation failed"
    _write_json(run_dir / "workflow_run.json", workflow)

    failed = subprocess.run(
        [sys.executable, "scripts/package_stage_c_brd_run.py", "--run-dir", str(run_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    allowed = subprocess.run(
        [sys.executable, "scripts/package_stage_c_brd_run.py", "--run-dir", str(run_dir), "--allow-failed"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert failed.returncode == 1
    assert allowed.returncode == 0


def test_render_brd_acceptance_html_contains_engineering_sections(tmp_path):
    run_dir = tmp_path / "run"
    _write_succeeded_run(run_dir)
    summary = build_brd_acceptance_summary(run_dir)

    html = render_brd_acceptance_html(summary)

    assert "Stage C BRD/MCM 生产验收报告" in html
    assert "环境预检" in html
    assert "节点执行状态" in html
    assert "端口策略" in html
    assert "输出文件" in html
    assert "SRDS_3_RX1_P" in html
    assert "GND" in html
    assert "/runs/case_cutout.aedt" in html
    assert "component_cylinder_port" in html


def test_render_brd_acceptance_html_shows_blocking_issues(tmp_path):
    run_dir = tmp_path / "failed"
    _write_succeeded_run(run_dir)
    workflow = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
    workflow["status"] = "failed"
    workflow["steps"][1]["status"] = "failed"
    workflow["steps"][1]["error_message"] = "port creation failed"
    _write_json(run_dir / "workflow_run.json", workflow)
    summary = build_brd_acceptance_summary(run_dir)

    html = render_brd_acceptance_html(summary)

    assert "阻塞问题" in html
    assert "port creation failed" in html


def test_run_stage_c_brd_acceptance_fake_adapter_writes_acceptance_package(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text(
        json.dumps({"layout_file": str(layout_file), "signal_nets": "*tx0*", "reference_nets": "gnd"}),
        encoding="utf-8",
    )
    run_dir = tmp_path / "acceptance"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_stage_c_brd_acceptance.py",
            "--adapter",
            "fake",
            "--params",
            str(params),
            "--run-dir",
            str(run_dir),
            "--config",
            "config/demo_config.example.json",
            "--local-config",
            str(tmp_path / "missing.local.json"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Stage C BRD acceptance: succeeded" in result.stdout
    assert (run_dir / "preflight.json").exists()
    assert (run_dir / "params.json").exists()
    assert (run_dir / "workflow_run.json").exists()
    assert (run_dir / "import_cutout_summary.json").exists()
    assert (run_dir / "acceptance_report.json").exists()
    assert (run_dir / "acceptance_report.html").exists()
