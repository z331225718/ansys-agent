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


def _create_local_cut_mission(tmp_path: Path, *, adapter_mode: str = "deterministic") -> str:
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
        "--adapter-mode",
        adapter_mode,
    )
    assert created.returncode == 0, created.stderr
    return json.loads(created.stdout)["mission_id"]


def _create_real_solve_mission(tmp_path: Path) -> str:
    project = tmp_path / "approved.aedt"
    project.write_text("approved project", encoding="utf-8")
    created = _run(
        tmp_path,
        "mission",
        "create",
        "--goal",
        "求解 approved local cut",
        "--brd-real-solve",
        "--project",
        str(project),
        "--tdr-expression",
        "TDRZt(P1,P1)",
    )
    assert created.returncode == 0, created.stderr
    return json.loads(created.stdout)["mission_id"]


def test_cli_advance_completes_one_job_mission(tmp_path):
    mission_id = _create_local_cut_mission(tmp_path)

    result = _run(tmp_path, "mission", "advance", "--mission-id", mission_id)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"]["decision"] == "completed"
    assert payload["mission"]["state"] == "completed"
    assert payload["mission"]["final_outcome"]["code"] == "completed"
    assert payload["loop"]["iteration_count"] == 1


def test_cli_loop_status_reports_profile_and_usage(tmp_path):
    mission_id = _create_local_cut_mission(tmp_path)

    result = _run(tmp_path, "mission", "loop-status", "--mission-id", mission_id)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["loop"]["profile"]["profile_id"] == "safe-recorded"
    assert payload["usage"]["job_attempts"] == 0
    assert payload["jobs"][0]["status"] == "queued"


def test_cli_resume_advances_existing_mission_without_recreating_job(tmp_path):
    mission_id = _create_local_cut_mission(tmp_path)

    result = _run(tmp_path, "mission", "resume", "--mission-id", mission_id)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"]["decision"] == "completed"
    assert payload["recovered_job_ids"] == []
    assert len(payload["jobs"]) == 1


def test_cli_safe_profile_blocks_real_build_before_worker_execution(tmp_path):
    mission_id = _create_local_cut_mission(tmp_path, adapter_mode="real_build")

    result = _run(tmp_path, "mission", "advance", "--mission-id", mission_id)

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["decision"]["decision"] == "failed"
    assert payload["mission"]["final_outcome"]["code"] == "real_aedt_disabled"
    assert payload["jobs"][0]["status"] == "queued"


def test_cli_safe_profile_blocks_real_solve_before_process_launch(
    tmp_path,
):
    mission_id = _create_real_solve_mission(tmp_path)

    result = _run(
        tmp_path,
        "mission",
        "advance",
        "--mission-id",
        mission_id,
        "--profile",
        "safe-recorded",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["mission"]["final_outcome"]["code"] == (
        "real_aedt_disabled"
    )
    assert payload["jobs"][0]["status"] == "queued"
    assert not (tmp_path / "harness").exists()
