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


def _write_channel(touchstone: Path, tdr: Path, reflection: float, peak: float) -> None:
    touchstone.write_text(
        "# GHz S MA R 50\n"
        "0 0.05 0 0.9 0 0.9 0 0.05 0\n"
        f"18 {reflection} 0 0.8 0 0.8 0 0.05 0\n"
        "67 0.04 0 0.7 0 0.7 0 0.04 0\n",
        encoding="utf-8",
    )
    tdr.write_text(f"time_ps,impedance_ohm\n0,100\n10,104\n20,{peak}\n30,101\n", encoding="utf-8")


def _create_action_mission(tmp_path: Path, *, improved: bool) -> tuple[str, dict]:
    before_s = tmp_path / "before.s2p"
    before_t = tmp_path / "before.csv"
    after_s = tmp_path / "after.s2p"
    after_t = tmp_path / "after.csv"
    if improved:
        _write_channel(before_s, before_t, 0.4, 112.0)
        _write_channel(after_s, after_t, 0.08, 103.0)
    else:
        _write_channel(before_s, before_t, 0.08, 103.0)
        _write_channel(after_s, after_t, 0.4, 112.0)
    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "执行受控 BRD void 调整",
        "--brd-recorded-void-action",
        "--before-touchstone",
        str(before_s),
        "--before-tdr",
        str(before_t),
        "--after-touchstone",
        str(after_s),
        "--after-tdr",
        str(after_t),
        "--action-layer",
        "ART03",
        "--action-region",
        "via-1",
        "--action-shape",
        "circle",
        "--action-variable",
        "r_cut_ART03",
        "--old-value-mil",
        "13",
        "--new-value-mil",
        "14",
    )
    assert created.returncode == 0, created.stderr
    mission_id = json.loads(created.stdout)["mission_id"]
    actions = _run(tmp_path, "mission", "actions", "--mission-id", mission_id)
    assert actions.returncode == 0, actions.stderr
    action = json.loads(actions.stdout)["actions"][0]
    return mission_id, action


def _approve(tmp_path: Path, action: dict) -> None:
    result = _run(
        tmp_path,
        "mission",
        "approve-action",
        "--approval-id",
        action["approval_id"],
        "--action-id",
        action["action_id"],
        "--action-digest",
        action["digest"],
        "--comment",
        "批准受控调整",
    )
    assert result.returncode == 0, result.stderr


def test_cli_recorded_void_action_improvement_is_accepted(tmp_path):
    mission_id, action = _create_action_mission(tmp_path, improved=True)
    assert action["status"] == "waiting_approval"
    _approve(tmp_path, action)

    result = _run(
        tmp_path,
        "mission",
        "run-graph",
        "--mission-id",
        mission_id,
        "--template",
        "brd_recorded_void_action",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    worker_run = [run for run in payload["node_runs"] if run["node_id"] == "recorded_action_worker"][0]
    assert worker_run["output_payload"]["decision"] == "accept"
    status = _run(tmp_path, "mission", "action-status", "--action-id", action["action_id"])
    status_payload = json.loads(status.stdout)
    assert status_payload["action"]["status"] == "accepted"
    assert status_payload["executions"][0]["result"]["accepted_artifact_refs"]
    assert "0 0.05" not in str(worker_run["output_payload"]["evidence_summary"])
    events = json.loads(_run(tmp_path, "mission", "events", "--mission-id", mission_id).stdout)["events"]
    artifacts = json.loads(_run(tmp_path, "mission", "artifacts", "--mission-id", mission_id).stdout)["artifacts"]
    evidence = json.loads(_run(tmp_path, "mission", "evidence", "--mission-id", mission_id).stdout)["evidence_packages"]
    event_types = {event["event_type"] for event in events}
    assert "checkpoint_created" in event_types
    assert "action_execution_created" in event_types
    assert len(artifacts) == 4
    assert {artifact["producer_kind"] for artifact in artifacts} == {"job"}
    assert evidence[0]["summary"]["scorecard"]["status"] == "passed"


def test_cli_recorded_void_action_regression_is_rolled_back(tmp_path):
    mission_id, action = _create_action_mission(tmp_path, improved=False)
    _approve(tmp_path, action)

    result = _run(
        tmp_path,
        "mission",
        "run-graph",
        "--mission-id",
        mission_id,
        "--template",
        "brd_recorded_void_action",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    worker_run = [run for run in payload["node_runs"] if run["node_id"] == "recorded_action_worker"][0]
    assert worker_run["output_payload"]["decision"] == "rollback"
    status = json.loads(_run(tmp_path, "mission", "action-status", "--action-id", action["action_id"]).stdout)
    assert status["action"]["status"] == "rolled_back"
