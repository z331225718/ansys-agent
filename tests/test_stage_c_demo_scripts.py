import json
import sys


def test_list_workflow_templates_outputs_template_ids(capsys, monkeypatch):
    import scripts.list_workflow_templates as script

    monkeypatch.setattr(sys, "argv", ["list_workflow_templates.py"])
    script.main()

    output = capsys.readouterr().out
    assert "microstrip_sparameter" in output
    assert "wave_port_setup" in output


def test_list_node_catalog_outputs_node_ids(capsys, monkeypatch):
    import scripts.list_node_catalog as script

    monkeypatch.setattr(sys, "argv", ["list_node_catalog.py"])
    script.main()

    output = capsys.readouterr().out
    assert "create_port" in output
    assert "create_setup" in output


def test_plan_workflow_from_chat_writes_json(tmp_path, monkeypatch):
    import scripts.plan_workflow_from_chat as script

    output_path = tmp_path / "plan.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "plan_workflow_from_chat.py",
            "--request",
            "create a microstrip s-parameter simulation at 5GHz",
            "--output",
            str(output_path),
        ],
    )

    script.main()

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["selected_template"] == "microstrip_sparameter"
    assert data["generated_workflow"]["workflow_id"] == "microstrip_sparameter_v1"


def test_run_workflow_template_writes_artifacts(tmp_path, monkeypatch):
    import scripts.run_workflow_template as script

    run_dir = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_workflow_template.py",
            "--template",
            "microstrip_sparameter",
            "--run-dir",
            str(run_dir),
        ],
    )

    script.main()

    run = json.loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
    assert run["status"] == "succeeded"
    assert (run_dir / "audit.jsonl").exists()
    assert (run_dir / "validation.json").exists()
    assert (run_dir / "report.html").exists()


def test_generate_node_evolution_report_writes_json(tmp_path, monkeypatch):
    import scripts.generate_node_evolution_report as script

    output_path = tmp_path / "evolution.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_node_evolution_report.py",
            "--source",
            "benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_report.json",
            "--output",
            str(output_path),
        ],
    )

    script.main()

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["source_count"] == 1
    assert data["proposals"]


def test_run_stage_c_real_workflow_smoke_supports_fake_adapter(tmp_path, monkeypatch):
    import scripts.run_stage_c_real_workflow_smoke as script

    run_dir = tmp_path / "real-smoke-contract"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_stage_c_real_workflow_smoke.py",
            "--adapter",
            "fake",
            "--template",
            "microstrip_sparameter",
            "--run-dir",
            str(run_dir),
        ],
    )

    script.main()

    summary = json.loads((run_dir / "smoke_summary.json").read_text(encoding="utf-8"))
    assert summary["adapter"] == "fake"
    assert summary["non_graphical"] is None
    assert summary["aedt"]["version"] == "2026.1"
    assert summary["status"] == "succeeded"
    assert (run_dir / "workflow_run.json").exists()
    assert (run_dir / "validation.json").exists()


