import time
from pathlib import Path
from unittest.mock import Mock

from aedt_agent.demo.service import DemoService, _agent_run_kind, _read_demo_sparameters, _stream_process_logs


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
    assert [step["step_id"] for step in run["steps"]] == [
        "substrate",
        "trace",
        "ground_pec",
        "trace_pec",
        "airbox",
        "radiation",
        "lumped_port_1",
        "lumped_port_2",
        "setup",
        "sweep",
        "solve",
        "postprocess",
    ]
    assert run["model_validation"]["passed"] is True
    validation_targets = {check["target"] for check in run["model_validation"]["checks"]}
    assert {"GroundPerfectE", "TracePerfectE"} <= validation_targets
    assert Path(run["outputs"]["touchstone"]).exists()
    assert run["sparameters"]["point_count"] == 1
    assert len(run["sparameters"]["samples"]) == 1
    assert run["sparameters"]["selected"]["s11_mag"] == 0.0
    assert run["sparameters"]["selected"]["s21_db"] is None
    assert Path(run["artifacts"]["workflow_run"]).exists()
    assert Path(run["artifacts"]["report"]).exists()


def test_demo_service_real_run_job_can_use_fake_adapter(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    started = service.start_real_run({"template_id": "microstrip_sparameter", "adapter": "fake", "stream_to_terminal": False})
    deadline = time.time() + 10
    status = started
    while status["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.1)
        status = service.real_run_status(started["job_id"])

    assert status["status"] == "succeeded"
    assert status["adapter"] == "fake"
    assert status["graphical"] is True
    assert status["model_validation"]["passed"] is True
    assert status["sparameters"]["point_count"] == 1
    assert Path(status["artifacts"]["workflow_run"]).exists()
    assert Path(status["artifacts"]["stdout"]).exists()


def test_demo_service_real_run_job_accepts_generated_workflow(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")
    plan = service.plan({"user_request": "create a microstrip s-parameter simulation at 5GHz"})

    started = service.start_real_run(
        {
            "workflow": plan["generated_workflow"],
            "adapter": "fake",
            "stream_to_terminal": False,
            "parameters": {"frequency": "5GHz"},
        }
    )
    deadline = time.time() + 10
    status = started
    while status["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.1)
        status = service.real_run_status(started["job_id"])

    assert status["status"] == "succeeded"
    assert status["template_id"] == "microstrip_sparameter_v1"
    assert (Path(status["run_dir"]) / "workflow_input.json").exists()
    assert status["model_validation"]["passed"] is True


def test_demo_service_real_run_job_can_use_dipole_template_with_fake_adapter(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    started = service.start_real_run(
        {
            "template_id": "dipole_antenna_s11_farfield",
            "adapter": "fake",
            "stream_to_terminal": False,
            "parameters": {"frequency": "2.4GHz", "sweep_start": "1GHz", "sweep_stop": "4GHz"},
        }
    )
    deadline = time.time() + 10
    status = started
    while status["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.1)
        status = service.real_run_status(started["job_id"])

    assert status["status"] == "succeeded"
    assert status["template_id"] == "dipole_antenna_s11_farfield"
    assert status["model_validation"]["passed"] is True
    assert status["outputs"]["farfield_setup"] == "InfiniteSphere1"
    assert status["sparameters"]["selected"]["s21_db"] is None
    assert Path(status["artifacts"]["touchstone"]).suffix == ".s1p"


def test_demo_service_tunes_dipole_resonance_from_feedback(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    result = service.tune_dipole(
        {
            "parameters": {
                "frequency": "2.5GHz",
                "dipole_arm_length_mm": 31.0,
                "sweep_start": "1GHz",
                "sweep_stop": "4GHz",
            }
        }
    )

    assert result["template_id"] == "dipole_antenna_s11_farfield"
    assert result["status"] == "converged"
    assert result["target_frequency_hz"] == 2.5e9
    assert 1 <= len(result["rounds"]) <= 3
    assert result["rounds"][0]["arm_length_mm"] == 31.0
    assert "缩短" in result["rounds"][0]["agent_message"]
    assert abs(result["rounds"][-1]["resonance_frequency_hz"] - 2.5e9) / 2.5e9 <= 0.02


def test_agent_run_kind_selects_tuning_for_dipole_resonance_request():
    assert _agent_run_kind("偶极子工作在2.5GHz，让谐振点落在2.5GHz") == "dipole_tuning"
    assert _agent_run_kind("做一个偶极子天线 S11 仿真，扫频到4GHz") == "single_workflow"
    assert _agent_run_kind("做一个微带线 S 参数仿真，求解频率 2.4GHz") == "single_workflow"


def test_demo_service_agent_run_starts_fake_tuning_job_when_llm_judges_tuning(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    started = service.start_agent_run(
        {
            "user_request": "偶极子工作在2.5GHz，让谐振点落在2.5GHz",
            "adapter": "fake",
            "stream_to_terminal": False,
            "parameters": {"frequency": "2.5GHz", "sweep_start": "1GHz", "sweep_stop": "4GHz"},
        }
    )
    deadline = time.time() + 10
    status = started
    while status["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.1)
        status = service.agent_run_status(started["job_id"])

    assert status["run_kind"] == "dipole_tuning"
    assert status["status"] == "succeeded"
    assert status["advisor_mode"] in {"llm", "engineering_fallback"}
    assert 1 <= len(status["rounds"]) <= 3
    assert status["rounds"][-1]["converged"] is True


def test_demo_service_agent_run_uses_workflow_parameters_when_payload_parameters_are_hidden(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")
    plan = service.plan({"user_request": "偶极子工作在3.1GHz，让谐振点落在3.1GHz"})

    started = service.start_agent_run(
        {
            "user_request": "偶极子工作在3.1GHz，让谐振点落在3.1GHz",
            "workflow": plan["generated_workflow"],
            "adapter": "fake",
            "stream_to_terminal": False,
        }
    )
    deadline = time.time() + 10
    status = started
    while status["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.1)
        status = service.agent_run_status(started["job_id"])

    assert status["run_kind"] == "dipole_tuning"
    assert status["status"] == "succeeded"
    assert status["tuning_result"]["target_frequency"] == "3.1GHz"
    assert status["rounds"][-1]["converged"] is True


def test_dipole_tuning_round_parameters_change_instantiated_geometry(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    workflow = service._template_catalog().get("dipole_antenna_s11_farfield").instantiate(
        {"frequency": "2.5GHz", "dipole_arm_length_mm": 34.0}
    )
    defaults = {parameter.name: parameter.default for parameter in workflow.parameters}
    geometry = workflow.node_by_id("dipole_geometry").inputs["geometry"]

    assert defaults["dipole_arm_length_mm"] == 34.0
    assert defaults["left_arm_origin"] == [-34.5, 0, 0]
    assert geometry[0]["height"] == {"$ref": "parameters.dipole_arm_length_mm"}


def test_demo_service_agent_run_starts_single_workflow_for_plain_request(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "stage_c1_demo")

    started = service.start_agent_run(
        {
            "user_request": "做一个微带线 S 参数仿真，求解频率 2.4GHz",
            "template_id": "microstrip_sparameter",
            "adapter": "fake",
            "stream_to_terminal": False,
            "parameters": {"frequency": "2.4GHz"},
        }
    )
    deadline = time.time() + 10
    status = started
    while status["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.1)
        status = service.agent_run_status(started["job_id"])

    assert status["run_kind"] == "single_workflow"
    assert status["status"] == "succeeded"
    assert status["template_id"] == "microstrip_sparameter"


def test_read_demo_sparameters_selects_nearest_frequency_and_converts_to_db(tmp_path):
    touchstone = tmp_path / "sample.s2p"
    touchstone.write_text(
        "\n".join(
            [
                "! demo touchstone",
                "# GHz S MA R 50",
                "1.0 0.5 0 0.8 0 0.8 0 0.5 0",
                "2.4 0.25 0 0.9 0 0.9 0 0.25 0",
                "3.0 0.1 0 0.7 0 0.7 0 0.1 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    parsed = _read_demo_sparameters(str(touchstone), target_frequency_hz=2.45e9)

    assert parsed["point_count"] == 3
    assert [sample["frequency"] for sample in parsed["samples"]] == [1.0, 2.4, 3.0]
    assert parsed["selected"]["frequency"] == 2.4
    assert round(parsed["selected"]["s11_db"], 2) == -12.04
    assert round(parsed["selected"]["s21_db"], 2) == -0.92


def test_read_demo_sparameters_supports_one_port_touchstone(tmp_path):
    touchstone = tmp_path / "dipole.s1p"
    touchstone.write_text(
        "\n".join(
            [
                "! demo one-port touchstone",
                "# GHz S MA R 50",
                "1.0 0.8 0",
                "2.4 0.2 0",
                "4.0 0.5 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    parsed = _read_demo_sparameters(str(touchstone), target_frequency_hz=2.45e9)

    assert parsed["point_count"] == 3
    assert parsed["selected"]["frequency"] == 2.4
    assert round(parsed["selected"]["s11_db"], 2) == -13.98
    assert parsed["selected"]["s21_db"] is None


def test_stream_process_logs_writes_files_and_terminal(capsys, tmp_path):
    process = Mock()
    process.stdout = iter(["out line\n"])
    process.stderr = iter(["err line\n"])
    process.wait.return_value = 0

    returncode = _stream_process_logs(
        process,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        terminal_prefix="[demo:test]",
    )

    captured = capsys.readouterr()
    assert returncode == 0
    assert "out line" in (tmp_path / "stdout.log").read_text(encoding="utf-8")
    assert "err line" in (tmp_path / "stderr.log").read_text(encoding="utf-8")
    assert "[demo:test] out line" in captured.out
    assert "[demo:test] stderr: err line" in captured.err
