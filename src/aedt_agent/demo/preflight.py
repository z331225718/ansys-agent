from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aedt_agent.demo.config import DemoConfig


@dataclass(frozen=True)
class PreflightCheck:
    id: str
    status: str
    message: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status, "message": self.message, "detail": dict(self.detail)}


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    checks: list[PreflightCheck]
    summary: dict[str, int]

    @property
    def checks_by_id(self) -> dict[str, PreflightCheck]:
        return {check.id: check for check in self.checks}

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "summary": dict(self.summary), "checks": [check.to_dict() for check in self.checks]}


def run_stage_c_preflight(
    config: DemoConfig,
    *,
    parameters: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    strict: bool = False,
) -> PreflightResult:
    env = dict(os.environ if environ is None else environ)
    params = dict(parameters or {})
    checks = [
        _check_python_package(),
        _check_awp_root(config, env),
        _check_ansysem_root(config, env),
        _check_cadence(config, env),
        _check_layout_file(params),
        _check_stackup_xml(params),
    ]
    summary = {
        "passed": sum(1 for check in checks if check.status == "passed"),
        "warning": sum(1 for check in checks if check.status == "warning"),
        "failed": sum(1 for check in checks if check.status == "failed"),
        "skipped": sum(1 for check in checks if check.status == "skipped"),
    }
    ok = summary["failed"] == 0 and (not strict or summary["warning"] == 0)
    return PreflightResult(ok=ok, checks=checks, summary=summary)


def _check_python_package() -> PreflightCheck:
    for module_name in ("ansys.aedt.core", "pyaedt"):
        if importlib.util.find_spec(module_name) is not None:
            return PreflightCheck("pyaedt_package", "passed", f"Python package is importable: {module_name}", {"module": module_name})
    return PreflightCheck(
        "pyaedt_package",
        "warning",
        "PyAEDT package was not found in this Python environment.",
        {"expected": "ansys.aedt.core or pyaedt"},
    )


def _check_awp_root(config: DemoConfig, env: Mapping[str, str]) -> PreflightCheck:
    suffix = _version_suffix(config.aedt.version)
    return _check_root(
        check_id="aedt_awp_root",
        label=f"AWP_ROOT{suffix}",
        explicit_value=config.aedt.awp_root,
        env_value=env.get(f"AWP_ROOT{suffix}", ""),
        optional_message="AWP root is not configured. PyAEDT may still discover AEDT from the system installation.",
    )


def _check_ansysem_root(config: DemoConfig, env: Mapping[str, str]) -> PreflightCheck:
    suffix = _version_suffix(config.aedt.version)
    return _check_root(
        check_id="aedt_ansysem_root",
        label=f"ANSYSEM_ROOT{suffix}",
        explicit_value=config.aedt.ansysem_root,
        env_value=env.get(f"ANSYSEM_ROOT{suffix}", ""),
        optional_message="ANSYSEM root is not configured. PyAEDT may still discover AEDT from PATH or the system installation.",
    )


def _check_root(check_id: str, label: str, explicit_value: str, env_value: str, optional_message: str) -> PreflightCheck:
    if explicit_value:
        path = Path(explicit_value).expanduser()
        if path.exists():
            return PreflightCheck(check_id, "passed", f"Configured {label} exists.", {"source": "config", "path": str(path)})
        return PreflightCheck(check_id, "failed", f"Configured {label} does not exist.", {"source": "config", "path": str(path)})
    if env_value:
        path = Path(env_value).expanduser()
        if path.exists():
            return PreflightCheck(check_id, "passed", f"Environment {label} exists.", {"source": "environment", "path": str(path)})
        return PreflightCheck(check_id, "failed", f"Environment {label} points to a missing path.", {"source": "environment", "path": str(path)})
    return PreflightCheck(check_id, "warning", optional_message, {"source": "autodiscovery"})


def _check_cadence(config: DemoConfig, env: Mapping[str, str]) -> PreflightCheck:
    if config.aedt.cadence_launcher:
        launcher = Path(config.aedt.cadence_launcher).expanduser()
        if not launcher.exists():
            return PreflightCheck("cadence_environment", "failed", "Configured Cadence launcher does not exist.", {"path": str(launcher)})
        text = launcher.read_text(encoding="utf-8", errors="replace")
        has_cdsroot = "CDSROOT" in text or bool(env.get("CDSROOT"))
        if has_cdsroot:
            return PreflightCheck("cadence_environment", "passed", "Cadence launcher is present and can provide CDSROOT.", {"path": str(launcher)})
        return PreflightCheck("cadence_environment", "failed", "Cadence launcher does not define CDSROOT and CDSROOT is not set.", {"path": str(launcher)})
    if env.get("CDSROOT"):
        return PreflightCheck("cadence_environment", "passed", "CDSROOT is set in the environment.", {"source": "environment"})
    return PreflightCheck(
        "cadence_environment",
        "warning",
        "Cadence launcher/CDSROOT is not configured. BRD import may fail if the machine relies on Cadence extracta.",
        {"source": "not_configured"},
    )


def _check_layout_file(parameters: Mapping[str, Any]) -> PreflightCheck:
    value = str(parameters.get("layout_file") or "")
    if not value:
        return PreflightCheck("layout_file", "skipped", "No layout_file parameter was provided.", {})
    path = Path(value).expanduser()
    if path.exists():
        return PreflightCheck("layout_file", "passed", "Layout file exists.", {"path": str(path)})
    return PreflightCheck("layout_file", "failed", "Layout file does not exist.", {"path": str(path)})


def _check_stackup_xml(parameters: Mapping[str, Any]) -> PreflightCheck:
    value = str(parameters.get("stackup_xml") or "")
    if not value:
        layout_value = str(parameters.get("layout_file") or "")
        if layout_value:
            layout_path = Path(layout_value).expanduser()
            discovered = sorted(layout_path.parent.glob("*.xml")) if layout_path.parent.exists() else []
            if discovered:
                return PreflightCheck("stackup_xml", "passed", "Stackup XML was found next to the layout file.", {"path": str(discovered[0])})
        return PreflightCheck("stackup_xml", "warning", "No stackup_xml parameter was provided.", {})
    path = Path(value).expanduser()
    if path.exists():
        return PreflightCheck("stackup_xml", "passed", "Stackup XML exists.", {"path": str(path)})
    return PreflightCheck("stackup_xml", "failed", "Stackup XML does not exist.", {"path": str(path)})


def _version_suffix(version: str) -> str:
    parts = version.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]) % 100}{int(parts[1])}"
    digits = "".join(char for char in version if char.isdigit())
    return digits[-3:] if len(digits) >= 3 else digits
