from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aedt_agent.mcp.types import ExecutionResult


class AuditLogger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event_type: str,
        session_id: str,
        node_id: str | None,
        inputs: dict[str, Any],
        result: ExecutionResult,
        state_before: dict[str, Any],
        state_after: dict[str, Any],
    ) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "session_id": session_id,
            "node_id": node_id,
            "inputs": inputs,
            "result": _result_to_dict(result),
            "state_before": state_before,
            "state_after": state_after,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _result_to_dict(result: ExecutionResult) -> dict[str, Any]:
    data = asdict(result)
    data["status"] = result.status.value
    return data
