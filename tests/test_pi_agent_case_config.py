from __future__ import annotations

import json
from pathlib import Path

from aedt_agent.pi_agent import PiAgentSupervisor, load_case_config
from aedt_agent.pi_agent.initializer import initialize_local_case


def test_example_case_defaults_to_local_real_profile_without_path_checks():
    case = load_case_config(
        "config/cases/reviewed_brd.example.json",
        no_check_paths=True,
    )

    assert case.case_id == "reviewed-brd-s19"
    assert case.execution_profile.name == "local_real_aedt.example.json"
    assert case.loop_config.name == "reviewed_brd_remote.example.json"
    assert case.max_workers == 1
    assert case.poll_interval_seconds == 30
    assert case.check_paths is False
    assert case.allow_ssh_remote is False


def test_pi_agent_preflight_example_passes_without_machine_paths():
    case = load_case_config(
        "config/cases/reviewed_brd.example.json",
        no_check_paths=True,
    )

    report = PiAgentSupervisor(case).preflight()

    assert report["status"] == "passed"
    check_status = {item["id"]: item["status"] for item in report["checks"]}
    assert check_status["touchstone_is_s4p"] == "passed"
    assert check_status["tdr_diff1"] == "passed"
    assert check_status["profile_local_cli"] == "passed"


def test_pi_agent_preflight_rejects_ssh_profile_by_default(tmp_path: Path):
    case_file = tmp_path / "case.json"
    case_file.write_text(
        json.dumps(
            {
                "case_id": "ssh-blocked",
                "db_path": str(tmp_path / "missions.db"),
                "loop_config": "config/optimization_loops/reviewed_brd_remote.example.json",
                "execution_profile": "config/execution_profiles/ssh_remote.example.json",
                "worker_id": "pi-agent",
                "max_workers": 1,
                "poll_interval_seconds": 30,
                "check_paths": False,
            }
        ),
        encoding="utf-8",
    )
    case = load_case_config(case_file)

    report = PiAgentSupervisor(case).preflight()

    assert report["status"] == "failed"
    assert "profile_local_cli" in report["failed_checks"]


def test_pi_agent_init_creates_local_files_and_rewrites_case(tmp_path: Path):
    profile = tmp_path / "local_real_aedt.example.json"
    profile.write_text(
        Path("config/execution_profiles/local_real_aedt.example.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    loop = tmp_path / "reviewed_brd_remote.example.json"
    loop.write_text(
        Path("config/optimization_loops/reviewed_brd_remote.example.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    case_file = tmp_path / "reviewed_brd.example.json"
    case_file.write_text(
        json.dumps(
            {
                "case_id": "init-case",
                "db_path": str(tmp_path / "missions.db"),
                "loop_config": str(loop),
                "execution_profile": str(profile),
                "max_workers": 1,
                "poll_interval_seconds": 30,
                "check_paths": False,
            }
        ),
        encoding="utf-8",
    )
    case = load_case_config(case_file)

    report = initialize_local_case(case)

    targets = {Path(item["target"]).name: item["status"] for item in report["files"]}
    assert targets["reviewed_brd_remote.local.json"] in {"written", "copied"}
    assert targets["local_real_aedt.local.json"] in {"written", "copied"}
    assert targets["reviewed_brd.local.json"] == "written"
    local_case = json.loads((tmp_path / "reviewed_brd.local.json").read_text(encoding="utf-8"))
    assert local_case["loop_config"].endswith("reviewed_brd_remote.local.json")
    assert local_case["execution_profile"].endswith("local_real_aedt.local.json")
    assert local_case["check_paths"] is True
