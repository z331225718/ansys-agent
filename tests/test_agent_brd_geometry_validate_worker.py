from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.brd_geometry_validate import (
    BRD_GEOMETRY_VALIDATE_CAPABILITY,
    build_brd_geometry_validate_job_input,
    run_brd_geometry_validate_worker,
)
from aedt_agent.agent.workers.registry import WorkerContext, WorkerReportedError


def _job(input_payload: dict) -> JobRecord:
    return JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability=BRD_GEOMETRY_VALIDATE_CAPABILITY,
        idempotency_key="geometry-validate",
        input_payload=input_payload,
        timeout_seconds=30,
        retry_limit=1,
    )


def test_geometry_validator_passes_parameterized_antipad_bridge(tmp_path):
    payload = build_brd_geometry_validate_job_input(
        project_path=r"D:\models\case.aedt",
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "layers": ["L02_GND"],
                "center_padstack_instance_ids": [4294981993, 4294982001],
                "plane_shape_ids": [101],
                "target_radius": {"value": 20, "unit": "mil"},
                "parameter_name": "l02_void_r",
                "bridge_between_vias": True,
            }
        ],
        loop_context={"round_index": 1},
    )

    output = run_brd_geometry_validate_worker(
        _job(payload),
        WorkerContext(
            worker_id="worker-1",
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
    )

    assert output["status"] == "succeeded"
    assert output["actions"][0]["constraints"]["max_diameter"] == "44mil"
    assert output["geometry_validation"]["approval_issue_count"] == 0
    assert Path(output["geometry_validation_manifest"]).is_file()
    assert output["loop_context"]["last_geometry_validation_status"] == "succeeded"


def test_geometry_validator_allows_multi_center_antipad_with_explicit_bridge_pair(tmp_path):
    payload = build_brd_geometry_validate_job_input(
        project_path=r"D:\models\case.aedt",
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "layers": ["L02_GND"],
                "center_padstack_instance_ids": [
                    4294981993,
                    4294981994,
                    4294982001,
                    4294982002,
                ],
                "bridge_center_padstack_instance_ids": [
                    4294981993,
                    4294982001,
                ],
                "plane_shape_ids": [101],
                "target_radius": {"value": 22, "unit": "mil"},
                "parameter_name": "l02_void_r",
                "bridge_between_vias": True,
            }
        ],
    )

    output = run_brd_geometry_validate_worker(
        _job(payload),
        WorkerContext(
            worker_id="worker-1",
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
    )

    assert output["status"] == "succeeded"
    assert output["geometry_validation"]["approval_issue_count"] == 0


def test_geometry_validator_allows_antipad_on_any_layer_with_shape_evidence(tmp_path):
    payload = build_brd_geometry_validate_job_input(
        project_path=r"D:\models\case.aedt",
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "layers": ["L05"],
                "center_padstack_instance_ids": [501, 502],
                "plane_shape_ids": [102],
                "target_radius": {"value": 20, "unit": "mil"},
                "parameter_name": "l05_void_r",
                "bridge_between_vias": True,
            }
        ],
    )

    output = run_brd_geometry_validate_worker(
        _job(payload),
        WorkerContext(
            worker_id="worker-1",
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
    )

    assert output["status"] == "succeeded"
    assert output["geometry_validation"]["approval_issue_count"] == 0
    messages = [
        check["message"]
        for check in output["geometry_validation"]["checks"]
    ]
    assert any("selected shape evidence" in message for message in messages)


def test_geometry_validator_requires_approval_for_missing_shape_and_radius_limit(tmp_path):
    payload = build_brd_geometry_validate_job_input(
        project_path=r"D:\models\case.aedt",
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "layers": ["L03"],
                "center_padstack_instance_ids": [1, 2],
                "target_radius": {"value": 23, "unit": "mil"},
                "parameter_name": "l03_void_r",
                "bridge_between_vias": True,
            }
        ],
    )

    output = run_brd_geometry_validate_worker(
        _job(payload),
        WorkerContext(
            worker_id="worker-1",
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
    )

    assert output["status"] == "approval_required"
    assert output["edge_outcome"] == "approval_required"
    assert output["approval_reason"].startswith("geometry_validation:")
    assert output["approval_options"][0]["id"] == "approve"
    messages = [
        check["message"]
        for check in output["geometry_validation"]["approval_issues"]
    ]
    assert any("exceeds max 22mil" in message for message in messages)
    assert any("plane_shape_ids" in message for message in messages)


def test_geometry_validator_passes_shape_non_functional_pad(tmp_path):
    payload = build_brd_geometry_validate_job_input(
        project_path=r"D:\models\case.aedt",
        actions=[
            {
                "action_type": "non_functional_pad.add_or_enlarge",
                "implementation": "shape",
                "layers": ["L05"],
                "center_padstack_instance_ids": [501, 502],
                "target_radius": {"value": 8, "unit": "mil"},
                "parameter_name": "l05_nfp_r",
            }
        ],
    )

    output = run_brd_geometry_validate_worker(
        _job(payload),
        WorkerContext(
            worker_id="worker-1",
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
    )

    assert output["status"] == "succeeded"
    constraints = output["actions"][0]["constraints"]
    assert constraints["min_diameter"] == "15.75mil"
    assert constraints["max_diameter"] == "20mil"


def test_geometry_validator_reports_empty_action_input():
    with pytest.raises(WorkerReportedError) as exc:
        run_brd_geometry_validate_worker(
            _job({"project_path": "case.aedt", "actions": []}),
            WorkerContext(worker_id="worker-1"),
        )

    assert exc.value.error_class == "invalid_input"
    assert exc.value.retryable is False
