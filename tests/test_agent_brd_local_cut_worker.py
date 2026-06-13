from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers import (
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    build_brd_local_cut_job_input,
    run_brd_local_cut_worker,
)
from aedt_agent.agent.workers.registry import WorkerContext


def _job(tmp_path: Path, **overrides) -> JobRecord:
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    inputs = {
        "layout_file": layout_file,
        "signal_nets": ["56G_TX0_P", "56G_TX0_N"],
        "reference_nets": ["GND"],
        "local_cut_region": {"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        "artifact_dir": tmp_path / "artifacts",
        "target_metrics": [{"metric": "s21_db_at_56g", "op": ">=", "value": -8.0}],
        "port_candidates": {"status": "ready", "recommended_endpoints": [{"name": "U1"}, {"name": "J1"}]},
    }
    inputs.update(overrides)
    payload = build_brd_local_cut_job_input(**inputs)
    return JobRecord.create(
        job_id="job-1",
        mission_id="mission-1",
        capability=BRD_LOCAL_CUT_BUILD_CAPABILITY,
        idempotency_key="mission-1:brd-local-cut:0",
        input_payload=payload,
        timeout_seconds=300,
        retry_limit=1,
    )


def test_brd_local_cut_worker_writes_artifacts_and_bounded_summary(tmp_path):
    result = run_brd_local_cut_worker(_job(tmp_path), WorkerContext("worker-1"))

    summary_path = Path(result["artifact_refs"][0])
    workflow_path = Path(result["artifact_refs"][1])

    assert result["status"] == "model_review"
    assert summary_path.name == "brd_local_cut_summary.json"
    assert workflow_path.name == "workflow_run.json"
    assert json.loads(summary_path.read_text(encoding="utf-8"))["local_cut_region"]["unit"] == "mil"
    assert result["evidence_summary"]["raw_sparameters"] == "artifact_only"
    assert len(json.dumps(result["evidence_summary"])) < 2000


def test_brd_local_cut_worker_requires_user_bbox(tmp_path):
    with pytest.raises(ValueError, match="local_cut_region is required"):
        run_brd_local_cut_worker(_job(tmp_path, local_cut_region=None), WorkerContext("worker-1"))


def test_ambiguous_port_candidates_request_approval(tmp_path):
    job = _job(
        tmp_path,
        port_candidates={
            "status": "ambiguous",
            "candidates": [{"id": "p1", "label": "TX0-GND"}, {"id": "p2", "label": "TX1-GND"}],
        },
    )

    result = run_brd_local_cut_worker(job, WorkerContext("worker-1"))

    assert result["status"] == "waiting_approval"
    assert result["approval_required"]["reason"] == "port_candidates_ambiguous"
    assert [option["id"] for option in result["approval_required"]["options"]] == ["p1", "p2"]


class FakeRealBuildAdapter:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return type(
            "Result",
            (),
            {
                "summary": {
                    "status": "succeeded",
                    "adapter": "real_pyedb_hfss3dlayout_build_only",
                    "layout_file": str(request.layout_file),
                    "signal_nets": request.signal_nets,
                    "reference_nets": request.reference_nets,
                    "local_cut_region": request.local_cut_region,
                    "local_cut_polygon": {"type": "polygon", "unit": "mil", "points": [[1.0, 2.0]]},
                    "port_candidates": {"status": "ready", "candidate_count": 1},
                    "port_execution": {"status": "skipped"},
                    "layout_setup": {"setup_name": "Setup1", "sweep_name": "Sweep1"},
                    "layout_solve": {"status": "skipped", "reason": "model_review_only"},
                    "layout_reports": {},
                    "recorded_layout_settings": {},
                    "edb_path": str(request.artifact_dir / "case_cutout.aedb"),
                    "aedt_project": str(request.artifact_dir / "case_cutout_hfss.aedt"),
                    "target_metrics": request.target_metrics,
                    "steps": [],
                }
            },
        )()


def test_brd_local_cut_worker_can_use_real_build_adapter(tmp_path):
    fake_adapter = FakeRealBuildAdapter()
    job = _job(
        tmp_path,
        adapter_mode="real_build",
        recorded_layout_settings={"sweep_options": {"MaxSolutions": 2500}},
        aedt={"version": "2026.1", "non_graphical": False, "edb_backend": "auto"},
    )

    result = run_brd_local_cut_worker(job, WorkerContext("worker-1"), real_build_adapter=fake_adapter)

    summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
    assert result["status"] == "model_review"
    assert summary["adapter"] == "real_pyedb_hfss3dlayout_build_only"
    assert summary["layout_solve"]["status"] == "skipped"
    assert result["evidence_summary"]["raw_sparameters"] == "artifact_only"
    assert fake_adapter.requests[0].environment.version == "2026.1"


def test_brd_local_cut_worker_rejects_real_build_solve_enabled(tmp_path):
    job = _job(tmp_path, adapter_mode="real_build", solve_enabled=True)

    with pytest.raises(ValueError, match="solve_enabled"):
        run_brd_local_cut_worker(job, WorkerContext("worker-1"), real_build_adapter=FakeRealBuildAdapter())
