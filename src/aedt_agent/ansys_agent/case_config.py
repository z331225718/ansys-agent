from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aedt_agent.agent.loop_runner import MIN_POLL_INTERVAL_SECONDS


class AnsysAgentCaseError(ValueError):
    """Raised when an ansys-agent case config is malformed or unsafe."""


@dataclass(frozen=True)
class AnsysAgentCase:
    case_id: str
    db_path: Path
    loop_config: Path
    execution_profile: Path
    worker_id: str = "ansys-agent"
    max_workers: int = 1
    poll_interval_seconds: int = 30
    check_paths: bool = True
    allow_ssh_remote: bool = False
    graph_run_id: str = ""
    mission_id: str = ""
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8766
    source_path: Path | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "db_path": str(self.db_path),
            "loop_config": str(self.loop_config),
            "execution_profile": str(self.execution_profile),
            "worker_id": self.worker_id,
            "max_workers": self.max_workers,
            "poll_interval_seconds": self.poll_interval_seconds,
            "check_paths": self.check_paths,
            "allow_ssh_remote": self.allow_ssh_remote,
            "graph_run_id": self.graph_run_id,
            "mission_id": self.mission_id,
            "dashboard": {
                "host": self.dashboard_host,
                "port": self.dashboard_port,
            },
            "source_path": "" if self.source_path is None else str(self.source_path),
        }


def load_case_config(
    path: str | Path,
    *,
    cwd: Path | None = None,
    no_check_paths: bool | None = None,
) -> AnsysAgentCase:
    case_path = Path(path)
    cwd = cwd or Path.cwd()
    if not case_path.is_absolute():
        case_path = (cwd / case_path).resolve(strict=False)
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AnsysAgentCaseError(f"{case_path} must contain a JSON object")

    case_id = _required_text(payload, "case_id")
    db_path = _resolve_path(_required_text(payload, "db_path"), cwd=cwd, base=case_path.parent)
    loop_config = _resolve_path(_required_text(payload, "loop_config"), cwd=cwd, base=case_path.parent)
    execution_profile = _resolve_path(
        _required_text(payload, "execution_profile"),
        cwd=cwd,
        base=case_path.parent,
    )
    dashboard = payload.get("dashboard") if isinstance(payload.get("dashboard"), dict) else {}
    check_paths = bool(payload.get("check_paths", True))
    if no_check_paths is not None:
        check_paths = not no_check_paths

    config = AnsysAgentCase(
        case_id=case_id,
        db_path=db_path,
        loop_config=loop_config,
        execution_profile=execution_profile,
        worker_id=str(payload.get("worker_id") or "ansys-agent"),
        max_workers=_positive_int(payload.get("max_workers", 1), "max_workers"),
        poll_interval_seconds=_positive_int(
            payload.get("poll_interval_seconds", 30),
            "poll_interval_seconds",
        ),
        check_paths=check_paths,
        allow_ssh_remote=bool(payload.get("allow_ssh_remote", False)),
        graph_run_id=str(payload.get("graph_run_id") or ""),
        mission_id=str(payload.get("mission_id") or ""),
        dashboard_host=str(dashboard.get("host") or "127.0.0.1"),
        dashboard_port=_positive_int(dashboard.get("port", 8766), "dashboard.port"),
        source_path=case_path,
    )
    _validate_case(config)
    return config


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise AnsysAgentCaseError(f"{key} is required")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AnsysAgentCaseError(f"{field_name} must be a positive integer") from exc
    if number <= 0:
        raise AnsysAgentCaseError(f"{field_name} must be a positive integer")
    return number


def _resolve_path(value: str, *, cwd: Path, base: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    cwd_candidate = (cwd / path).resolve(strict=False)
    if path.parts and path.parts[0] in {"config", "docs", "src", "tests"}:
        return cwd_candidate
    if cwd_candidate.exists():
        return cwd_candidate
    base_candidate = (base / path).resolve(strict=False)
    if base_candidate.exists():
        return base_candidate
    return cwd_candidate


def _validate_case(config: AnsysAgentCase) -> None:
    if config.poll_interval_seconds < MIN_POLL_INTERVAL_SECONDS:
        raise AnsysAgentCaseError(
            "poll_interval_seconds must be at least "
            f"{MIN_POLL_INTERVAL_SECONDS}"
        )
    if config.max_workers != 1:
        raise AnsysAgentCaseError("ansys-agent reviewed loop requires max_workers=1")
    if config.worker_id.strip() == "":
        raise AnsysAgentCaseError("worker_id is required")
