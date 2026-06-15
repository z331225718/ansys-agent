from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_BASE_ENV_NAMES = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "PYTHONPATH",
)
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class HarnessWorkspaceError(ValueError):
    """Raised when a harness workspace or environment policy is unsafe."""


@dataclass(frozen=True)
class HarnessWorkspace:
    root: Path
    request_path: Path
    result_path: Path
    heartbeat_path: Path
    stdout_path: Path
    stderr_path: Path
    artifacts_dir: Path

    def protocol_artifacts(self) -> list[str]:
        return [
            str(self.request_path),
            str(self.stdout_path),
            str(self.stderr_path),
            str(self.result_path),
        ]


class HarnessWorkspacePolicy:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()

    def create_attempt(
        self,
        mission_id: str,
        job_id: str,
        attempt_id: str,
    ) -> HarnessWorkspace:
        parts = [_validate_segment(value) for value in (mission_id, job_id, attempt_id)]
        attempt_root = self.root.joinpath(*parts).resolve()
        if not attempt_root.is_relative_to(self.root):
            raise HarnessWorkspaceError("attempt workspace escapes harness root")
        try:
            attempt_root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise HarnessWorkspaceError(f"attempt workspace already exists: {attempt_root}") from exc
        artifacts_dir = attempt_root / "artifacts"
        artifacts_dir.mkdir()
        return HarnessWorkspace(
            root=attempt_root,
            request_path=attempt_root / "request.json",
            result_path=attempt_root / "result.json",
            heartbeat_path=attempt_root / "heartbeat.json",
            stdout_path=attempt_root / "stdout.log",
            stderr_path=attempt_root / "stderr.log",
            artifacts_dir=artifacts_dir,
        )


def build_child_environment(
    allowed_names: list[str] | tuple[str, ...],
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    requested = [*_BASE_ENV_NAMES, *allowed_names]
    output: dict[str, str] = {}
    for name in requested:
        if not isinstance(name, str) or not _ENV_NAME_PATTERN.fullmatch(name):
            raise HarnessWorkspaceError(f"invalid environment variable name: {name!r}")
        if name in source:
            output[name] = str(source[name])
    return output


def _validate_segment(value: object) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise HarnessWorkspaceError(f"unsafe path segment: {value!r}")
    if "/" in value or "\\" in value or Path(value).is_absolute():
        raise HarnessWorkspaceError(f"unsafe path segment: {value!r}")
    return value
