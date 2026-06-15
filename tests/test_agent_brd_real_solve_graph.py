from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_runner import resume_graph, run_graph
from aedt_agent.agent.graph_template import load_graph_template
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.scorecard import score_mission
from aedt_agent.agent.workers import (
    BRD_CHANNEL_SCORE_CAPABILITY,
    BRD_REAL_SOLVE_CAPABILITY,
    InMemoryWorkerRegistry,
    build_brd_real_solve_job_input,
    run_brd_channel_score_worker,
)
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.infrastructure.harness import (
    HarnessWorkspacePolicy,
    LocalProcessHarness,
    ResourceGate,
)


def _runtime(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path.cwd()))
    store = SQLiteMissionStore(tmp_path / "mission.db")
    harness = LocalProcessHarness(
        HarnessWorkspacePolicy(tmp_path / "harness"),
        resource_gate=ResourceGate(
            max_concurrent_cpu=2,
            max_concurrent_aedt=1,
            max_concurrent_license_jobs=1,
        ),
        heartbeat_timeout_seconds=30,
        termination_grace_seconds=1,
    )
    registry = InMemoryWorkerRegistry(
        harness=harness,
        heartbeat_interval_seconds=1,
        allow_real_aedt=True,
    )
    registry.register_process(
        BRD_REAL_SOLVE_CAPABILITY,
        (
            "tests.fixtures.fake_real_solve:"
            "run_fake_real_solve_worker"
        ),
        resource_classes=("license", "aedt"),
        allowed_env=("PYTHONPATH",),
        requires_real_aedt=True,
    )
    registry.register(
        BRD_CHANNEL_SCORE_CAPABILITY,
        run_brd_channel_score_worker,
    )
    return AgentRuntime(store, registry=registry)


def _initial_payload(tmp_path: Path) -> dict:
    project = tmp_path / "approved.aedt"
    project.write_text("approved project", encoding="utf-8")
    return build_brd_real_solve_job_input(
        project_path=project,
        setup_name="Setup1",
        sweep_name="Sweep1",
        tdr_expression="TDRZt(P1,P1)",
        expected_port_count=2,
        frequency_start_ghz=0.0,
        frequency_stop_ghz=67.0,
        rl_target_db=-20.0,
        tdr_target_ohm=100.0,
        aedt={"version": "2026.1", "non_graphical": True},
    )


def test_real_solve_graph_resumes_same_run_and_scores_artifacts(
    tmp_path,
    monkeypatch,
):
    runtime = _runtime(tmp_path, monkeypatch)
    mission = runtime.create_mission(
        "solve approved local cut",
        [],
        [],
    )
    initial = _initial_payload(tmp_path)
    runtime.create_job(
        mission.mission_id,
        BRD_REAL_SOLVE_CAPABILITY,
        "real-solve",
        initial,
        timeout_seconds=30,
        retry_limit=0,
    )
    template = load_graph_template("brd_real_solve_evidence")

    waiting = run_graph(
        runtime,
        mission.mission_id,
        template,
        initial_payload=initial,
    )
    graph_run_id = waiting["graph_run"]["graph_run_id"]
    approval_run = next(
        run
        for run in waiting["node_runs"]
        if run["node_id"] == "model_approval_gate"
    )
    ApprovalService(runtime.store).approve(
        approval_run["output_payload"]["approval_id"],
        "approve",
    )
    completed = resume_graph(runtime, graph_run_id)

    assert waiting["status"] == "waiting_approval"
    assert completed["status"] == "succeeded"
    assert completed["graph_run"]["graph_run_id"] == graph_run_id
    solve_run = next(
        run
        for run in completed["node_runs"]
        if run["node_id"] == "real_solve_worker"
    )
    score_run = next(
        run
        for run in completed["node_runs"]
        if run["node_id"] == "channel_score_worker"
    )
    scorecard_run = next(
        run
        for run in completed["node_runs"]
        if run["node_id"] == "real_solve_scorecard"
    )
    assert (
        solve_run["output_payload"]["solve_summary"][
            "raw_sparameters"
        ]
        == "artifact_only"
    )
    assert (
        score_run["output_payload"]["evidence_summary"]["raw_tdr"]
        == "artifact_only"
    )
    checks = {
        check["id"]: check
        for check in scorecard_run["output_payload"]["checks"]
    }
    assert checks["model_approval_resolved"]["passed"] is True
    assert checks["solve_used_local_process"]["passed"] is True
    assert checks["solve_manifest_verified"]["passed"] is True
    assert checks["channel_score_bound_to_solve"]["passed"] is True
    assert len(
        runtime.store.list_evidence_packages(mission.mission_id)
    ) >= 3

    Path(solve_run["output_payload"]["touchstone_path"]).write_text(
        "tampered",
        encoding="utf-8",
    )
    tampered = score_mission(
        runtime,
        mission.mission_id,
        template_id="brd_real_solve_evidence",
    )
    tampered_checks = {
        check["id"]: check for check in tampered["checks"]
    }
    assert tampered["status"] == "failed"
    assert (
        tampered_checks["solve_manifest_verified"]["passed"]
        is False
    )
