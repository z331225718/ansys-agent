from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_runner import resume_graph, run_graph
from aedt_agent.agent.graph_template import load_graph_template
from aedt_agent.agent.mission import (
    ArtifactManifest,
    GraphRunRecord,
    GraphRunStatus,
    JobAttemptRecord,
    JobAttemptStatus,
    MissionState,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.real_aedt_acceptance import validate_real_aedt_acceptance
from aedt_agent.agent.workers import (
    BRD_CHANNEL_SCORE_CAPABILITY,
    BRD_REAL_SOLVE_CAPABILITY,
    InMemoryWorkerRegistry,
    build_brd_real_solve_job_input,
    run_brd_channel_score_worker,
)
from aedt_agent.infrastructure import (
    BrdRealSolveAdapter,
    BrdRealSolveRequest,
    RealAedtEnvironment,
    SQLiteMissionStore,
)
from aedt_agent.infrastructure.harness import (
    HarnessWorkspacePolicy,
    LocalProcessHarness,
)


class SimulatedHfss3dLayout:
    setup_names = ["Setup1"]
    setup_sweeps_names = ["Setup1 : Sweep1"]
    port_list = ["P1", "P2"]

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def analyze_setup(self, name, blocking):
        return name == "Setup1" and blocking is True

    def save_project(self, file_name):
        Path(file_name).write_text("simulated solved project", encoding="utf-8")
        return True

    def export_touchstone(self, setup, sweep, output_file):
        Path(output_file).write_text(
            "# GHz S MA R 50\n"
            "0 0.05 0 0.9 0 0.9 0 0.05 0\n"
            "18 0.45 0 0.8 0 0.8 0 0.05 0\n",
            encoding="utf-8",
        )
        return output_file

    def release_desktop(self, close_projects, close_desktop):
        return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _register_artifacts(runtime, mission_id: str, producer_id: str, refs: list[str]) -> None:
    for ref in refs:
        path = Path(ref)
        runtime.store.create_artifact_manifest(
            ArtifactManifest.create(
                artifact_id=str(uuid4()),
                mission_id=mission_id,
                producer_kind="job",
                producer_id=producer_id,
                path=str(path),
                kind="artifact",
                sha256=_sha256(path),
                size_bytes=path.stat().st_size,
            )
        )


def _attested_graph(
    tmp_path: Path,
    *,
    complete: bool = True,
) -> tuple[AgentRuntime, str, Path]:
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("attested real solve", [], [])
    approval = ApprovalService(store).request_approval(
        mission.mission_id,
        "approve_real_brd_solve",
        [{"id": "approve", "label": "approve"}],
    )
    ApprovalService(store).approve(approval.approval_id, "approve")

    source = tmp_path / "approved.aedt"
    source.write_text("approved project", encoding="utf-8")
    artifact_dir = tmp_path / "real-artifacts"
    artifact_dir.mkdir()
    solve_input = build_brd_real_solve_job_input(
        project_path=source,
        setup_name="Setup1",
        sweep_name="Sweep1",
        tdr_expression="TDRZt(P1,P1)",
        expected_port_count=2,
        aedt={"version": "2026.1", "non_graphical": True},
    )
    adapter_result = BrdRealSolveAdapter(
        hfss3dlayout_factory=SimulatedHfss3dLayout
    ).run(
        BrdRealSolveRequest(
            project_path=source,
            artifact_dir=artifact_dir,
            setup_name="Setup1",
            sweep_name="Sweep1",
            solution_name="Setup1 : Sweep1",
            touchstone_name="channel.s2p",
            tdr_report_name="ChannelTDR",
            tdr_expression="TDRZt(P1,P1)",
            expected_port_count=2,
            environment=RealAedtEnvironment(version="2026.1", non_graphical=True),
        )
    )
    solve_refs = [
        adapter_result.project_checkpoint,
        adapter_result.solved_project,
        adapter_result.touchstone_path,
        adapter_result.tdr_path,
        adapter_result.solve_manifest_path,
    ]
    solve_output = {
        "status": "succeeded",
        "solved_project": adapter_result.solved_project,
        "solve_summary": adapter_result.summary,
        "touchstone_path": adapter_result.touchstone_path,
        "tdr_path": adapter_result.tdr_path,
        "solve_manifest": adapter_result.solve_manifest_path,
        "artifact_dir": str(artifact_dir),
        "evidence_summary": {
            "raw_sparameters": "artifact_only",
            "raw_tdr": "artifact_only",
        },
    }
    solve_job = runtime.create_job(
        mission.mission_id,
        BRD_REAL_SOLVE_CAPABILITY,
        "attested-solve",
        solve_input,
    )
    store.complete_job(solve_job.job_id, solve_output, solve_refs)
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            str(uuid4()),
            mission.mission_id,
            solve_job.job_id,
            1,
            "local-harness",
        )
    )
    store.complete_job_attempt(
        attempt.attempt_id,
        JobAttemptStatus.SUCCEEDED,
        retry_decision="none",
        metadata={
            "execution_mode": "local_process",
            "harness_status": "succeeded",
        },
    )
    _register_artifacts(runtime, mission.mission_id, solve_job.job_id, solve_refs)

    score_artifact = artifact_dir / "score.json"
    score_artifact.write_text('{"score": 1}', encoding="utf-8")
    score_job = runtime.create_job(
        mission.mission_id,
        BRD_CHANNEL_SCORE_CAPABILITY,
        "attested-score",
        {
            "touchstone_path": adapter_result.touchstone_path,
            "tdr_path": adapter_result.tdr_path,
            "artifact_dir": str(artifact_dir),
        },
    )
    store.complete_job(
        score_job.job_id,
        {
            "status": "succeeded",
            "evidence_summary": {
                "raw_sparameters": "artifact_only",
                "raw_tdr": "artifact_only",
            },
        },
        [str(score_artifact)],
    )
    _register_artifacts(
        runtime,
        mission.mission_id,
        score_job.job_id,
        [str(score_artifact)],
    )

    template = load_graph_template("brd_real_solve_evidence")
    graph = store.create_graph_run(
        GraphRunRecord.create(
            str(uuid4()),
            mission.mission_id,
            template.template_id,
            template.version,
            mission.plan_version,
            template_snapshot=template.to_json_dict(),
        )
    )
    store.bind_graph_node_job(graph.graph_run_id, "real_solve_worker", 1, solve_job.job_id)
    store.bind_graph_node_job(graph.graph_run_id, "channel_score_worker", 1, score_job.job_id)
    scorecard = store.create_node_run(
        NodeRunRecord.create(
            str(uuid4()),
            graph.graph_run_id,
            mission.mission_id,
            "real_solve_scorecard",
            "scorecard",
            "program",
            1,
            {},
        )
    )
    store.complete_node_run(
        scorecard.node_run_id,
        NodeRunStatus.SUCCEEDED,
        {"status": "passed", "checks": []},
        [],
    )
    store.update_graph_run_status(graph.graph_run_id, GraphRunStatus.RUNNING)
    if complete:
        store.update_graph_run_status(graph.graph_run_id, GraphRunStatus.SUCCEEDED)
        store.update_mission_state(mission.mission_id, MissionState.EVALUATING)
        store.update_mission_state(mission.mission_id, MissionState.COMPLETED)
    else:
        store.update_graph_run_status(graph.graph_run_id, GraphRunStatus.FAILED)
    return runtime, graph.graph_run_id, Path(adapter_result.touchstone_path)


