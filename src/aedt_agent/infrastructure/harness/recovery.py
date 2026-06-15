from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from aedt_agent.infrastructure.harness.contracts import HarnessRequest, HarnessResult
from aedt_agent.infrastructure.harness.local_process import ProcessTreeController


class HarnessRecoveryClassification(StrEnum):
    COMPLETED = "completed"
    ACTIVE = "active"
    STALE = "stale"
    INTERRUPTED = "interrupted"
    INVALID = "invalid"


@dataclass(frozen=True)
class HarnessRecoveryRecord:
    classification: HarnessRecoveryClassification
    workspace: str
    mission_id: str = ""
    job_id: str = ""
    attempt_id: str = ""
    harness_run_id: str = ""
    pid: int | None = None
    heartbeat_age_seconds: float | None = None
    error: str = ""

    def to_json_dict(self) -> dict:
        return {
            "classification": self.classification.value,
            "workspace": self.workspace,
            "mission_id": self.mission_id,
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "harness_run_id": self.harness_run_id,
            "pid": self.pid,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "error": self.error,
        }


class HarnessRecoveryScanner:
    def __init__(
        self,
        root: Path | str,
        *,
        process_controller: ProcessTreeController | None = None,
        heartbeat_timeout_seconds: int = 30,
    ):
        self.root = Path(root).expanduser().resolve()
        self.process_controller = process_controller or ProcessTreeController()
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds

    def scan(self, mission_id: str | None = None) -> list[HarnessRecoveryRecord]:
        roots = [self.root / mission_id] if mission_id else _directories(self.root)
        attempts = [
            attempt
            for mission_root in roots
            if mission_root.is_dir()
            for job_root in _directories(mission_root)
            for attempt in _directories(job_root)
        ]
        return [self.inspect(path) for path in sorted(attempts)]

    def inspect(self, workspace: Path | str) -> HarnessRecoveryRecord:
        root = Path(workspace).resolve()
        try:
            request = HarnessRequest.from_json_dict(
                json.loads((root / "request.json").read_text(encoding="utf-8"))
            )
            if Path(request.workspace).resolve() != root:
                raise ValueError("request workspace identity mismatch")
            result_path = root / "result.json"
            if result_path.exists():
                result = HarnessResult.from_json_dict(
                    json.loads(result_path.read_text(encoding="utf-8"))
                )
                result.assert_identity(request.harness_run_id, request.job_id)
                return self._record(
                    HarnessRecoveryClassification.COMPLETED,
                    root,
                    request,
                )
            heartbeat = json.loads(
                (root / "heartbeat.json").read_text(encoding="utf-8")
            )
            if heartbeat.get("protocol_version") != request.protocol_version:
                raise ValueError("heartbeat protocol_version mismatch")
            if heartbeat.get("harness_run_id") != request.harness_run_id:
                raise ValueError("heartbeat harness_run_id mismatch")
            if heartbeat.get("job_id") != request.job_id:
                raise ValueError("heartbeat job_id mismatch")
            pid = heartbeat.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                raise ValueError("heartbeat pid is invalid")
            updated_at = datetime.fromisoformat(
                str(heartbeat["updated_at"]).replace("Z", "+00:00")
            )
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=UTC)
            age = max((datetime.now(UTC) - updated_at.astimezone(UTC)).total_seconds(), 0.0)
            alive = self.process_controller.is_alive(pid)
            if not alive:
                classification = HarnessRecoveryClassification.INTERRUPTED
            elif age > self.heartbeat_timeout_seconds:
                classification = HarnessRecoveryClassification.STALE
            else:
                classification = HarnessRecoveryClassification.ACTIVE
            return self._record(
                classification,
                root,
                request,
                pid=pid,
                heartbeat_age_seconds=age,
            )
        except Exception as exc:
            return HarnessRecoveryRecord(
                classification=HarnessRecoveryClassification.INVALID,
                workspace=str(root),
                error=str(exc),
            )

    @staticmethod
    def _record(
        classification: HarnessRecoveryClassification,
        root: Path,
        request: HarnessRequest,
        *,
        pid: int | None = None,
        heartbeat_age_seconds: float | None = None,
    ) -> HarnessRecoveryRecord:
        return HarnessRecoveryRecord(
            classification=classification,
            workspace=str(root),
            mission_id=request.mission_id,
            job_id=request.job_id,
            attempt_id=request.attempt_id,
            harness_run_id=request.harness_run_id,
            pid=pid,
            heartbeat_age_seconds=heartbeat_age_seconds,
        )


def _directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [path for path in root.iterdir() if path.is_dir()]
