from __future__ import annotations

from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.brd_model_edit import (
    BRD_MODEL_EDIT_CAPABILITY,
    build_brd_model_edit_job_input,
    run_brd_model_edit_worker,
)
from aedt_agent.agent.workers.registry import WorkerContext, WorkerReportedError


def _job(input_payload: dict) -> JobRecord:
    return JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability=BRD_MODEL_EDIT_CAPABILITY,
        idempotency_key="model-edit",
        input_payload=input_payload,
        timeout_seconds=30,
        retry_limit=1,
    )


class FakeEditResult:
    edited_project_path = r"D:\runs\edited\case.edited.aedt"
    edited_edb_path = r"D:\runs\edited\case.edited.aedb"
    manifest_path = r"D:\runs\edited\model_edit_manifest.json"
    summary = {
        "status": "succeeded",
        "action_count": 1,
        "change_count": 2,
        "changes": [{"layer": "L05"}, {"layer": "L06_GND"}],
    }


class FakeEditAdapter:
    def __init__(self) -> None:
        self.request = None

    def run(self, request):
        self.request = request
        return FakeEditResult()


def test_model_edit_worker_returns_edited_project_for_next_solve(tmp_path):
    adapter = FakeEditAdapter()
    payload = build_brd_model_edit_job_input(
        project_path=r"D:\models\case.aedt",
        actions=[
            {
                "action_type": "anti_pad.enlarge",
                "parasitic_target": "l1_ball_and_l1_l4_laser_via_pad",
                "center_source": "padstack_instances",
                "center_padstack_instance_ids": [4294981993, 4294982001],
                "layers": ["L06_GND"],
                "plane_shape_ids": [101],
                "target_diameter": {"value": 0.6, "unit": "mm"},
                "bridge_between_vias": True,
            }
        ],
        project_copy_mode="working_project",
        aedt={"version": "2026.1", "edb_backend": "grpc"},
    )

    output = run_brd_model_edit_worker(
        _job(payload),
        WorkerContext(
            worker_id="worker-1",
            artifacts_dir=str(tmp_path / "artifacts"),
        ),
        edit_adapter=adapter,
    )

    assert adapter.request.project_path == Path(r"D:\models\case.aedt")
    assert adapter.request.artifact_dir == tmp_path / "artifacts"
    assert adapter.request.environment.edb_backend == "grpc"
    assert adapter.request.project_copy_mode == "working_project"
    assert output["status"] == "succeeded"
    assert output["edited_project_path"].endswith("case.edited.aedt")
    assert output["evidence_summary"]["raw_project"] == "artifact_only"
    assert output["evidence_summary"]["change_count"] == 2
    assert output["artifact_refs"] == [
        FakeEditResult.edited_project_path,
        FakeEditResult.edited_edb_path,
        FakeEditResult.manifest_path,
    ]


def test_model_edit_worker_requires_artifacts_dir():
    with pytest.raises(ValueError, match="artifacts_dir"):
        run_brd_model_edit_worker(
            _job({"project_path": "case.aedt", "actions": []}),
            WorkerContext(worker_id="worker-1"),
            edit_adapter=FakeEditAdapter(),
        )


def test_model_edit_worker_reports_invalid_input(tmp_path):
    class FailingAdapter:
        def run(self, request):
            raise ValueError("bad model edit")

    with pytest.raises(WorkerReportedError) as exc:
        run_brd_model_edit_worker(
            _job({"project_path": "case.aedt", "actions": []}),
            WorkerContext(
                worker_id="worker-1",
                artifacts_dir=str(tmp_path / "artifacts"),
            ),
            edit_adapter=FailingAdapter(),
        )

    assert exc.value.error_class == "invalid_input"
    assert exc.value.retryable is False
