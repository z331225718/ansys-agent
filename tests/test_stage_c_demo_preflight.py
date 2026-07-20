import json
import subprocess
import sys

from aedt_agent.demo.config import AedtConfig, DemoConfig, ExecutionConfig, PlannerConfig, ServerConfig
from aedt_agent.demo.preflight import run_stage_c_preflight


def _demo_config(**aedt_overrides):
    return DemoConfig(
        planner=PlannerConfig(),
        server=ServerConfig(),
        execution=ExecutionConfig(),
        aedt=AedtConfig(**aedt_overrides),
    )


def test_preflight_accepts_blank_aedt_roots_when_versioned_environment_exists(tmp_path):
    awp_root = tmp_path / "v261"
    ansysem_root = awp_root / "AnsysEM"
    ansysem_root.mkdir(parents=True)
    layout_file = tmp_path / "board.brd"
    stackup_xml = tmp_path / "stackup.xml"
    layout_file.write_text("", encoding="utf-8")
    stackup_xml.write_text("<stackup />", encoding="utf-8")

    result = run_stage_c_preflight(
        _demo_config(version="2026.1", ansysem_root="", awp_root=""),
        parameters={"layout_file": str(layout_file), "stackup_xml": str(stackup_xml)},
        environ={"AWP_ROOT261": str(awp_root), "ANSYSEM_ROOT261": str(ansysem_root), "PATH": ""},
    )

    assert result.ok is True
    assert result.checks_by_id["aedt_awp_root"].status == "passed"
    assert result.checks_by_id["aedt_ansysem_root"].status == "passed"
    assert result.checks_by_id["layout_file"].status == "passed"
    assert result.checks_by_id["stackup_xml"].status == "passed"


def test_preflight_fails_for_explicit_missing_paths(tmp_path):
    missing_ansysem = tmp_path / "missing" / "AnsysEM"
    missing_awp = tmp_path / "missing"
    missing_layout = tmp_path / "missing.brd"
    missing_stackup = tmp_path / "missing.xml"

    result = run_stage_c_preflight(
        _demo_config(version="2026.1", ansysem_root=str(missing_ansysem), awp_root=str(missing_awp)),
        parameters={"layout_file": str(missing_layout), "stackup_xml": str(missing_stackup)},
        environ={},
    )

    assert result.ok is False
    assert result.checks_by_id["aedt_awp_root"].status == "failed"
    assert result.checks_by_id["aedt_ansysem_root"].status == "failed"
    assert result.checks_by_id["layout_file"].status == "failed"
    assert result.checks_by_id["stackup_xml"].status == "failed"


def test_preflight_warns_for_missing_optional_cadence_environment_in_non_strict_mode():
    result = run_stage_c_preflight(
        _demo_config(version="2026.1", ansysem_root="", awp_root="", cadence_launcher=""),
        parameters={},
        environ={},
        strict=False,
    )

    assert result.ok is True
    assert result.checks_by_id["cadence_environment"].status == "warning"


def test_preflight_strict_mode_fails_warnings():
    result = run_stage_c_preflight(
        _demo_config(version="2026.1", ansysem_root="", awp_root="", cadence_launcher=""),
        parameters={},
        environ={},
        strict=True,
    )

    assert result.ok is False
    assert result.checks_by_id["cadence_environment"].status == "warning"


def test_preflight_cli_prints_json_and_returns_nonzero_on_failed_check(tmp_path):
    config = tmp_path / "demo_config.json"
    params = tmp_path / "params.json"
    config.write_text(
        json.dumps(
            {
                "planner": {},
                "server": {},
                "execution": {},
                "aedt": {"version": "2026.1", "ansysem_root": str(tmp_path / "missing" / "AnsysEM"), "awp_root": ""},
            }
        ),
        encoding="utf-8",
    )
    params.write_text(json.dumps({"layout_file": str(tmp_path / "missing.brd")}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_stage_c_demo_environment.py",
            "--config",
            str(config),
            "--local-config",
            str(tmp_path / "missing.local.json"),
            "--params",
            str(params),
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["ok"] is False
    assert any(check["id"] == "aedt_ansysem_root" and check["status"] == "failed" for check in payload["checks"])
