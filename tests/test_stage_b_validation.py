from pathlib import Path

from aedt_agent.benchmark.stage_b_validation import run_stage_b_validation


def test_stage_b_validation_passes_expected_outputs():
    result = run_stage_b_validation(
        validation_script=Path("benchmarks/validation_scripts/validate_session.py"),
        session_id="session-1",
        project_id="p1",
        design_id="d1",
        model_info={
            "objects": {"Substrate": {"material": "FR4_epoxy"}},
            "ports": {},
            "boundaries": {},
            "setups": {},
            "sweeps": {},
        },
        expected_outputs=["substrate"],
    )

    assert result["passed"] is True
    assert "substrate_present" in result["checks"]


def test_stage_b_validation_fails_missing_expected_outputs():
    result = run_stage_b_validation(
        validation_script=Path("benchmarks/validation_scripts/validate_session.py"),
        session_id="session-1",
        project_id="p1",
        design_id="d1",
        model_info={"objects": {}, "ports": {}, "boundaries": {}, "setups": {}, "sweeps": {}},
        expected_outputs=["substrate"],
    )

    assert result["passed"] is False
    assert result["failure_type"] == "validation_fail"
    assert result["failures"] == ["missing expected output: substrate"]


def test_stage_b_validation_checks_wave_port_selected_face_traceability():
    result = run_stage_b_validation(
        validation_script=Path("benchmarks/validation_scripts/validate_session.py"),
        session_id="session-1",
        project_id="p1",
        design_id="d1",
        model_info={
            "objects": {"waveguide": {"material": "copper"}},
            "ports": {"Port1": {"type": "Wave Port"}},
            "boundaries": {},
            "setups": {},
            "sweeps": {},
        },
        expected_outputs=["wave_port"],
        known_failure_modes=["wrong_face_selected_for_port"],
        node_steps=[
            {"node_id": "select_face", "output": {"selected_face_id": 42}},
            {"node_id": "create_port", "inputs": {"port_type": "wave", "assignment": 42}},
        ],
    )

    assert result["passed"] is True
    assert "wave_port_uses_selected_face" in result["checks"]


def test_stage_b_validation_fails_untraceable_wave_port_face():
    result = run_stage_b_validation(
        validation_script=Path("benchmarks/validation_scripts/validate_session.py"),
        session_id="session-1",
        project_id="p1",
        design_id="d1",
        model_info={
            "objects": {"waveguide": {"material": "copper"}},
            "ports": {"Port1": {"type": "Wave Port"}},
            "boundaries": {},
            "setups": {},
            "sweeps": {},
        },
        expected_outputs=["wave_port"],
        known_failure_modes=["wrong_face_selected_for_port"],
        node_steps=[
            {"node_id": "select_face", "output": {"selected_face_id": 42}},
            {"node_id": "create_port", "inputs": {"port_type": "wave", "assignment": 7}},
        ],
    )

    assert result["passed"] is False
    assert "wave port assignment is not traceable to a selected face" in result["failures"]