def _fake_graph(tmp_path: Path, monkeypatch) -> tuple[AgentRuntime, str]:
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))
    store = SQLiteMissionStore(tmp_path / "fake.db")
    registry = InMemoryWorkerRegistry(
        harness=LocalProcessHarness(HarnessWorkspacePolicy(tmp_path / "harness")),
        allow_real_aedt=True,
    )
    registry.register_process(
        BRD_REAL_SOLVE_CAPABILITY,
        "tests.fixtures.fake_real_solve:run_fake_real_solve_worker",
        resource_classes=("license", "aedt"),
        allowed_env=("PYTHONPATH",),
        requires_real_aedt=True,
    )
    registry.register(BRD_CHANNEL_SCORE_CAPABILITY, run_brd_channel_score_worker)
    runtime = AgentRuntime(store, registry=registry)
    project = tmp_path / "fake-approved.aedt"
    project.write_text("approved", encoding="utf-8")
    initial = build_brd_real_solve_job_input(
        project_path=project,
        setup_name="Setup1",
        sweep_name="Sweep1",
        tdr_expression="TDRZt(P1,P1)",
        expected_port_count=2,
        aedt={"version": "2026.1", "non_graphical": True},
    )
    mission = runtime.create_mission("fake real solve", [], [])
    runtime.create_job(
        mission.mission_id,
        BRD_REAL_SOLVE_CAPABILITY,
        "fake-solve",
        initial,
    )
    waiting = run_graph(
        runtime,
        mission.mission_id,
        load_graph_template("brd_real_solve_evidence"),
        initial_payload=initial,
    )
    approval_run = next(
        run for run in waiting["node_runs"] if run["node_id"] == "model_approval_gate"
    )
    ApprovalService(store).approve(
        approval_run["output_payload"]["approval_id"],
        "approve",
    )
    completed = resume_graph(runtime, waiting["graph_run"]["graph_run_id"])
    assert completed["status"] == "succeeded"
    return runtime, waiting["graph_run"]["graph_run_id"]


