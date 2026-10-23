from __future__ import annotations

import os
from pathlib import Path

import pytest

from aedt_agent.agent.graph_runner import run_graph_once
from aedt_agent.agent.graph_template import load_graph_template, resolve_template_path
from aedt_agent.agent.mission import GraphRunRecord, GraphRunStatus
from aedt_agent.agent.mission.contracts import ArtifactManifest
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import (
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    InMemoryWorkerRegistry,
    build_brd_local_cut_job_input,
    run_brd_local_cut_worker,
)
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.operator_api import OperatorService
from aedt_agent.operator_api import OperatorContractError


def _runtime(tmp_path: Path) -> AgentRuntime:
    registry = InMemoryWorkerRegistry()
    registry.register(BRD_LOCAL_CUT_BUILD_CAPABILITY, run_brd_local_cut_worker)
    return AgentRuntime(SQLiteMissionStore(tmp_path / "mission.db"), registry=registry)


def _payload(tmp_path: Path) -> dict:
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    return build_brd_local_cut_job_input(
        layout_file=layout_file,
        signal_nets=["56G_TX0_P", "56G_TX0_N"],
        reference_nets=["GND"],
        local_cut_region={"type": "bbox", "unit": "mil", "x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4},
        artifact_dir=tmp_path / "artifacts",
    )


def test_graph_status_returns_bounded_dto_without_node_payloads(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("build local cut", [], [])
    runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))
    report = run_graph_once(runtime, mission.mission_id, template, worker_id="graph")

    response = OperatorService(runtime).graph_status(report["graph_run"]["graph_run_id"])

    assert response["resource"]["mission_id"] == mission.mission_id
    assert response["data"]["status"] == "waiting_approval"
    assert response["data"]["nodes"]
    assert all("output_payload" not in node and "input_payload" not in node for node in response["data"]["nodes"])
    assert response["resource"]["state_digest"].startswith("sha256:")


def test_handshake_advertises_only_read_only_operations(tmp_path: Path) -> None:
    response = OperatorService(_runtime(tmp_path)).handshake()

    assert response["schema_version"] == "1.0"
    assert response["schema_bundle_digest"].startswith("sha256:")
    assert response["runtime_pid"] == os.getpid()
    assert response["runtime_argv_digest"].startswith("sha256:")
    assert "graph.status" in response["capabilities"]
    assert "mission.start" not in response["capabilities"]
    assert "graph.advance" not in response["capabilities"]


def test_template_catalog_is_allowlisted_and_digest_bound(tmp_path: Path) -> None:
    catalog = OperatorService(_runtime(tmp_path)).template_catalog()

    assert {item["template_id"] for item in catalog["templates"]} == {
        "brd_local_cut_build",
        "brd_channel_optimize",
        "brd_before_after_compare",
        "brd_real_solve_evidence",
        "brd_multi_channel_demo",
    }
    assert all(item["digest"].startswith("sha256:") for item in catalog["templates"])
    assert all("system_prompt" not in item for item in catalog["templates"])


def test_preflight_returns_only_input_shape_and_never_starts_a_mission(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    response = OperatorService(runtime).system_preflight(
        template_id="brd_local_cut_build",
        execution_profile="safe-recorded",
        inputs={"layout_file": "C:/sensitive/case.brd", "raw_s_parameters": [1.0, 2.0, 3.0]},
    )

    data = response["data"]
    assert data["safe_to_start"] is False
    assert data["requires_trusted_controller"] is True
    assert data["preflight_snapshot_id"].startswith("sha256:")
    assert "C:/sensitive/case.brd" not in str(data)
    assert "raw_s_parameters" in str(data)
    assert runtime.store.list_missions(limit=10) == []


def test_replay_delta_is_cursor_based_and_does_not_expose_payloads(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("build local cut", [], [])
    runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))
    report = run_graph_once(runtime, mission.mission_id, template, worker_id="graph")

    response = OperatorService(runtime).replay_delta(report["graph_run"]["graph_run_id"], limit=100)

    assert response["data"]["events"]
    assert all("payload" not in event for event in response["data"]["events"])
    assert response["data"]["next_cursor"].isdecimal()


def test_graph_visualize_mermaid_uses_snapshot_without_payload(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("build local cut", [], [])
    runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))
    report = run_graph_once(runtime, mission.mission_id, template, worker_id="graph")

    response = OperatorService(runtime).graph_visualize(report["graph_run"]["graph_run_id"], format="mermaid")

    assert response["data"]["content"].startswith("flowchart TD")
    assert "layout_file" not in response["data"]["content"]


def test_pending_approvals_are_summarized_and_prepare_never_submits(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("build local cut", [], [])
    runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))
    report = run_graph_once(runtime, mission.mission_id, template, worker_id="graph")
    service = OperatorService(runtime)

    pending = service.approval_get_pending(graph_run_id=report["graph_run"]["graph_run_id"])["data"]["approvals"]

    assert len(pending) == 1
    prepared = service.approval_prepare_decision(pending[0]["approval_id"], decision_intent="approve")
    assert prepared["data"]["requires_trusted_controller"] is True
    assert runtime.store.get_approval(pending[0]["approval_id"]).decision.value == "pending"


def test_artifact_query_returns_metadata_without_paths_or_payloads(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("inspect artifact", [], [])
    runtime.store.create_artifact_manifest(
        ArtifactManifest.create(
            "artifact-001",
            mission.mission_id,
            "node",
            "node-run-001",
            "C:/sensitive/workspace/raw-curve.s2p",
            "touchstone",
            "f" * 64,
            1234,
            metadata={"raw_curve": [1, 2, 3]},
        )
    )

    response = OperatorService(runtime).artifact_query_bounded(
        mission.mission_id,
        query="touchstone",
        limit=10,
    )

    artifact = response["data"]["artifacts"][0]
    assert artifact["artifact_id"] == "artifact-001"
    assert artifact["kind"] == "touchstone"
    assert artifact["sha256"] == "f" * 64
    assert "path" not in artifact
    assert "metadata" not in artifact


def test_graph_status_digest_changes_when_observable_state_changes(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("build local cut", [], [])
    runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))
    report = run_graph_once(runtime, mission.mission_id, template, worker_id="graph")
    service = OperatorService(runtime)

    first = service.graph_status(report["graph_run"]["graph_run_id"])
    runtime.store.update_graph_run_status(report["graph_run"]["graph_run_id"], GraphRunStatus.CANCELED)
    second = service.graph_status(report["graph_run"]["graph_run_id"])

    assert first["resource"]["state_digest"] != second["resource"]["state_digest"]


def test_replay_delta_rejects_a_future_cursor(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    mission = runtime.create_mission("replay cursor", [], [])
    graph = runtime.store.create_graph_run(
        GraphRunRecord.create("graph-cursor-001", mission.mission_id, "brd_local_cut_build", 1, 1, template_snapshot={})
    )
    with pytest.raises(OperatorContractError, match="outside the graph replay range") as error:
        OperatorService(runtime).replay_delta(graph.graph_run_id, after_cursor="999999", limit=10)
    assert error.value.code == "invalid_cursor"


def test_pending_approvals_rejects_mismatched_mission_and_graph(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    owner = runtime.create_mission("owner", [], [])
    other = runtime.create_mission("other", [], [])
    graph = runtime.store.create_graph_run(
        GraphRunRecord.create("graph-owner-001", owner.mission_id, "brd_local_cut_build", 1, 1, template_snapshot={})
    )
    with pytest.raises(OperatorContractError, match="does not own graph_run_id"):
        OperatorService(runtime).approval_get_pending(mission_id=other.mission_id, graph_run_id=graph.graph_run_id)
