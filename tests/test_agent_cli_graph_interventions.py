from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aedt_agent.agent.event_replay import replay_graph_run
from aedt_agent.agent.graph_template import graph_template_from_mapping
from aedt_agent.agent.mission import (
    GraphHandoffRecord,
    GraphRunRecord,
    GraphRunStatus,
    MissionRecord,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.infrastructure import SQLiteMissionStore


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "aedt_agent.agent.cli",
            "--db",
            str(tmp_path / "mission.db"),
            *args,
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def _pending_graph(tmp_path: Path, graph_run_id: str = "cli-pending"):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    mission = store.create_mission(
        MissionRecord.create(f"mission-{graph_run_id}", "CLI intervention", [], [])
    )
    template = graph_template_from_mapping(
        {
            "id": "cli-intervention",
            "version": 1,
            "nodes": [
                {"id": "source", "role": "planner", "kind": "program", "handler": "source"},
                {
                    "id": "target",
                    "role": "worker",
                    "kind": "worker",
                    "capability": "fake.target",
                },
            ],
            "edges": [
                {"id": "source-target", "from": "source", "to": "target", "on": "succeeded"}
            ],
            "handoffs": {},
        }
    )
    graph_run = store.create_graph_run(
        GraphRunRecord.create(
            graph_run_id,
            mission.mission_id,
            template.template_id,
            template.version,
            mission.plan_version,
            template_snapshot=template.to_json_dict(),
        )
    )
    store.update_graph_run_status(graph_run_id, GraphRunStatus.RUNNING)
    source_run = store.create_node_run(
        NodeRunRecord.create(
            f"source-{graph_run_id}",
            graph_run_id,
            mission.mission_id,
            "source",
            "planner",
            "program",
            1,
            {},
        )
    )
    store.complete_node_run(
        source_run.node_run_id,
        NodeRunStatus.SUCCEEDED,
        {"seed": 1},
        [],
        edge_decision="succeeded",
    )
    store.create_graph_handoff(
        GraphHandoffRecord.create(
            f"handoff-{graph_run_id}",
            graph_run_id,
            mission.mission_id,
            "source-target",
            source_run.node_run_id,
            "source",
            "target",
            "succeeded",
            {"seed": 1},
        )
    )
    store.update_graph_run_status(
        graph_run_id,
        GraphRunStatus.RUNNING,
        current_node_id="target",
    )
    cursor = replay_graph_run(store, graph_run_id)["event_cursor"]
    return store, graph_run, cursor


def _failed_graph(tmp_path: Path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    mission = store.create_mission(
        MissionRecord.create("mission-cli-retry", "CLI retry", [], [])
    )
    template = graph_template_from_mapping(
        {
            "id": "cli-retry",
            "version": 1,
            "nodes": [
                {
                    "id": "target",
                    "role": "worker",
                    "kind": "worker",
                    "capability": "fake.target",
                },
                {"id": "failure", "role": "aggregate", "kind": "program", "handler": "sink"},
            ],
            "edges": [
                {"id": "target-failure", "from": "target", "to": "failure", "on": "failed"}
            ],
            "handoffs": {},
        }
    )
    graph_run = store.create_graph_run(
        GraphRunRecord.create(
            "cli-retry",
            mission.mission_id,
            template.template_id,
            template.version,
            mission.plan_version,
            template_snapshot=template.to_json_dict(),
        )
    )
    store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.RUNNING)
    failed_run = store.create_node_run(
        NodeRunRecord.create(
            "cli-failed-target",
            graph_run.graph_run_id,
            mission.mission_id,
            "target",
            "worker",
            "worker",
            1,
            {"seed": 7},
        )
    )
    store.complete_node_run(
        failed_run.node_run_id,
        NodeRunStatus.FAILED,
        {},
        [],
        edge_decision="failed",
        error={"error_class": "worker_crash", "message": "boom"},
    )
    store.create_graph_handoff(
        GraphHandoffRecord.create(
            "cli-failure-handoff",
            graph_run.graph_run_id,
            mission.mission_id,
            "target-failure",
            failed_run.node_run_id,
            "target",
            "failure",
            "failed",
            {},
        )
    )
    store.update_graph_run_status(
        graph_run.graph_run_id,
        GraphRunStatus.RUNNING,
        current_node_id="failure",
    )
    cursor = replay_graph_run(store, graph_run.graph_run_id)["event_cursor"]
    return store, graph_run, cursor


def test_cli_cancel_branch_outputs_json_and_does_not_run_worker(tmp_path):
    store, graph_run, cursor = _pending_graph(tmp_path)

    result = _run(
        tmp_path,
        "mission",
        "intervene",
        "--graph-run-id",
        graph_run.graph_run_id,
        "--action",
        "cancel-branch",
        "--node-id",
        "target",
        "--expected-event-cursor",
        str(cursor),
        "--idempotency-key",
        "cli-cancel-target",
        "--reason",
        "operator canceled pending branch",
    )

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["intervention"]["status"] == "applied"
    target_runs = [
        row for row in store.list_node_runs(graph_run.graph_run_id) if row.node_id == "target"
    ]
    assert len(target_runs) == 1
    assert target_runs[0].status == NodeRunStatus.SKIPPED
    assert store.list_graph_bound_job_ids(graph_run.graph_run_id) == []


def test_cli_retry_node_only_prepares_state(tmp_path):
    store, graph_run, cursor = _failed_graph(tmp_path)

    result = _run(
        tmp_path,
        "mission",
        "intervene",
        "--graph-run-id",
        graph_run.graph_run_id,
        "--action",
        "retry-node",
        "--node-id",
        "target",
        "--expected-event-cursor",
        str(cursor),
        "--idempotency-key",
        "cli-retry-target",
        "--reason",
        "retry after transient failure",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["result"]["failed_node_run_id"] == "cli-failed-target"
    assert len(store.list_node_runs(graph_run.graph_run_id)) == 1
    assert store.list_graph_bound_job_ids(graph_run.graph_run_id) == []
    failure_handoff = store.list_graph_handoffs(graph_run.graph_run_id)[0]
    assert failure_handoff.status.value == "consumed"
    target = next(
        node
        for node in store.get_graph_run(graph_run.graph_run_id).template_snapshot["nodes"]
        if node["id"] == "target"
    )
    assert target["max_runs"] == 2


def test_cli_stale_cursor_returns_structured_nonzero_error_without_traceback(tmp_path):
    store, graph_run, cursor = _pending_graph(tmp_path)

    result = _run(
        tmp_path,
        "mission",
        "intervene",
        "--graph-run-id",
        graph_run.graph_run_id,
        "--action",
        "cancel-branch",
        "--node-id",
        "target",
        "--expected-event-cursor",
        str(cursor - 1),
        "--idempotency-key",
        "cli-stale-target",
        "--reason",
        "stale operator view",
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "stale_event_cursor"
    assert payload["error"]["details"]["expected"] == cursor - 1
    assert payload["error"]["details"]["actual"] == cursor
    handoff = store.list_graph_handoffs(graph_run.graph_run_id)[0]
    assert handoff.status.value == "pending"
    assert all(row.node_id != "target" for row in store.list_node_runs(graph_run.graph_run_id))
