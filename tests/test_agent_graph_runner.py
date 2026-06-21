from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aedt_agent.agent.graph_executors import GraphNodeExecutionResult
from aedt_agent.agent.graph_runner import _execute_wave, run_graph_once
from aedt_agent.agent.graph_template import GraphNode, load_graph_template, resolve_template_path
from aedt_agent.agent.mission import NodeRunStatus
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import BRD_LOCAL_CUT_BUILD_CAPABILITY, InMemoryWorkerRegistry, build_brd_local_cut_job_input, run_brd_local_cut_worker
from aedt_agent.infrastructure import SQLiteMissionStore


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


def test_run_graph_once_executes_full_template_and_stops_at_approval(tmp_path):
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("构建 local cut", [], [])
    job = runtime.create_job(mission.mission_id, BRD_LOCAL_CUT_BUILD_CAPABILITY, "build", _payload(tmp_path))

    report = run_graph_once(runtime, mission.mission_id, template, worker_id="graph")

    assert report["status"] == "waiting_approval"
    assert report["executed_node"]["id"] == "real_build_worker"
    assert report["executed_job"]["job_id"] == job.job_id
    assert report["executed_job"]["status"] == "succeeded"
    assert report["scorecard"]["status"] == "passed"
    assert [node_run["node_id"] for node_run in report["node_runs"]] == [
        "planner",
        "input_validator",
        "real_build_worker",
        "model_review_scorecard",
        "approval_gate",
    ]
    assert report["node_runs"][-1]["status"] == "waiting_approval"


def test_run_graph_once_rejects_job_outside_template(tmp_path):
    runtime = _runtime(tmp_path)
    template = load_graph_template(resolve_template_path("brd_local_cut_build"))
    mission = runtime.create_mission("未知 job", [], [])
    runtime.create_job(mission.mission_id, "unknown.capability", "unknown", {})

    with pytest.raises(ValueError, match="not allowed by graph template"):
        run_graph_once(runtime, mission.mission_id, template, worker_id="graph")


def test_worker_wave_uses_runtime_default_timeout_before_job_is_bound(monkeypatch):
    from aedt_agent.agent import graph_runner

    observed_timeouts: list[int] = []

    class FakeFuture:
        def result(self, timeout=None):
            observed_timeouts.append(timeout)
            return GraphNodeExecutionResult(
                NodeRunStatus.SUCCEEDED,
                "succeeded",
                {},
                [],
            )

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, *args, **kwargs):
            return FakeFuture()

    monkeypatch.setattr(graph_runner, "ThreadPoolExecutor", FakeExecutor)
    runtime = SimpleNamespace(
        default_job_timeout_seconds=1234,
        store=SimpleNamespace(get_graph_node_job=lambda *args: None),
        get_job=lambda job_id: None,
    )
    node = GraphNode("worker", "worker", "worker", capability="fake.worker")
    ready = [SimpleNamespace(node=node, input_payload={}, run_index=1)]

    results = _execute_wave(
        runtime,
        SimpleNamespace(graph_run_id="graph-1"),
        SimpleNamespace(),
        ready,
        [SimpleNamespace()],
        worker_id="graph",
        max_workers=1,
        registry=None,
    )

    assert results[0].status == NodeRunStatus.SUCCEEDED
    assert observed_timeouts == [1234]