def test_import_cutout_script_writes_workflow_run_artifact(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text(
        __import__("json").dumps(
            {
                "layout_file": str(layout_file),
                "signal_nets": "*tx0*",
                "reference_nets": "gnd",
            }
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"

    result = __import__("subprocess").run(
        [
            __import__("sys").executable,
            "scripts/run_stage_c_import_cutout.py",
            "--adapter",
            "fake",
            "--params",
            str(params),
            "--run-dir",
            str(run_dir),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    workflow_run = __import__("json").loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
    assert workflow_run["workflow_id"] == "import_brd_cutout_sparam_tdr_v1"
    assert workflow_run["status"] == "succeeded"
    assert workflow_run["outputs"]["signal_nets"] == ["56G_TX0_P", "56G_TX0_N"]
    assert '"workflow_id": "import_brd_cutout_sparam_tdr_v1"' in result.stdout


def test_import_cutout_script_prints_progress_heartbeat(tmp_path):
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("", encoding="utf-8")
    params = tmp_path / "params.json"
    params.write_text(
        __import__("json").dumps({"layout_file": str(layout_file), "signal_nets": "*tx0*", "reference_nets": "gnd"}),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"

    result = __import__("subprocess").run(
        [
            __import__("sys").executable,
            "scripts/run_stage_c_import_cutout.py",
            "--adapter",
            "fake",
            "--params",
            str(params),
            "--run-dir",
            str(run_dir),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "[brd-progress] import_layout_file running" in result.stdout
    assert "[brd-progress] create_layout_setup succeeded" in result.stdout
    workflow_run = __import__("json").loads((run_dir / "workflow_run.json").read_text(encoding="utf-8"))
    assert workflow_run["status"] == "succeeded"


def test_generate_stage_c_smoke_dashboard_writes_html_and_json(tmp_path, monkeypatch):
    import scripts.generate_stage_c_smoke_dashboard as script

    run_dir = tmp_path / "smoke"
    run_dir.mkdir()
    (run_dir / "smoke_summary.json").write_text(
        json.dumps(
            {
                "adapter": "real",
                "template": "wave_port_setup",
                "status": "succeeded",
                "step_count": 2,
                "artifacts": ["workflow_run.json", "validation.json"],
                "model_validation": {
                    "passed": True,
                    "summary": "Validation passed (1/1 checks).",
                    "checks": [{"rule": "port_exists", "target": "Port1", "passed": True}],
                    "failed_checks": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "workflow_run.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "steps": [
                    {"node_id": "select_face"},
                    {"node_id": "create_port"},
                ],
            }
        ),
        encoding="utf-8",
    )
    output_html = tmp_path / "dashboard.html"
    output_json = tmp_path / "dashboard.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_stage_c_smoke_dashboard.py",
            "--run-dir",
            str(run_dir),
            "--output-html",
            str(output_html),
            "--output-json",
            str(output_json),
        ],
    )

    script.main()

    dashboard = json.loads(output_json.read_text(encoding="utf-8"))
    html = output_html.read_text(encoding="utf-8")
    assert dashboard["summary"]["succeeded_count"] == 1
    assert dashboard["summary"]["coverage"] == ["port", "selection"]
    assert "Stage C 真实 AEDT Smoke Dashboard" in html
    assert "wave_port_setup" in html


def test_generate_node_evolution_review_writes_html_and_json(tmp_path, monkeypatch):
    import scripts.generate_node_evolution_review as script

    source = tmp_path / "evolution.json"
    source.write_text(
        json.dumps(
            {
                "source_count": 1,
                "evidence": [
                    {
                        "source": "unit",
                        "kind": "node_subgraph",
                        "summary": "create_conductor_or_geometry_group -> select_face -> create_port",
                        "count": 2,
                        "tasks": [],
                        "node_ids": ["create_conductor_or_geometry_group", "select_face", "create_port"],
                    }
                ],
                "proposals": [
                    {
                        "proposal_id": "proposal-test",
                        "source": "unit",
                        "problem_pattern": "Repeated node subgraph",
                        "affected_tasks": [],
                        "recommended_action": "add_node",
                        "candidate_node_metadata": {"node_id": "composite_port", "description": "candidate"},
                        "required_tests": ["test_node_catalog.py", "real_aedt_smoke_or_manual_gate"],
                        "risk_level": "medium",
                        "review_status": "proposed",
                        "evidence": [
                            {
                                "source": "unit",
                                "kind": "node_subgraph",
                                "summary": "create_conductor_or_geometry_group -> select_face -> create_port",
                                "count": 2,
                                "tasks": [],
                                "node_ids": ["create_conductor_or_geometry_group", "select_face", "create_port"],
                            }
                        ],
                        "notes": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_html = tmp_path / "review.html"
    output_json = tmp_path / "review.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_node_evolution_review.py",
            "--source",
            str(source),
            "--output-html",
            str(output_html),
            "--output-json",
            str(output_json),
        ],
    )

    script.main()

    review = json.loads(output_json.read_text(encoding="utf-8"))
    html = output_html.read_text(encoding="utf-8")
    assert review["summary"]["proposal_count"] == 1
    assert review["summary"]["blocked_count"] == 1
    assert review["proposals"][0]["candidate_node_id"] == "composite_port"
    assert "Stage C 节点进化 Proposal 审核报告" in html


def test_generate_stage_c_demo_index_writes_html_and_json(tmp_path, monkeypatch):
    import scripts.generate_stage_c_demo_index as script

    output_html = tmp_path / "index.html"
    output_json = tmp_path / "index.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_stage_c_demo_index.py",
            "--output-html",
            str(output_html),
            "--output-json",
            str(output_json),
        ],
    )

    script.main()

    index = json.loads(output_json.read_text(encoding="utf-8"))
    html = output_html.read_text(encoding="utf-8")
    assert index["title"] == "AEDT Agent Stage C Demo Index"
    assert any(link["kind"] == "real_aedt" for link in index["links"])
    assert "AEDT Agent Stage C Demo Index" in html
    assert "真实 AEDT Smoke Dashboard" in html


def test_generate_stage_c_workflow_expansion_report_writes_html_and_json(tmp_path, monkeypatch):
    import scripts.generate_stage_c_workflow_expansion_report as script

    output_html = tmp_path / "workflow_expansion.html"
    output_json = tmp_path / "workflow_expansion.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_stage_c_workflow_expansion_report.py",
            "--output-html",
            str(output_html),
            "--output-json",
            str(output_json),
        ],
    )

    script.main()

    data = json.loads(output_json.read_text(encoding="utf-8"))
    html = output_html.read_text(encoding="utf-8")
    assert data["title"] == "Stage C 工作流扩展报告"
    assert "偶极子天线" in html
    assert "create_farfield_setup" in html
    assert "create_dipole_antenna" not in html


def test_stage_c_scripts_keep_legacy_import_paths_for_compatibility():
    from pathlib import Path

    script_paths = {
        "scripts/run_stage_c1_demo_server.py": "from aedt_agent.demo",
        "scripts/run_stage_c_import_cutout.py": "from aedt_agent.demo",
        "scripts/run_stage_c_brd_acceptance.py": "from aedt_agent.demo",
        "scripts/run_stage_c5_local_cut_build.py": "from aedt_agent.demo",
    }

    for path, expected_import in script_paths.items():
        assert expected_import in Path(path).read_text(encoding="utf-8")