def test_real_acceptance_rejects_fake_requires_real_aedt_graph(tmp_path, monkeypatch):
    runtime, graph_run_id = _fake_graph(tmp_path, monkeypatch)

    report = validate_real_aedt_acceptance(runtime, graph_run_id)

    assert report["status"] == "failed"
    assert "real_execution_attestation_verified" in report["failed_check_ids"]
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["latest_solve_attempt_local_process_succeeded"]["passed"] is True
    assert checks["score_mission.solve_manifest_verified"]["passed"] is True


def test_real_acceptance_passes_adapter_attested_manifest_without_aedt(tmp_path):
    runtime, graph_run_id, _ = _attested_graph(tmp_path)

    report = validate_real_aedt_acceptance(runtime, graph_run_id)

    assert report["status"] == "passed"
    assert report["failed_check_ids"] == []
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["real_execution_attestation_verified"]["passed"] is True
    assert checks["required_solve_outputs_verified"]["passed"] is True


def test_real_acceptance_rejects_tampered_output(tmp_path):
    runtime, graph_run_id, touchstone = _attested_graph(tmp_path)
    touchstone.write_text("tampered", encoding="utf-8")

    report = validate_real_aedt_acceptance(runtime, graph_run_id)

    assert report["status"] == "failed"
    assert "required_solve_outputs_verified" in report["failed_check_ids"]
    assert "score_mission.solve_manifest_verified" in report["failed_check_ids"]


def test_real_acceptance_rejects_graph_and_mission_state(tmp_path):
    runtime, graph_run_id, _ = _attested_graph(tmp_path, complete=False)

    report = validate_real_aedt_acceptance(runtime, graph_run_id)

    assert report["status"] == "failed"
    assert "graph_succeeded" in report["failed_check_ids"]
    assert "mission_completed" in report["failed_check_ids"]


def test_real_acceptance_cli_exit_codes_and_report_file(tmp_path):
    _, graph_run_id, _ = _attested_graph(tmp_path)
    output = tmp_path / "reports" / "acceptance.json"
    command = [
        sys.executable,
        "-m",
        "aedt_agent.agent.cli",
        "--db",
        str(tmp_path / "mission.db"),
        "mission",
        "real-acceptance",
        "--graph-run-id",
        graph_run_id,
        "--output",
        str(output),
    ]

    passed = subprocess.run(command, cwd=Path.cwd(), text=True, capture_output=True, check=False)
    missing = subprocess.run(
        [*command[:8], "missing-graph"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert passed.returncode == 0
    assert passed.stderr == ""
    assert json.loads(passed.stdout)["status"] == "passed"
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "passed"
    assert missing.returncode == 2
    assert missing.stderr == ""
    assert json.loads(missing.stdout)["status"] == "failed"
