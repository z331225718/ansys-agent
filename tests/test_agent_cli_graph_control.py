from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aedt_agent.agent.cli", "--db", str(tmp_path / "mission.db"), *args],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def _create_and_run_graph(tmp_path: Path) -> tuple[str, str]:
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
    run_graph = _run(tmp_path, "mission", "run-graph", "--mission-id", mission_id, "--template", "brd_local_cut_build")
    graph_run_id = json.loads(run_graph.stdout)["graph_run"]["graph_run_id"]
    return mission_id, graph_run_id


def test_cli_events_returns_ordered_mission_events(tmp_path):
    mission_id, _ = _create_and_run_graph(tmp_path)

    result = _run(tmp_path, "mission", "events", "--mission-id", mission_id)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [event["sequence"] for event in payload["events"]] == sorted(event["sequence"] for event in payload["events"])
    assert "graph_run_created" in {event["event_type"] for event in payload["events"]}


def test_cli_graph_runs_returns_graph_run_records(tmp_path):
    mission_id, graph_run_id = _create_and_run_graph(tmp_path)

    result = _run(tmp_path, "mission", "graph-runs", "--mission-id", mission_id)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["graph_runs"][0]["graph_run_id"] == graph_run_id
    assert payload["graph_runs"][0]["status"] == "waiting_approval"


def test_cli_node_runs_returns_node_run_records(tmp_path):
    _, graph_run_id = _create_and_run_graph(tmp_path)

    result = _run(tmp_path, "mission", "node-runs", "--graph-run-id", graph_run_id)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["node_runs"][0]["node_id"] == "planner"
    assert payload["node_runs"][-1]["node_id"] == "approval_gate"
    assert payload["node_runs"][-1]["status"] == "waiting_approval"


def test_cli_artifacts_returns_artifact_manifests(tmp_path):
    mission_id, _ = _create_and_run_graph(tmp_path)

    result = _run(tmp_path, "mission", "artifacts", "--mission-id", mission_id)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["artifacts"]
    assert {artifact["producer_kind"] for artifact in payload["artifacts"]} == {"job"}


def test_cli_evidence_returns_evidence_packages(tmp_path):
    mission_id, _ = _create_and_run_graph(tmp_path)

    result = _run(tmp_path, "mission", "evidence", "--mission-id", mission_id)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["evidence_packages"][0]["summary"]["scorecard"]["status"] == "passed"
    assert payload["evidence_packages"][0]["token_budget"]["raw_trace_policy"] == "artifact_only"
