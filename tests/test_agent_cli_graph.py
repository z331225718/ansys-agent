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


def test_cli_plan_prints_brd_graph_template(tmp_path):
    result = _run(tmp_path, "mission", "plan", "--template", "brd_local_cut_build")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["template_id"] == "brd_local_cut_build"
    assert payload["nodes"][2]["id"] == "real_build_worker"
    assert payload["nodes"][2]["capability"] == "brd.local_cut.build"


def test_cli_scorecard_reads_runtime_records(tmp_path):
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
    _run(tmp_path, "mission", "run", "--mission-id", mission_id)

    result = _run(tmp_path, "mission", "scorecard", "--mission-id", mission_id, "--template", "brd_local_cut_build")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["template_id"] == "brd_local_cut_build"


def test_cli_run_graph_executes_next_template_worker_and_scores(tmp_path):
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

    result = _run(tmp_path, "mission", "run-graph", "--mission-id", mission_id, "--template", "brd_local_cut_build")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["executed_node"]["id"] == "real_build_worker"
    assert payload["scorecard"]["status"] == "passed"
