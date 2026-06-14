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


def _write_touchstone(path: Path) -> None:
    path.write_text(
        "# GHz S MA R 50\n"
        "0.00 0.05 0 0.90 0 0.90 0 0.05 0\n"
        "18.00 0.45 0 0.80 0 0.80 0 0.05 0\n"
        "67.00 0.04 0 0.70 0 0.70 0 0.04 0\n",
        encoding="utf-8",
    )


def _write_tdr(path: Path) -> None:
    path.write_text("time_ps,impedance_ohm\n0,100\n10,104\n20,111\n30,101\n", encoding="utf-8")


def test_cli_creates_brd_channel_score_mission_and_runs_graph(tmp_path):
    touchstone = tmp_path / "channel.s2p"
    tdr = tmp_path / "channel_tdr.csv"
    _write_touchstone(touchstone)
    _write_tdr(tdr)

    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "评分 BRD 通道",
        "--brd-channel-score",
        "--touchstone",
        str(touchstone),
        "--tdr",
        str(tdr),
        "--artifact-dir",
        str(tmp_path / "artifacts"),
        "--frequency-stop-ghz",
        "67",
        "--rl-target-db",
        "-20",
    )

    assert created.returncode == 0, created.stderr
    mission_id = json.loads(created.stdout)["mission_id"]
    status = _run(tmp_path, "mission", "status", "--mission-id", mission_id)
    status_payload = json.loads(status.stdout)
    assert status_payload["jobs"][0]["capability"] == "brd.channel.score"

    plan = _run(tmp_path, "mission", "plan", "--template", "brd_local_cut_solve_evidence")
    assert plan.returncode == 0, plan.stderr
    plan_payload = json.loads(plan.stdout)
    assert any(node["capability"] == "brd.channel.score" for node in plan_payload["nodes"])

    result = _run(tmp_path, "mission", "run-graph", "--mission-id", mission_id, "--template", "brd_local_cut_solve_evidence")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["executed_node"]["id"] == "channel_score_worker"
    assert payload["executed_job"]["output_payload"]["evidence_summary"]["raw_sparameters"] == "artifact_only"

    graph_runs = json.loads(_run(tmp_path, "mission", "graph-runs", "--mission-id", mission_id).stdout)
    evidence = json.loads(_run(tmp_path, "mission", "evidence", "--mission-id", mission_id).stdout)
    assert graph_runs["graph_runs"][0]["status"] == "succeeded"
    assert evidence["evidence_packages"][0]["summary"]["scorecard"]["status"] == "passed"
