from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.graph_runner import run_graph
from aedt_agent.agent.graph_template import graph_template_from_mapping
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.infrastructure.harness import (
    HarnessWorkspacePolicy,
    LocalProcessHarness,
    ResourceGate,
)


def test_local_process_worker_completes_planner_worker_scorecard_graph(tmp_path, monkeypatch):
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
    registry = InMemoryWorkerRegistry(harness=harness, heartbeat_interval_seconds=1)
    registry.register_process(
        "fake.evidence",
        "tests.fixtures.process_workers:evidence_worker",
        allowed_env=("PYTHONPATH",),
    )
    runtime = AgentRuntime(store, registry=registry)
    mission = runtime.create_mission("process graph", [], [])
    template = graph_template_from_mapping(
        {
            "id": "process-graph",
            "version": 1,
            "nodes": [
                {"id": "planner", "role": "planner", "kind": "llm"},
                {
                    "id": "worker",
                    "role": "worker",
                    "kind": "worker",
                    "capability": "fake.evidence",
                },
                {"id": "scorecard", "role": "scorecard", "kind": "program"},
            ],
            "edges": [
                {
                    "id": "planner-worker",
                    "from": "planner",
                    "to": "worker",
                    "on": "succeeded",
                },
                {
                    "id": "worker-scorecard",
                    "from": "worker",
                    "to": "scorecard",
                    "on": "succeeded",
                },
            ],
            "handoffs": {},
        }
    )

    report = run_graph(runtime, mission.mission_id, template, initial_payload={"value": 1})

    assert report["status"] == "succeeded"
    assert [run["node_id"] for run in report["node_runs"]] == [
        "planner",
        "worker",
        "scorecard",
    ]
    worker_job = next(job for job in report["jobs"] if job["capability"] == "fake.evidence")
    attempts = store.list_job_attempts(worker_job["job_id"])
    manifests = store.list_artifact_manifests(mission.mission_id)
    assert len(attempts) == 1
    assert attempts[0].metadata["harness_run_id"]
    assert Path(attempts[0].metadata["workspace"]).is_dir()
    assert {"request.json", "result.json", "stdout.log", "stderr.log"} <= {
        Path(manifest.path).name for manifest in manifests
    }
