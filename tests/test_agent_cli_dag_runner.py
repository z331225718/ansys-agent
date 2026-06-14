from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aedt_agent.agent.graph_runner import create_graph_run
from aedt_agent.agent.graph_template import graph_template_from_mapping
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aedt_agent.agent.cli", "--db", str(tmp_path / "mission.db"), *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def _create_waiting_local_cut_graph(tmp_path: Path) -> dict:
    layout_file = tmp_path / "case.brd"
    layout_file.write_text("brd", encoding="utf-8")
    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "构建 local cut",
        "--brd-local-cut",
        "--layout-file",
        str(layout_file),
        "--signal-net",
        "56G_TX0_P",
        "--reference-net",
        "GND",
        "--bbox",
        "mil,1,2,3,4",
    )
    mission_id = json.loads(created.stdout)["mission_id"]
    result = _run(
        tmp_path,
        "mission",
        "run-graph",
        "--mission-id",
        mission_id,
        "--template",
        "brd_local_cut_build",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_cli_graph_status_lists_persisted_handoffs(tmp_path):
    waiting = _create_waiting_local_cut_graph(tmp_path)

    result = _run(
        tmp_path,
        "mission",
        "graph-status",
        "--graph-run-id",
        waiting["graph_run"]["graph_run_id"],
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "waiting_approval"
    assert len(payload["handoffs"]) == 4
    assert payload["handoffs"][0]["status"] == "consumed"


def test_cli_approve_and_resume_graph_finishes_same_graph_run(tmp_path):
    waiting = _create_waiting_local_cut_graph(tmp_path)
    graph_run_id = waiting["graph_run"]["graph_run_id"]
    approval_id = waiting["node_runs"][-1]["output_payload"]["approval_id"]

    approved = _run(
        tmp_path,
        "mission",
        "approve",
        "--approval-id",
        approval_id,
        "--option-id",
        "approve",
        "--comment",
        "模型可接受",
    )
    resumed = _run(
        tmp_path,
        "mission",
        "resume-graph",
        "--graph-run-id",
        graph_run_id,
    )

    assert approved.returncode == 0, approved.stderr
    assert json.loads(approved.stdout)["decision"] == "approved"
    assert resumed.returncode == 0, resumed.stderr
    payload = json.loads(resumed.stdout)
    assert payload["status"] == "succeeded"
    assert payload["graph_run"]["graph_run_id"] == graph_run_id
    assert payload["node_runs"][-1]["status"] == "succeeded"


def test_cli_advance_graph_executes_exactly_one_wave(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("two waves", [], [])
    template = graph_template_from_mapping(
        {
            "id": "two_waves",
            "version": 1,
            "nodes": [
                {"id": "source", "role": "planner", "kind": "llm"},
                {"id": "validator", "role": "validator", "kind": "program"},
            ],
            "edges": [
                {"id": "source-validator", "from": "source", "to": "validator", "on": "succeeded"}
            ],
            "handoffs": {},
        }
    )
    graph_run = create_graph_run(runtime, mission.mission_id, template, initial_payload={"value": 1})

    result = _run(
        tmp_path,
        "mission",
        "advance-graph",
        "--graph-run-id",
        graph_run.graph_run_id,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "running"
    assert payload["graph_run"]["step_count"] == 1
    assert [run["node_id"] for run in payload["node_runs"]] == ["source"]
