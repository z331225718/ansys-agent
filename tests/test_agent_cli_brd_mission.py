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


def test_cli_runs_brd_local_cut_mission_to_model_review(tmp_path):
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
        "--signal-net",
        "56G_TX0_N",
        "--reference-net",
        "GND",
        "--bbox",
        "mil,1,2,3,4",
        "--criterion",
        "s21_db_at_56g>=-8",
    )
    mission_id = json.loads(created.stdout)["mission_id"]

    ran = _run(tmp_path, "mission", "run", "--mission-id", mission_id)
    status = _run(tmp_path, "mission", "status", "--mission-id", mission_id)

    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout)["status"] == "succeeded"
    payload = json.loads(status.stdout)
    assert payload["state"] == "evaluating"
    assert payload["jobs"][0]["capability"] == "brd.local_cut.build"
    assert payload["jobs"][0]["output_payload"]["evidence_summary"]["raw_sparameters"] == "artifact_only"
