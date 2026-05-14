from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable
from uuid import uuid4

from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus, SessionRef


class ExecutionQueue:
    def __init__(self, timeout_seconds: float = 120.0):
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def submit_callable(
        self,
        session: SessionRef,
        fn: Callable[[], dict[str, Any] | None],
        node_id: str | None = None,
    ) -> ExecutionResult:
        transaction_id = f"txn-{uuid4().hex}"
        with self._lock:
            started_at = time.monotonic()
            try:
                output = fn() or {}
            except Exception as exc:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    transaction_id=transaction_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    traceback=traceback.format_exc(),
                    elapsed_seconds=time.monotonic() - started_at,
                )
            elapsed = time.monotonic() - started_at
            if elapsed > self.timeout_seconds:
                return ExecutionResult(
                    status=ExecutionStatus.TIMEOUT,
                    transaction_id=transaction_id,
                    output=output,
                    error_type="Timeout",
                    error_message=f"Execution exceeded {self.timeout_seconds} seconds",
                    elapsed_seconds=elapsed,
                )
            output.setdefault("session_id", session.session_id)
            if node_id:
                output.setdefault("node_id", node_id)
            return ExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                transaction_id=transaction_id,
                output=output,
                elapsed_seconds=elapsed,
            )
