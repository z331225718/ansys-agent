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
